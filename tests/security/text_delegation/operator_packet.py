"""One-shot kube1 packet operator; approval/commit/full manifest anchor are external gates.

python3 operator_packet.py preflight|run FULL_SHA MANIFEST_SHA256
No operational effects at import time. Never rerun an existing evidence directory.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from protocol import PHASES, RELATIVE, Refused, need, safe_report, unique

BASE = Path("/home/jre/notebook-tests/text-delegation")
PROJECT = "notebook-test-text-delegation"
LABEL = "notebook.text-delegation.sha"
DOCKER_HOST = "unix:///var/run/docker.sock"


class ProcessFailed(Refused):
    def __init__(self, code, output):
        self.code = code
        self.output = output
        super().__init__()


EXPECTED_STAGES = ("migrate", "principals", *PHASES, "regression", "credentials")


def private_directory(path):
    info = path.lstat()
    need(path.resolve() == path and stat.S_ISDIR(info.st_mode))
    need(info.st_uid == 1001 and stat.S_IMODE(info.st_mode) == 0o700)


def host_guard():
    need(socket.gethostname().split(".")[0] == "kube1")
    need(os.getuid() == os.geteuid() == 1001 and os.getgid() == os.getegid() == 1001)
    private_directory(BASE)
    need(stat.S_ISSOCK(Path("/var/run/docker.sock").stat().st_mode))


@contextlib.contextmanager
def namespace_lock():
    """Permanent inode: NEVER unlink/replace even after failure or cleanup."""
    path = BASE / ".operator.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_uid == 1001 and info.st_nlink == 1)
        need(stat.S_IMODE(info.st_mode) == 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        need(path.lstat().st_ino == info.st_ino and path.lstat().st_dev == info.st_dev)
        yield
    finally:
        os.close(fd)


def command(args, env, timeout=30, material=None, limit=8 * 1024**2):
    """Bounded memory, scrubbed environment, process-group reap; never persist raw bytes."""
    child = subprocess.Popen(
        args,
        env=env,
        stdin=subprocess.PIPE if material else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    data = bytearray()
    try:
        if material:
            need(len(material) <= 4096 and child.stdin is not None)
            if child.stdin is None:
                raise Refused()
            child.stdin.write(material)
            child.stdin.close()
        need(child.stdout is not None)
        if child.stdout is None:
            raise Refused()
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            eof = False
            while not eof:
                need(time.monotonic() < deadline)
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    block = os.read(key.fd, 8192)
                    if not block:
                        eof = True
                        break
                    data.extend(block)
                    need(len(data) <= limit)
        code = child.wait(timeout=max(0.001, deadline - time.monotonic()))
        if code != 0:
            raise ProcessFailed(code, bytes(data))
        return bytes(data)
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=3)
        if child.stdout is not None:
            child.stdout.close()
        if child.stdin is not None:
            child.stdin.close()


def verify_source(root, sha, digest):
    need(re.fullmatch("[0-9a-f]{40}", sha) is not None and not sha.startswith("ac817f8"))
    need(re.fullmatch("[0-9a-f]{64}", digest) is not None)
    private_directory(root)
    path = root / ".source-manifest.json"
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_uid == 1001 and info.st_nlink == 1)
    need(stat.S_IMODE(info.st_mode) == 0o600 and info.st_size < 2**20)
    raw = path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == digest)
    data = json.loads(raw, object_pairs_hook=unique)
    need(set(data) == {"sha", "files"} and data["sha"] == sha)
    files = data["files"]
    need(type(files) is dict and 0 < len(files) < 10000)
    for name, expected in files.items():
        if not isinstance(name, str) or not isinstance(expected, dict):
            raise Refused()
        part = Path(name)
        need(not part.is_absolute() and ".." not in part.parts and part.as_posix() == name)
        need(not any(p in (".pi", ".git") for p in part.parts))
        need(set(expected) == {"mode", "sha256"} and expected["mode"] in (0o644, 0o755))
        target = root / part
        need(not any(p.is_symlink() for p in (target, *target.parents)))
        meta = target.lstat()
        need(stat.S_ISREG(meta.st_mode) and meta.st_uid == 1001 and meta.st_nlink == 1)
        need(stat.S_IMODE(meta.st_mode) == expected["mode"])
        need(hashlib.sha256(target.read_bytes()).hexdigest() == expected["sha256"])
    actual = set()
    for target in root.rglob("*"):
        need(not target.is_symlink() and (target.is_file() or target.is_dir()))
        if target.is_file():
            actual.add(target.relative_to(root).as_posix())
    need(actual == set(files) | {".source-manifest.json"})
    for name in (
        "backend/app/workers/delegation.py",
        "backend/migrations/versions/0007_text_dispatch.sql",
        "backend/tests/auth/test_text_delegation_migration.py",
        str(RELATIVE / "cases.json"),
    ):
        need(name in files)
    return len(files)


def docker_environment(config):
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(config),
        "DOCKER_CONFIG": str(config),
        "DOCKER_HOST": DOCKER_HOST,
        "LANG": "C.UTF-8",
        "TD_SHA": "",
        "TD_SNAPSHOT": "",
    }


def inventory(env):
    result = {}
    for kind in ("container", "network", "volume"):
        flags = (
            ["-aq", "--no-trunc"]
            if kind == "container"
            else ["-q", "--no-trunc"]
            if kind == "network"
            else ["-q"]
        )
        raw = command(
            [
                "docker",
                kind,
                "ls",
                *flags,
                "--filter",
                f"label=com.docker.compose.project={PROJECT}",
            ],
            env,
            5,
        )
        ids = raw.decode("ascii").splitlines()
        need(len(ids) < 12 and len(set(ids)) == len(ids))
        need(all(re.fullmatch("[0-9a-f]{64}", i) for i in ids) if kind != "volume" else not ids)
        result[kind] = ids
    return result


def owned(kind, identifier, sha, env):
    need(kind in ("container", "network") and re.fullmatch("[0-9a-f]{64}", identifier))
    field = ".Config.Labels" if kind == "container" else ".Labels"
    fmt = (
        "{{index "
        + field
        + ' "com.docker.compose.project"}}|{{index '
        + field
        + ' "'
        + LABEL
        + '"}}'
    )
    raw = command(["docker", kind, "inspect", "--format", fmt, identifier], env, 5)
    need(raw.strip() == (PROJECT + "|" + sha).encode())


class RememberingEmitter:
    """Evidence failure is sticky, but must never interrupt ownership-checked cleanup."""

    def __init__(self, emit):
        self.emit = emit
        self.failed = False

    def __call__(self, report):
        try:
            self.emit(report)
        except Exception:  # noqa: BLE001 — disk failure cannot certify acceptance
            self.failed = True


def cleanup(env, sha, emit):
    """Inspect labels then delete exact owned IDs only; unknown resources fail closed."""
    if not isinstance(emit, RememberingEmitter):
        emit = RememberingEmitter(emit)
    success = True
    for kind in ("container", "network"):
        try:
            ids = inventory(env)[kind]
        except Exception:  # noqa: BLE001 — no broad namespace down/prune fallback
            success = False
            continue
        for identifier in ids:
            try:
                owned(kind, identifier, sha, env)
                args = (
                    ["docker", kind, "rm"]
                    + (["--force"] if kind == "container" else [])
                    + [identifier]
                )
                emit({"stage": "cleanup-owned-id", "kind": kind, "id": identifier})
                command(args, env, 20)
            except Exception:  # noqa: BLE001 — still inspect other exact IDs
                success = False
    try:
        counts = [len(ids) for ids in inventory(env).values()]
    except Exception:  # noqa: BLE001
        counts = [-1, -1, -1]
    success = success and counts == [0, 0, 0] and not emit.failed
    emit({"stage": "cleanup", "ok": success, "namespace": counts})
    return success and not emit.failed


def cleanup_runtime(env, sha, emit, secret, config):
    """Resource, credential and client-config cleanup each get an independent attempt."""
    cleaned = credentials_removed = False
    try:
        cleaned = cleanup(env, sha, emit)
    except BaseException:  # noqa: BLE001 — catchable interruption still deletes host material
        cleaned = False
    finally:
        try:
            secret.unlink(missing_ok=True)
            credentials_removed = not secret.exists()
        except BaseException:  # noqa: BLE001
            credentials_removed = False
        finally:
            try:
                shutil.rmtree(config)
                credentials_removed = credentials_removed and not config.exists()
            except BaseException:  # noqa: BLE001
                credentials_removed = False
    emit({"stage": "host-credentials", "ok": credentials_removed})
    return cleaned and credentials_removed and not emit.failed


def capacity(env):
    need(all(not values for values in inventory(env).values()))
    available = (
        int(
            next(
                line.split()[1]
                for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:")
            )
        )
        * 1024
    )
    need(available >= 4 * 1024**3 and shutil.disk_usage(BASE).free >= 4 * 1024**3)
    need(os.getloadavg()[0] <= 6)
    command(["docker", "version", "--format", "{{.Server.Version}}"], env, 5)
    command(["docker", "compose", "version", "--short"], env, 5)
    return {
        "stage": "preflight",
        "ok": True,
        "namespace": [0, 0, 0],
        "available_mib": available // 1024**2,
    }


def own_service(compose, service, sha, env):
    raw = command([*compose, "ps", "--all", "--quiet", service], env, 5)
    identifier = raw.decode().strip()
    owned("container", identifier, sha, env)
    label = command(
        [
            "docker",
            "container",
            "inspect",
            "--format",
            '{{index .Config.Labels "com.docker.compose.service"}}',
            identifier,
        ],
        env,
        5,
    )
    need(label.strip() == service.encode())
    return identifier


def attest_container(identifier, service, env):
    raw = command(["docker", "container", "inspect", "--format", "{{json .}}", identifier], env, 5)
    data = json.loads(raw)
    host, config = data["HostConfig"], data["Config"]
    need(config["User"] == ("70:70" if service == "db" else "1001:1001"))
    need(host["ReadonlyRootfs"] and host["CapDrop"] == ["ALL"] and not host["CapAdd"])
    need(host["SecurityOpt"] == ["no-new-privileges:true"] and not host["Privileged"])
    need(host["NanoCpus"] == 1000000000 and host["PidsLimit"] == 128)
    need(host["Memory"] == (512 if service == "db" else 1536) * 1024**2)
    need(host["MemorySwap"] == host["Memory"])
    compose = json.loads(Path(__file__).with_name("compose.json").read_text())
    expected_tmpfs = compose["services"][service]["tmpfs"]
    need(set(host["Tmpfs"]) == {entry.split(":", 1)[0] for entry in expected_tmpfs})
    for entry in expected_tmpfs:
        destination, options = entry.split(":", 1)
        need(set(options.split(",")) <= set(host["Tmpfs"][destination].split(",")))
    need(
        not host["PortBindings"] and not config.get("ExposedPorts")
        if service == "runner"
        else not host["PortBindings"]
    )
    need(not host.get("Devices") and host["NetworkMode"] not in ("host", "none"))
    need(not host["PidMode"] and host["IpcMode"] == "private")
    need(host["LogConfig"]["Type"] == "none")
    mounts = [m for m in data["Mounts"] if m["Type"] != "tmpfs"]
    need(
        not mounts
        if service == "db"
        else len(mounts) == 1
        and mounts[0]["Source"] == env["TD_SNAPSHOT"]
        and mounts[0]["Destination"] == "/snapshot"
        and mounts[0]["RW"] is False
    )
    # Inspect actual kernel confinement for both nonroot images, not just config.
    raw = command(["docker", "exec", identifier, "cat", "/proc/self/status"], env, 5)
    status = dict(line.split(":", 1) for line in raw.decode().splitlines())
    uid = "70" if service == "db" else "1001"
    need(status["Uid"].split() == [uid] * 4 and status["Gid"].split() == [uid] * 4)
    need(
        int(status["CapEff"], 16) == 0
        and status["NoNewPrivs"].strip() == "1"
        and status["Seccomp"].strip() == "2"
    )


def execute(sha, digest, mode):
    host_guard()
    os.umask(0o077)
    with namespace_lock():
        root = BASE / sha
        need(Path(__file__).resolve() == root / RELATIVE / "operator_packet.py")
        count = verify_source(root, sha, digest)
        runtime = BASE / (".runtime-" + sha)
        need(not runtime.exists() and not runtime.is_symlink())
        with tempfile.TemporaryDirectory(prefix=".docker-preflight-", dir=BASE) as temporary:
            initial = capacity(docker_environment(Path(temporary)))
        if mode == "preflight":
            print(
                json.dumps(
                    {**initial, "source_files": count, "sha": sha, "manifest_sha256": digest}
                )
            )
            return 0
        runtime.mkdir(mode=0o700)
        config = runtime / "docker"
        config.mkdir(mode=0o700)
        env = docker_environment(config)
        env.update(TD_SHA=sha, TD_SNAPSHOT=str(root))
        compose = [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-p",
            PROJECT,
            "-f",
            str(root / RELATIVE / "compose.json"),
        ]
        secret = runtime / "synthetic.json"
        success = cleaned = False
        evidence = None

        def write_report(report):
            if evidence is None:
                raise Refused()
            evidence.write(json.dumps({"time": datetime.now(UTC).isoformat(), **report}) + "\n")
            evidence.flush()

        emit = RememberingEmitter(write_report)
        with contextlib.ExitStack() as stack:
            stage = "configure"
            try:
                evidence = stack.enter_context((runtime / "evidence.jsonl").open("x"))
                emit({"stage": "source", "sha": sha, "manifest_sha256": digest, "files": count})
                emit(initial)
                need(not emit.failed)
                material = {"admin": secrets.token_hex(24), "app": secrets.token_hex(24)}
                with secret.open("x") as stream:
                    json.dump(material, stream)
                command([*compose, "config", "--quiet"], env)
                stage = "pull"
                command([*compose, "pull"], env, 600)
                stage = "create"
                command([*compose, "create", "db", "runner"], env, 60)
                db = own_service(compose, "db", sha, env)
                runner = own_service(compose, "runner", sha, env)
                emit({"stage": "owned-containers", "db": db, "runner": runner})
                command(["docker", "start", db, runner], env)
                attest_container(db, "db", env)
                attest_container(runner, "runner", env)
                emit({"stage": "hardening", "ok": True})
                stage = "bootstrap"
                command(
                    [
                        "docker",
                        "exec",
                        "-i",
                        db,
                        "/bin/sh",
                        "-ec",
                        "umask 077; cat > /tmp/bootstrap.pending; mv /tmp/bootstrap.pending /tmp/bootstrap",
                    ],
                    env,
                    material=material["admin"].encode(),
                )
                deadline = time.monotonic() + 100
                while True:
                    need(time.monotonic() < deadline)
                    try:
                        command(
                            [
                                "docker",
                                "exec",
                                db,
                                "pg_isready",
                                "-h",
                                "127.0.0.1",
                                "-U",
                                "postgres",
                                "-d",
                                "delegation",
                            ],
                            env,
                            3,
                        )
                        break
                    except Refused:
                        time.sleep(1)
                stage = "dependencies"
                raw = command(
                    [
                        "docker",
                        "exec",
                        runner,
                        "python",
                        "/snapshot/" + str(RELATIVE / "run_packet.py"),
                        "dependencies",
                    ],
                    env,
                    840,
                )
                need(json.loads(raw) == {"stage": "dependencies", "ok": True})
                emit(safe_report(json.loads(raw)))
                # External network exists only for bounded frozen install. Database never joins it.
                nets = inventory(env)["network"]
                names = {}
                for identifier in nets:
                    owned("network", identifier, sha, env)
                    name = (
                        command(
                            ["docker", "network", "inspect", "--format", "{{.Name}}", identifier],
                            env,
                            5,
                        )
                        .decode()
                        .strip()
                    )
                    names[name] = identifier
                need(set(names) == {PROJECT + "_database", PROJECT + "_dependencies"})
                external, internal = names[PROJECT + "_dependencies"], names[PROJECT + "_database"]
                command(["docker", "network", "disconnect", external, runner], env, 10)
                command(["docker", "network", "connect", internal, runner], env, 10)
                for identifier in (runner, db):
                    raw = command(
                        [
                            "docker",
                            "container",
                            "inspect",
                            "--format",
                            "{{json .NetworkSettings.Networks}}",
                            identifier,
                        ],
                        env,
                        5,
                    )
                    need(set(json.loads(raw)) == {PROJECT + "_database"})
                need(
                    command(
                        ["docker", "network", "inspect", "--format", "{{.Internal}}", internal],
                        env,
                        5,
                    ).strip()
                    == b"true"
                )
                emit({"stage": "runtime-network", "ok": True})
                stage = "tests"
                test_exit = 0
                try:
                    raw = command(
                        [
                            "docker",
                            "exec",
                            "-i",
                            runner,
                            "python",
                            "/snapshot/" + str(RELATIVE / "run_packet.py"),
                            "run",
                        ],
                        env,
                        1800,
                        material=json.dumps(material).encode(),
                    )
                except ProcessFailed as failure:
                    test_exit, raw = failure.code, failure.output
                reports = [safe_report(json.loads(line)) for line in raw.splitlines()]
                stages = tuple(r["stage"] for r in reports)
                need(
                    stages == EXPECTED_STAGES[: len(stages)]
                    or (
                        stages
                        and stages[-1] == "credentials"
                        and stages[:-1] == EXPECTED_STAGES[: len(stages) - 1]
                    )
                )
                for report in reports:
                    emit(report)
                emit(
                    {
                        "stage": "test-exit",
                        "ok": test_exit == 0,
                        "exit": test_exit if -128 <= test_exit <= 255 else 255,
                    }
                )
                need(test_exit == 0 and stages == EXPECTED_STAGES and all(r["ok"] for r in reports))
                # docker exec must not have hidden OOM/container death behind its exit code.
                for identifier in (runner, db):
                    raw = command(
                        [
                            "docker",
                            "container",
                            "inspect",
                            "--format",
                            "{{.State.Running}}|{{.State.OOMKilled}}|{{.State.Error}}",
                            identifier,
                        ],
                        env,
                        5,
                    )
                    need(raw.strip() == b"true|false|")
                need(verify_source(root, sha, digest) == count)
                success = True
                emit({"stage": "process-exits", "ok": True})
            except BaseException:  # noqa: BLE001 — fixed phase only; no raw errors/SQL/output
                emit({"stage": "failure", "operation": stage, "ok": False})
            finally:
                cleaned = cleanup_runtime(env, sha, emit, secret, config)
            emit({"stage": "complete", "ok": success and cleaned and not emit.failed})
        return 0 if success and cleaned and not emit.failed else 1


def interrupted(*_):
    raise Refused()


def main():
    need(len(sys.argv) == 4 and sys.argv[1] in ("preflight", "run"))
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, interrupted)
    return execute(sys.argv[2], sys.argv[3], sys.argv[1])


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:  # noqa: BLE001 — no raw fallback
        code = 1
    print("TEXT_DELEGATION_OPERATOR_EXIT=" + str(code), flush=True)
    raise SystemExit(code)
