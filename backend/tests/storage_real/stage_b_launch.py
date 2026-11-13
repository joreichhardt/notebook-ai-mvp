"""kube1-only immutable Stage B operator; raw child output is never persisted."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import selectors
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

if __package__:
    from .diagnostics import selected_state, supervise
    from .launch import STATE_FORMAT
else:
    from diagnostics import selected_state, supervise
    from launch import STATE_FORMAT

PROJECT = "notebook-test-a5-storage"
CASES = (
    "prepare",
    "post_storage_acl",
    "private_http",
    "owner_upload",
    "editor_upload",
    "idempotency",
    "application_denials",
    "jwt_denials",
    "cleanup",
    "complete",
)
STATES = (
    "unknown",
    "08006",
    "28P01",
    "23503",
    "23505",
    "3F000",
    "42501",
    "42601",
    "42703",
    "42704",
    "42710",
    "42883",
    "42P01",
    "42P07",
    "55P03",
    "57014",
)
TYPES = (
    "unknown",
    "assertion",
    "value",
    "os",
    "alembic_command",
    "sqlalchemy_programming",
    "sqlalchemy_operational",
    "sqlalchemy_integrity",
    "sqlalchemy_error",
    "driver_programming",
    "driver_operational",
    "driver_integrity",
)


def safe_line(line):
    if type(line) is not str or len(line) > 1024:
        return None
    if line.startswith("STAGE_B_BEGIN="):
        value = line.split("=", 1)[1]
        return {"begin": value} if value in CASES else None
    if line.startswith("STAGE_B_JWT="):
        value = line.split("=", 1)[1]
        return (
            {"jwt_case": value}
            if value in ("missing", "invalid", "anon", "authenticated_a", "authenticated_b")
            else None
        )
    if line.startswith("STAGE_B_HTTP="):
        try:
            data = json.loads(line.split("=", 1)[1])
            if (
                type(data) is dict
                and set(data) == {"method", "status"}
                and type(data["status"]) is int
                and 100 <= data["status"] <= 599
                and data["method"] in ("GET", "POST", "DELETE")
            ):
                return {"http": data}
        except (ValueError, TypeError):
            pass
        return None
    if line.startswith("STAGE_B_INVENTORY="):
        try:
            data = json.loads(line.split("=", 1)[1])
            source = json.loads(Path(__file__).with_name("stage_b_source.json").read_text())
            expected = {
                "storage_table_owner": "supabase_storage_admin",
                "storage_schema_owner": "supabase_admin",
                "tables_rls": True,
                "owner_flags": [True, False, False, False, True],
                "migration_count": source["migration_count"],
                "migration_fingerprint": source["migration_ids_names_sha256"],
                "app_relations": 14,
                "app_functions": 26,
            }
            if (
                type(data) is dict
                and data == expected
                and all(type(v) is type(expected[k]) for k, v in data.items())
                and all(type(v) is bool for v in data["owner_flags"])
            ):
                return {"inventory": expected}
        except (ValueError, TypeError, KeyError):
            pass
        return None
    if line.startswith("STAGE_B_CASE="):
        value = line.split("=", 1)[1]
        return {"case": value} if value in CASES else None
    if line.startswith("STAGE_B_FAILURE="):
        try:
            data = json.loads(line.split("=", 1)[1])
            if (
                type(data) is dict
                and set(data) == {"stage", "type", "sqlstate"}
                and all(type(v) is str for v in data.values())
                and data["stage"] in CASES
                and data["type"] in TYPES
                and data["sqlstate"] in STATES
            ):
                return {"failure": data}
        except (ValueError, TypeError):
            pass
    if re.fullmatch(
        r"NATIVE_WEBHOOK_DEPENDENCY_PRESENT=1|RED_UPSTREAM_DEFAULTS_EXPOSE_SYNTHETIC_PROBE=3|GREEN_ACL_RELATIONS=14 FUNCTIONS=26 ACTORS=3|MIGRATOR_DISABLED_AND_CREDENTIAL_REMOVED=1",
        line,
    ):
        return {"preparation": line}
    return None


def fixed_run(argv, env, seconds, emit):
    child = subprocess.Popen(
        argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True
    )
    selector = None
    deadline = time.monotonic() + seconds
    pending = b""
    total = 0
    failed = False
    try:
        selector = selectors.DefaultSelector()
        assert child.stdout is not None
        selector.register(child.stdout, selectors.EVENT_READ)
        eof = False
        while not eof:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("fixed_child_failure")
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fd, 4096)
                if not chunk:
                    eof = True
                    break
                total += len(chunk)
                if total > 1048576:
                    raise ValueError("fixed_child_failure")
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if len(line) > 1024:
                        raise ValueError("fixed_child_failure")
                    record = safe_line(line.decode("utf-8", errors="strict").rstrip("\r"))
                    if record is not None:
                        emit(record)
                if len(pending) > 1024:
                    raise ValueError("fixed_child_failure")
        # Quiet EOF retains the remaining deadline, as in the repaired native supervisor.
        return child.wait(timeout=max(0, deadline - time.monotonic()))
    except Exception:  # noqa: BLE001 -- do not expose arbitrary subprocess/decoder text
        failed = True
        raise ValueError("fixed_child_failure") from None
    finally:
        for resource in (selector, child.stdout):
            if resource is not None:
                try:
                    resource.close()
                except Exception:  # noqa: BLE001 -- cleanup must still reap owned child
                    failed = True
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            failed = True
        try:
            child.wait(timeout=0.3)
        except (OSError, subprocess.TimeoutExpired):
            failed = True
        if failed:
            raise ValueError("fixed_child_failure") from None


def quiet(argv, env, seconds):
    child = subprocess.Popen(
        argv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )
    try:
        return child.wait(timeout=seconds)
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=0.3)


def inventory():
    result = []
    for kind in ("container", "network", "volume"):
        args = [
            "docker",
            kind,
            "ls",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
        ]
        if kind == "container":
            args.insert(3, "-a")
        output = subprocess.run(args, capture_output=True, check=True, timeout=15)
        result.append(len(output.stdout.splitlines()))
    return result


def credentials(root):
    def put(directory, name, value):
        with open(
            root / directory / name, "x", opener=lambda path, flags: os.open(path, flags, 0o600)
        ) as stream:
            stream.write(value)

    for name in ("db", "storage", "runner"):
        (root / name).mkdir(mode=0o700)
    bootstrap = secrets.token_hex(32)
    owner = secrets.token_hex(32)
    signing = secrets.token_hex(32)
    put("db", "bootstrap", bootstrap)
    put("runner", "bootstrap", bootstrap)
    put("runner", "owner-password", owner)
    fixture = {
        k: str(uuid4())
        for k in (
            "owner",
            "editor",
            "viewer",
            "foreign",
            "revoked",
            "workspace",
            "notebook",
            "foreign_workspace",
            "foreign_notebook",
        )
    }
    put("runner", "fixture.json", json.dumps(fixture))

    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=")

    def jwt(role, subject=None):
        payload = {
            "role": role,
            "iat": int(time.time()),
            "exp": int(time.time()) + 1800,
            "iss": "a5-synthetic",
            "aud": "authenticated",
        }
        if subject is not None:
            payload["sub"] = subject
        value = (
            encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
            + b"."
            + encode(json.dumps(payload).encode())
        )
        return (
            value + b"." + encode(hmac.new(signing.encode(), value, hashlib.sha256).digest())
        ).decode()

    anon = jwt("anon")
    service = jwt("service_role")
    put("runner", "invalid.jwt", "synthetic-invalid-token")
    put("runner", "anon.jwt", anon)
    put("runner", "service.jwt", service)
    put("runner", "user-a.jwt", jwt("authenticated", fixture["owner"]))
    put("runner", "user-b.jwt", jwt("authenticated", fixture["foreign"]))
    put("storage", "database.pgpass", "db:5432:postgres:supabase_storage_admin:" + owner + "\n")
    put(
        "storage",
        "environment.json",
        json.dumps(
            {
                "DATABASE_URL": "postgresql://supabase_storage_admin@db:5432/postgres",
                "AUTH_JWT_SECRET": signing,
                "ANON_KEY": anon,
                "SERVICE_KEY": service,
            }
        ),
    )
    assert all(f.stat().st_mode & 0o777 == 0o600 for f in root.rglob("*") if f.is_file())


def main():
    root = Path.cwd().resolve()
    base = root / "backend/tests/storage_real"
    # A fresh full-SHA archive, never the accepted PhaseA snapshot or any existing runtime.
    if sys.argv[1:] != ["--run-stage-b"] or not re.fullmatch(
        r"/home/jre/notebook-tests/a5/storage-stage-b-[0-9a-f]{40}", str(root)
    ):
        return 2
    assert (
        os.getuid() != 0
        and root.stat().st_uid == os.getuid()
        and root.stat().st_mode & 0o777 == 0o700
    )
    assert inventory() == [0, 0, 0]
    version = subprocess.run(
        ["docker", "version", "--format", "{{.Client.Version}}"],
        capture_output=True,
        check=True,
        timeout=15,
    ).stdout.strip()
    assert version == b"29.1.3"
    available = int(
        next(
            line.split()[1]
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
    )
    assert available > 4194304 and shutil.disk_usage(root).free > 10 * 1024**3
    lock = json.loads((base / "lock.json").read_text())
    for name, digest in lock["native_init_sha256"].items():
        assert hashlib.sha256((base / name).read_bytes()).hexdigest() == digest
    os.umask(0o077)
    runtime = root / ".stage-b-runtime"
    runtime.mkdir(mode=0o700)
    secret = runtime / "credentials"
    secret.mkdir(mode=0o700)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(Path.home()),
        "A5_STAGE_B_ROOT": str(secret),
        "A5_PHASE_A_UID": str(os.getuid()),
        "A5_PHASE_A_GID": str(os.getgid()),
    }
    compose = [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "-p",
        PROJECT,
        "-f",
        str(base / "stage_b.compose.json"),
    ]
    result = 1
    stage = "preflight"
    seen = []
    with (runtime / "evidence.jsonl").open("x") as evidence:

        def emit(record):
            evidence.write(json.dumps(record, separators=(",", ":")) + "\n")
            evidence.flush()
            if "case" in record:
                seen.append(record["case"])

        def run(stage_name, args, seconds):
            code = quiet(compose + args, env, seconds)
            emit({"operation": stage_name, "exit": code})
            assert code == 0

        try:
            emit(
                {
                    "sha": root.name.rsplit("-", 1)[1],
                    "preflight_resources": [0, 0, 0],
                    "available_kib": available,
                    "cli": "29.1.3",
                }
            )
            stage = "build"
            run(stage, ["build", "runner"], 600)
            stage = "pull"
            run(stage, ["pull", "db", "storage", "gateway"], 300)
            credentials(secret)
            stage = "create"
            run(stage, ["create", "db"], 30)
            container = subprocess.run(
                compose + ["ps", "--all", "--quiet", "db"],
                env=env,
                capture_output=True,
                check=True,
                timeout=5,
            ).stdout.strip()
            assert re.fullmatch(rb"[a-f0-9]{64}", container)

            def state():
                r = subprocess.run(
                    [
                        "docker",
                        "inspect",
                        "--type",
                        "container",
                        "--format",
                        STATE_FORMAT,
                        container.decode(),
                    ],
                    capture_output=True,
                    timeout=2,
                    check=True,
                )
                return selected_state(r.stdout)

            stage = "native_start"
            outcome = supervise(
                ["docker", "start", "--attach", container.decode()],
                seconds=120,
                ready=lambda: "health_healthy" in state() and "running_true" in state(),
                detach=True,
                environment=env,
            )
            native = state()
            emit(
                {
                    "native_client_exit": outcome.exit_code,
                    "native_events": outcome.events,
                    "native_state": native,
                }
            )
            assert (
                "health_ready" in outcome.events
                and not set(outcome.events)
                & {"limit_time", "limit_bytes", "classifier_failure", "spawn_failure"}
                and "running_true" in native
            )
            stage = "prepare"
            code = fixed_run(
                compose
                + [
                    "run",
                    "--rm",
                    "-T",
                    "runner",
                    "python",
                    "backend/tests/storage_real/stage_b.py",
                    "prepare",
                ],
                env,
                120,
                emit,
            )
            emit({"operation": stage, "exit": code})
            assert code == 0 and "prepare" in seen
            stage = "storage_start"
            run(stage, ["up", "-d", "--wait", "--wait-timeout", "120", "storage", "gateway"], 130)
            stage = "verify"
            code = fixed_run(compose + ["run", "--rm", "-T", "runner"], env, 600, emit)
            emit({"operation": stage, "exit": code})
            assert code == 0 and seen == [
                "prepare",
                "post_storage_acl",
                "private_http",
                "owner_upload",
                "idempotency",
                "editor_upload",
                "idempotency",
                "application_denials",
                "jwt_denials",
                "cleanup",
                "complete",
            ]
            emit({"pre_cleanup_native": state()})
            result = 0
        except Exception:  # noqa: BLE001 -- fixed operation labels only, no command/error text
            emit({"operator_failure": stage})
            result = 1
        finally:
            try:
                cleanup = quiet(
                    compose + ["down", "--volumes", "--remove-orphans", "--timeout", "5"], env, 45
                )
            except Exception:  # noqa: BLE001 -- cleanup failure has one fixed numeric status
                cleanup = 2
            try:
                shutil.rmtree(secret)
                removed = True
            except OSError:
                removed = False
            try:
                counts = inventory()
            except Exception:  # noqa: BLE001 -- unknown inventory is not success
                counts = [-1, -1, -1]
            emit({"cleanup_exit": cleanup, "resources": counts, "credentials_removed": removed})
            if cleanup or counts != [0, 0, 0] or not removed:
                result = 2
    print("STAGE_B_OPERATOR_EXIT=" + str(result), flush=True)
    return result


if __name__ == "__main__":
    # No raw preflight exception fallback either.
    try:
        code = main()
    except Exception:  # noqa: BLE001 -- fixed process boundary
        code = 2
        print("STAGE_B_PREFLIGHT_FAILED=1", flush=True)
    raise SystemExit(code)
