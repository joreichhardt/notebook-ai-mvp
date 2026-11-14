"""LOCAL-UNRUN combined operator; supported entry is --run-combined SHA MANIFEST_SHA.

The full archive manifest is supplied separately, never generated/repaired here.
No operational imports, container/DB/network actions or secret reads at import time.
"""

import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

PROJECT = "notebook-test-b5-upload"
REL = Path("tests/security/upload_http")
A5_CASES = (
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
)
B5_STAGES = ("configuration", "principals", "setup", "closed", "denials", "owner", "editor", "done")
A5_SECONDS, B5_SECONDS, ACTIVE_SECONDS, OUTER_SECONDS = 600, 240, 2250, 2400


class Refused(RuntimeError):
    """No attached source values or underlying exception text."""


def need(value):
    if not value:
        raise Refused()


def unique(pairs):
    data = {}
    for key, value in pairs:
        need(key not in data)
        data[key] = value
    return data


def verify_source(root, digest):
    need(re.fullmatch("[0-9a-f]{64}", digest) is not None)
    need(not root.is_symlink() and stat.S_IMODE(root.stat().st_mode) == 0o700)
    need(root.stat().st_uid == os.getuid())
    manifest = root / ".source-manifest.json"
    need(not manifest.is_symlink() and stat.S_ISREG(manifest.lstat().st_mode))
    need(manifest.stat().st_uid == os.getuid() and stat.S_IMODE(manifest.stat().st_mode) == 0o600)
    raw = manifest.read_bytes()
    need(len(raw) <= 2**20 and hashlib.sha256(raw).hexdigest() == digest)
    data = json.loads(raw, object_pairs_hook=unique)
    need(type(data) is dict and 0 < len(data) <= 10000)
    for name, expected in data.items():
        if type(name) is not str or type(expected) is not str:
            raise Refused()
        path = Path(name)
        need(not path.is_absolute() and path.as_posix() == name and ".." not in path.parts)
        need(name != ".source-manifest.json" and ".b5-runtime" not in path.parts)
        need(re.fullmatch("[0-9a-f]{64}", expected) is not None)
        target = root / path
        need(not any(p.is_symlink() for p in (target, *target.parents)))
        mode = target.stat()
        need(stat.S_ISREG(mode.st_mode) and mode.st_uid == os.getuid())
        need(stat.S_IMODE(mode.st_mode) in (0o644, 0o755))
        need(hashlib.sha256(target.read_bytes()).hexdigest() == expected)
    actual = set()
    for target in root.rglob("*"):
        need(not target.is_symlink())
        need(target.is_file() or target.is_dir())
        if target.is_file():
            actual.add(target.relative_to(root).as_posix())
    need(actual == set(data) | {".source-manifest.json"})
    return len(data)


def accepted(root):
    # Only called AFTER full externally anchored archive verification.
    sys.path.insert(0, str(root))
    from backend.tests.storage_real import stage_b_launch

    return stage_b_launch


def verify_pins(root):
    manifest = json.loads(
        (root / REL / "source_manifest.b5.json").read_text(), object_pairs_hook=unique
    )
    need(set(manifest) == {"baseline", "accepted_files"})
    need(manifest["baseline"] == "1d821f136b08501bb1e75837a5c95a5a8a479d1c")
    for name, digest in manifest["accepted_files"].items():
        need(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest)


def inventory(env):
    counts = []
    for kind in ("container", "network", "volume"):
        command = [
            "docker",
            kind,
            "ls",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
        ]
        if kind == "container":
            command.insert(3, "-a")
        result = subprocess.run(command, env=env, capture_output=True, timeout=5, check=True)
        counts.append(len(result.stdout.splitlines()))
    return counts


def own_id(compose, env, service):
    need(service in ("runner", "db"))
    data = subprocess.run(
        compose + ["ps", "--all", "--quiet", service],
        env=env,
        capture_output=True,
        timeout=5,
        check=True,
    ).stdout.strip()
    need(re.fullmatch(rb"[a-f0-9]{64}", data) is not None)
    container = data.decode("ascii")
    check_owned(container, env, service)
    return container


