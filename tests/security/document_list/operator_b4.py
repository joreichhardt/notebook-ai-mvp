"""One-shot kube1 B4 operator; only run after Main reviews full SHA and releases slot.

python3 operator_b4.py preflight FULL_SHA | run FULL_SHA
Private immutable git archive at BASE/FULL_SHA; no retries or general DB diagnosis.
"""

import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

BASE = Path("/home/jre/notebook-tests/b4")
PROJECT = "notebook-test-b4"
RELATIVE = Path("tests/security/document_list")
EXPECTED = {"postgres": 7, "focused": 50}
ERRORS = {
    "Error",
    "RuntimeError",
    "TimeoutExpired",
    "PermissionError",
    "FileExistsError",
    "FileNotFoundError",
    "ValueError",
    "AssertionError",
    "Failed",
    "Skipped",
    "OperationalError",
    "ProgrammingError",
    "IntegrityError",
    "HTTPException",
}


def error_kind(error):
    name = type.__getattribute__(type(error), "__name__")
    return name if type(name) is str and name in ERRORS else "Error"


def command(args, env=None, timeout=30):
    if env is None:
        env = {
            key: value
            for key, value in os.environ.items()
            if key in ("PATH", "HOME", "XDG_RUNTIME_DIR")
        }
    return subprocess.run(
        args,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )


def namespace():
    counts = []
    for kind, flags in (("ps", ["-aq"]), ("network", ["ls", "-q"]), ("volume", ["ls", "-q"])):
        result = command(
            ["docker", kind, *flags, "--filter", f"label=com.docker.compose.project={PROJECT}"]
        )
        if result.returncode:
            raise RuntimeError("namespace-check")
        counts.append(len(result.stdout.splitlines()))
    return counts


def private_directory(path):
    return (
        not path.is_symlink()
        and path.is_dir()
        and path.resolve() == path
        and path.stat().st_uid == os.getuid()
        and stat.S_IMODE(path.stat().st_mode) == 0o700
    )