def check_owned(container, env, service):
    need(re.fullmatch("[a-f0-9]{64}", container) is not None and service in ("runner", "db"))
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--type",
            "container",
            "--format",
            '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}',
            container,
        ],
        env=env,
        capture_output=True,
        timeout=5,
        check=True,
    )
    need(result.stdout.strip() == (PROJECT + "|" + service).encode())


def container_state(container, env, a5):
    result = subprocess.run(
        ["docker", "inspect", "--type", "container", "--format", a5.STATE_FORMAT, container],
        env=env,
        capture_output=True,
        timeout=5,
        check=True,
    )
    return a5.selected_state(result.stdout)


def remove_runner(container, env, quiet):
    # Exact ID, checked project AND service; never just kill the attach CLI.
    check_owned(container, env, "runner")
    need(quiet(["docker", "rm", "--force", container], env, 5) == 0)


def b5_record(line):
    for stage in B5_STAGES:
        if line == "B5_PACKET_STAGE=" + stage:
            return {"b5_stage": stage}
    if line == "B5_LIVE_CASE_PASSED=1":
        return {"b5_live_case_passed": 1}
    if line in ("B5_PACKET_FAILURE=live_case", "B5_PACKET_FAILURE=entrypoint"):
        return {"b5_failure": "live_case" if line.endswith("live_case") else "entrypoint"}
    return None


def stream(argv, env, seconds, emit):
    """Bounded memory-only byte filtering; no raw output/error persistence."""
    child = subprocess.Popen(
        argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True
    )
    deadline = time.monotonic() + seconds
    pending = b""
    total = 0
    try:
        if child.stdout is None:
            raise Refused()
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            eof = False
            while not eof:
                need(time.monotonic() < deadline)
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        eof = True
                        break
                    total += len(chunk)
                    need(total <= 1048576)
                    pending += chunk
                    need(len(pending) <= 8192)
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        need(len(line) <= 128)
                        record = b5_record(line.decode("ascii"))
                        need(record is not None)
                        emit(record)
            need(not pending)
            return child.wait(timeout=max(0.001, deadline - time.monotonic()))
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=0.3)
        if child.stdout is not None:
            child.stdout.close()


def runner_phase(compose, env, phase, seconds, a5, emit):
    need(phase in ("prepare", "a5", "b5"))
    need(seconds == {"prepare": 120, "a5": A5_SECONDS, "b5": B5_SECONDS}[phase])
    deadline = time.monotonic() + seconds
    environment = {**env, "B5_RUN_PHASE": phase}
    container = None
    try:
        emit({"runner_phase": phase, "runner_checkpoint": "create"})
        need(
            a5.quiet(
                compose + ["create", "--no-deps", "runner"], environment, min(30, seconds - 20)
            )
            == 0
        )
        emit({"runner_phase": phase, "runner_checkpoint": "identify"})
        container = own_id(compose, environment, "runner")
        budget = deadline - time.monotonic() - 21  # reserve exact-ID check+rm and reap
        need(budget > 0)
        execute = stream if phase == "b5" else a5.fixed_run
        emit({"runner_phase": phase, "runner_checkpoint": "attach"})
        code = execute(["docker", "start", "--attach", container], environment, budget, emit)
        emit({"runner_phase": phase, "runner_cli_zero": code == 0})
        need(code == 0)
        emit({"runner_phase": phase, "runner_checkpoint": "inspect"})
        state = set(container_state(container, environment, a5))
        emit({"runner_phase": phase, "runner_checkpoint": "validate"})
        state_ok = {"exit_0", "oom_false", "running_false", "engine_error_absent"} <= state
        emit({"runner_phase": phase, "runner_state_ok": state_ok})
        need(state_ok)
        emit({"operation": phase, "exit": 0})
    finally:
        if container is not None:
            try:
                emit({"runner_phase": phase, "runner_checkpoint": "remove"})
            finally:
                remove_runner(container, environment, a5.quiet)
        # If creation/ID acquisition failed, final project down handles the owned orphan.