def preflight():
    if socket.gethostname().split(".")[0] != "kube1" or os.getuid() == 0:
        raise RuntimeError("wrong-host-or-user")
    if not private_directory(BASE):
        raise RuntimeError("unsafe-assigned-parent")
    # CLI availability checked without dumping host info/config or credentials.
    for args in (
        ["docker", "version", "--format", "{{.Server.Version}}"],
        ["docker", "compose", "version", "--short"],
    ):
        if command(args).returncode:
            raise RuntimeError("cli-unavailable")
    endpoint = command(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"])
    if endpoint.returncode or endpoint.stdout.strip() not in {
        b"unix:///var/run/docker.sock",
        f"unix:///run/user/{os.getuid()}/docker.sock".encode(),
    }:
        raise RuntimeError("nonlocal-docker-endpoint")
    counts = namespace()
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    available = int(memory["MemAvailable"].split()[0]) * 1024
    free = shutil.disk_usage(BASE.parent).free
    load = os.getloadavg()[0]
    if counts != [0, 0, 0] or available < 3 * 1024**3 or free < 3 * 1024**3 or load > 6:
        raise RuntimeError("capacity-or-namespace")
    return {
        "stage": "preflight",
        "ok": True,
        "namespace": counts,
        "available_mib": available // 1024**2,
        "free_mib": free // 1024**2,
        "load1": round(load, 2),
    }


def safe_report(report):
    """Closed report schema: no diagnostic strings, DSNs, headers or raw node IDs."""
    if type(report) is not dict or type(report.get("ok")) is not bool:
        raise RuntimeError("invalid-report")
    phase = report.get("stage")
    if phase not in {
        "prepare",
        "dependencies",
        "migrate",
        "role",
        "postgres",
        "focused",
        "runner-credentials",
    }:
        raise RuntimeError("invalid-stage")
    if set(report) - {
        "stage",
        "ok",
        "error_type",
        "passed",
        "failed",
        "skipped",
        "expected",
        "cases",
    }:
        raise RuntimeError("unknown-report-field")
    if "error_type" in report and report["error_type"] not in ERRORS:
        raise RuntimeError("invalid-error-type")
    if phase not in EXPECTED:
        if set(report) - {"stage", "ok", "error_type"}:
            raise RuntimeError("unexpected-counts")
        return report
    if not report["ok"] and set(report) == {"stage", "ok", "error_type"}:
        return report
    expected = EXPECTED[phase]
    for key in ("passed", "failed", "skipped", "expected"):
        if type(report.get(key)) is not int or not 0 <= report[key] <= expected * 3:
            raise RuntimeError("invalid-counts")
    cases = report.get("cases")
    if report["expected"] != expected or type(cases) is not list or len(cases) > expected * 3:
        raise RuntimeError("invalid-cases")
    seen = set()
    for row in cases:
        if type(row) is not dict or set(row) - {"test", "phase", "outcome", "error_type"}:
            raise RuntimeError("invalid-case")
        if row.get("test") not in {f"{phase}-{n:02}" for n in range(1, expected + 1)}:
            raise RuntimeError("invalid-case-id")
        if row.get("phase") not in ("setup", "call", "teardown") or row.get("outcome") not in (
            "passed",
            "failed",
            "skipped",
        ):
            raise RuntimeError("invalid-case-outcome")
        if "error_type" in row and row["error_type"] not in ERRORS:
            raise RuntimeError("invalid-case-error")
        key = (row["test"], row["phase"])
        if key in seen:
            raise RuntimeError("duplicate-case")
        seen.add(key)
    passed = sum(r["phase"] == "call" and r["outcome"] == "passed" for r in cases)
    failed = sum(r["outcome"] == "failed" for r in cases)
    skipped = sum(r["outcome"] == "skipped" for r in cases)
    if (passed, failed, skipped) != (report["passed"], report["failed"], report["skipped"]):
        raise RuntimeError("inconsistent-counts")
    if report["ok"] and (passed != expected or failed or skipped):
        raise RuntimeError("incomplete-success")
    return report


def run(sha):
    initial = preflight()
    root = BASE / sha
    if (
        not re.fullmatch(r"[0-9a-f]{40}", sha)
        or Path(__file__).resolve() != root / RELATIVE / "operator_b4.py"
    ):
        raise RuntimeError("not-immutable-snapshot")
    if not private_directory(root) or not (root / "uv.lock").is_file():
        raise RuntimeError("snapshot-invalid")
    runtime = BASE / (".runtime-b4-" + sha)
    runtime.mkdir(mode=0o700)  # exclusive, never overwrite/reuse evidence
    descriptor = os.open(runtime / "evidence.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    bootstrap = runtime / "bootstrap"
    env = {
        key: value
        for key, value in os.environ.items()
        if key in ("PATH", "HOME", "XDG_RUNTIME_DIR")
    }
    env.update(
        {
            "B4_SHA": sha,
            "B4_DATABASE": "notebook_b4_" + sha[:12],
            "B4_SNAPSHOT": str(root),
            "B4_BOOTSTRAP_FILE": str(bootstrap),
            "B4_UID": str(os.getuid()),
            "B4_GID": str(os.getgid()),
        }
    )
    compose = [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "-p",
        PROJECT,
        "-f",
        str(root / RELATIVE / "compose.b4.yaml"),
    ]
    started = success = cleanup = removed = False
    stage = "prepare"
    with os.fdopen(descriptor, "w") as evidence:

        def record(data):
            line = json.dumps({"time": datetime.now(UTC).isoformat(), **data})
            evidence.write(line + "\n")
            evidence.flush()
            print(line, flush=True)

        record({"stage": "snapshot", "sha": sha})
        record(initial)
        try:
            # Host parent0700; mount readable to postgres70 and host-UID runner.
            with os.fdopen(
                os.open(bootstrap, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
            ) as stream:
                stream.write(secrets.token_hex(24))
                stream.flush()
                os.fchmod(stream.fileno(), 0o444)
            for stage, args, timeout in (
                ("compose-config", [*compose, "config", "--quiet"], 30),
                ("pull", [*compose, "pull"], 600),
            ):
                if command(args, env, timeout).returncode:
                    raise RuntimeError(stage)
            stage = "database-start"
            started = True
            if command(
                [*compose, "up", "-d", "--wait", "--wait-timeout", "90", "db"], env, 120
            ).returncode:
                raise RuntimeError(stage)
            stage = "runner"
            result = command([*compose, "run", "--rm", "-T", "--no-deps", "runner"], env, 1800)
            if len(result.stdout) > 131072:
                raise RuntimeError("report-too-large")
            reports = []
            for line in result.stdout.splitlines():
                if line.startswith(b"B4_RESULT "):
                    report = safe_report(json.loads(line[len(b"B4_RESULT ") :]))
                    reports.append(report)
                    record(report)
            success = (
                result.returncode == 0
                and [r["stage"] for r in reports]
                == ["migrate", "role", "postgres", "focused", "runner-credentials"]
                and all(r["ok"] for r in reports)
            )
            record({"stage": "run-exit", "ok": success, "exit": result.returncode})
        except BaseException as error:  # noqa: BLE001 — enum only, no exception text
            record({"stage": stage, "ok": False, "error_type": error_kind(error)})
        finally:
            try:
                down = (
                    command(
                        [*compose, "down", "--volumes", "--remove-orphans", "--timeout", "15"],
                        env,
                        120,
                    )
                    if started
                    else None
                )
                counts = namespace()
                cleanup = (down is None or down.returncode == 0) and counts == [0, 0, 0]
                record({"stage": "cleanup", "ok": cleanup, "namespace": counts})
            except BaseException as error:  # noqa: BLE001 — no global prune or broad stop
                record({"stage": "cleanup", "ok": False, "error_type": error_kind(error)})
            finally:
                bootstrap.unlink(missing_ok=True)
                removed = not bootstrap.exists()
                record({"stage": "credential-removal", "ok": removed})
    return 0 if success and cleanup and removed else 1


def interrupted(signum, frame):
    raise RuntimeError("operator-interrupted")


def main():
    if (
        len(sys.argv) != 3
        or sys.argv[1] not in ("preflight", "run")
        or not re.fullmatch(r"[0-9a-f]{40}", sys.argv[2])
    ):
        return 2
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        if sys.argv[1] == "preflight":
            print(json.dumps(preflight()), flush=True)
            return 0
        return run(sys.argv[2])
    except BaseException as error:  # noqa: BLE001 — no raw output, one shot
        print(
            json.dumps({"stage": "operator", "ok": False, "error_type": error_kind(error)}),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