def cleanup(compose, env, secret, quiet, emit):
    code, counts, removed = 2, [-1, -1, -1], False
    try:
        code = quiet(compose + ["down", "--volumes", "--remove-orphans", "--timeout", "5"], env, 45)
    except Exception:  # noqa: BLE001 — fixed failure metadata only
        code = 2
    try:
        if secret.exists():
            shutil.rmtree(secret)
        removed = not secret.exists()
    except OSError:
        pass
    try:
        counts = inventory(env)  # independent, not derived from `down` exit
    except Exception:  # noqa: BLE001 — unknown inventory is NOT success
        counts = [-1, -1, -1]
    emit(
        {
            "cleanup_exit": code if code in (0, 1, 2) else 2,
            "resources": counts,
            "credentials_removed": removed,
        }
    )
    return code == 0 and counts == [0, 0, 0] and removed


def execute(root, sha, digest):
    need(socket.gethostname() == "kube1" and os.getuid() != 0)
    need(str(root) == "/home/jre/notebook-tests/b5/upload-http-" + sha)
    need(re.fullmatch("[0-9a-f]{40}", sha) is not None)
    count = verify_source(root, digest)
    verify_pins(root)
    for name in ("webhooks.sql", "roles.sql", "jwt.sql"):
        need(stat.S_IMODE((root / "backend/tests/storage_real" / name).stat().st_mode) == 0o644)
    a5 = accepted(root)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/home/jre",
        "A5_PHASE_A_UID": str(os.getuid()),
        "A5_PHASE_A_GID": str(os.getgid()),
        "B5_RUN_PHASE": "prepare",
    }
    need(inventory(env) == [0, 0, 0])
    cli = subprocess.run(
        ["docker", "version", "--format", "{{.Client.Version}}"],
        env=env,
        capture_output=True,
        timeout=5,
        check=True,
    ).stdout.strip()
    need(cli == b"29.1.3")
    available = int(
        next(
            line.split()[1]
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
    )
    need(available > 4194304 and shutil.disk_usage(root).free > 10 * 1024**3)
    os.umask(0o077)
    runtime = root / ".b5-runtime"
    runtime.mkdir(mode=0o700)  # exclusive, never resume/replay/rewrite
    (runtime / "owner.pid").write_text(str(os.getpid()))
    secret = runtime / "credentials"
    secret.mkdir(mode=0o700)
    env["A5_STAGE_B_ROOT"] = str(secret)
    compose = [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "-p",
        PROJECT,
        "-f",
        str(root / REL / "compose.b5.json"),
    ]
    result, phase = 1, "build"
    cases, stages, passes = [], [], []
    with (runtime / "evidence.jsonl").open("x") as evidence:

        def emit(record):
            evidence.write(json.dumps(record, separators=(",", ":")) + "\n")
            evidence.flush()
            if "case" in record:
                cases.append(record["case"])
            if "b5_stage" in record:
                need(len(stages) < len(B5_STAGES) and record["b5_stage"] == B5_STAGES[len(stages)])
                stages.append(record["b5_stage"])
            if "b5_live_case_passed" in record:
                need(stages == list(B5_STAGES) and not passes)
                passes.append(1)
            need("b5_failure" not in record and "failure" not in record)

        def run(label, args, seconds):
            need(a5.quiet(compose + args, env, seconds) == 0)
            emit({"operation": label, "exit": 0})

        try:
            emit({"source_sha": sha, "source_manifest_sha256": digest, "source_count": count})
            run("build", ["build", "runner"], 600)
            phase = "pull"
            run(phase, ["pull", "db", "storage", "gateway"], 300)
            a5.credentials(secret)  # unchanged generator, caller-owned root only
            phase = "native"
            run("create_db", ["create", "db"], 30)
            db = own_id(compose, env, "db")

            def state():
                output = subprocess.run(
                    ["docker", "inspect", "--type", "container", "--format", a5.STATE_FORMAT, db],
                    env=env,
                    capture_output=True,
                    timeout=2,
                    check=True,
                )
                return a5.selected_state(output.stdout)

            outcome = a5.supervise(
                ["docker", "start", "--attach", db],
                seconds=120,
                ready=lambda: "health_healthy" in state() and "running_true" in state(),
                detach=True,
                environment=env,
            )
            emit({"native_events": outcome.events, "native_state": state()})
            need("health_ready" in outcome.events and "running_true" in state())
            need(
                not set(outcome.events)
                & {"limit_time", "limit_bytes", "classifier_failure", "spawn_failure"}
            )
            phase = "prepare"
            runner_phase(compose, env, phase, 120, a5, emit)
            need(cases == ["prepare"])
            phase = "storage"
            run(phase, ["up", "-d", "--wait", "--wait-timeout", "120", "storage", "gateway"], 130)
            phase = "a5"
            runner_phase(compose, env, phase, A5_SECONDS, a5, emit)
            need(cases == list(A5_CASES))
            emit({"a5_direct_passed": 1})
            phase = "b5"
            runner_phase(compose, env, phase, B5_SECONDS, a5, emit)
            need(stages == list(B5_STAGES) and passes == [1])
            emit({"combined_passed": 1})
            result = 0
        except Exception:  # noqa: BLE001 — closed operation enum only
            emit({"operator_failure": phase})
        finally:
            if not cleanup(compose, env, secret, a5.quiet, emit):
                result = 2
    return result


def interrupted(*_):
    raise Refused()


def outer(sha, digest):
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, interrupted)
    os.umask(0o077)
    root = Path.cwd().resolve()
    need(re.fullmatch("[0-9a-f]{40}", sha) is not None)
    need(re.fullmatch("[0-9a-f]{64}", digest) is not None)
    need(str(root) == "/home/jre/notebook-tests/b5/upload-http-" + sha)
    need(not (root / ".b5-runtime").exists())
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/home/jre",
        "PYTHONDONTWRITEBYTECODE": "1",
        "B5_PARENT_PID": str(os.getpid()),
    }
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--execute", sha, digest],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    timed_out = False
    try:
        return child.wait(timeout=ACTIVE_SECONDS)
    except (subprocess.TimeoutExpired, Refused, KeyboardInterrupt, SystemExit, OSError):
        # Timeout OR catchable interruption must reap the execution.
        timed_out = True
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=45)  # child finally owns exact runner + namespace teardown
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
    finally:
        if timed_out:
            # Parent fallback after confirmed child exit, no whole-flow replay.
            runtime = root / ".b5-runtime"
            marker = runtime / "owner.pid"
            if (
                marker.is_file()
                and not marker.is_symlink()
                and marker.read_text() == str(child.pid)
            ):
                a5 = accepted(root)
                environment["A5_STAGE_B_ROOT"] = str(runtime / "credentials")
                environment.update(
                    A5_PHASE_A_UID=str(os.getuid()),
                    A5_PHASE_A_GID=str(os.getgid()),
                    B5_RUN_PHASE="prepare",
                )
                compose = [
                    "docker",
                    "compose",
                    "--env-file",
                    "/dev/null",
                    "-p",
                    PROJECT,
                    "-f",
                    str(root / REL / "compose.b5.json"),
                ]
                with (runtime / "outer-cleanup.jsonl").open("x") as log:
                    cleanup(
                        compose,
                        environment,
                        runtime / "credentials",
                        a5.quiet,
                        lambda record: log.write(json.dumps(record) + "\n"),
                    )
    return 2


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--run-combined":
        return outer(sys.argv[2], sys.argv[3])
    if (
        len(sys.argv) == 4
        and sys.argv[1] == "--execute"
        and os.environ.get("B5_PARENT_PID") == str(os.getppid())
    ):
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, interrupted)
        return execute(Path.cwd().resolve(), sys.argv[2], sys.argv[3])
    return 2


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:  # noqa: BLE001 — no raw fallback
        code = 2
    print("B5_OPERATOR_EXIT=" + str(code if code in (0, 1, 2) else 2), flush=True)
    raise SystemExit(code if code in (0, 1, 2) else 2)
