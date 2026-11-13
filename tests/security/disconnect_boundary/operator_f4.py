"""Kube1-only operator. Local preparation is safe; execution needs Main packet approval.

Usage: python3 operator_f4.py preflight FULL_SHA | run FULL_SHA
A run is single-shot; every failure requires a Main ruling, not automatic retry.
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

BASE = Path("/home/jre/notebook-tests/main")
PROJECT = "notebook-test-main"
RELATIVE = Path("tests/security/disconnect_boundary")


def command(args, env=None, timeout=30):
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


def preflight():
    if socket.gethostname().split(".")[0] != "kube1" or os.getuid() == 0:
        raise RuntimeError("wrong-host-or-user")
    if (
        BASE.is_symlink()
        or BASE.stat().st_uid != os.getuid()
        or stat.S_IMODE(BASE.stat().st_mode) != 0o700
    ):
        raise RuntimeError("unsafe-assigned-parent")
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
    # Only the reviewed runner's structured, safe fields may reach evidence.
    if (
        not isinstance(report, dict)
        or report.get("stage")
        not in {"prepare", "dependencies", "migrate", "role", "postgres", "focused"}
        or type(report.get("ok")) is not bool
    ):
        raise RuntimeError("invalid-runner-report")
    if set(report) - {
        "stage",
        "ok",
        "error_type",
        "sqlstate",
        "passed",
        "failed",
        "skipped",
        "expected",
        "cases",
    }:
        raise RuntimeError("unknown-report-field")
    for key in ("passed", "failed", "skipped", "expected"):
        if key in report and (type(report[key]) is not int or not 0 <= report[key] <= 1000):
            raise RuntimeError("invalid-report-count")
    for key, pattern in (("error_type", r"[A-Za-z_]{1,64}"), ("sqlstate", r"[0-9A-Z]{5}")):
        if key in report and (
            not isinstance(report[key], str) or not re.fullmatch(pattern, report[key])
        ):
            raise RuntimeError("invalid-report-type")
    for case in report.get("cases", []):
        if set(case) - {"test", "phase", "outcome", "error_type", "label"}:
            raise RuntimeError("invalid-case-fields")
        if not re.fullmatch(r"[A-Za-z0-9_./:\[\]-]{1,400}", case.get("test", "")):
            raise RuntimeError("invalid-case-name")
        if case.get("phase") not in ("setup", "call", "teardown") or case.get("outcome") not in (
            "passed",
            "failed",
            "skipped",
        ):
            raise RuntimeError("invalid-case-outcome")
        if "label" in case and not re.fullmatch(r"F4 [A-Za-z0-9 /()_-]{1,150}", case["label"]):
            raise RuntimeError("invalid-safe-label")
        if "error_type" in case and not re.fullmatch(r"[A-Za-z_]{1,64}", case["error_type"]):
            raise RuntimeError("invalid-case-type")
    return report


def run(sha):
    initial = preflight()  # Repeat immediately before mutation; do not trust old capacity.
    root = BASE / sha
    if Path(__file__).resolve() != root / RELATIVE / "operator_f4.py":
        raise RuntimeError("not-immutable-snapshot")
    if (
        not (root / "uv.lock").is_file()
        or root.is_symlink()
        or root.stat().st_uid != os.getuid()
        or stat.S_IMODE(root.stat().st_mode) != 0o700
    ):
        raise RuntimeError("snapshot-invalid")
    runtime = BASE / (".runtime-f4-" + sha)
    runtime.mkdir(mode=0o700)  # exclusive: never reuse/overwrite previous evidence
    logfile = runtime / "evidence.jsonl"
    descriptor = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    bootstrap = runtime / "bootstrap"
    env = {
        key: value
        for key, value in os.environ.items()
        if key in ("PATH", "HOME", "XDG_RUNTIME_DIR")
    }
    env.update(
        {
            "F4_SHA": sha,
            "F4_DATABASE": "notebook_f4_" + sha[:12],
            "F4_SNAPSHOT": str(root),
            "F4_BOOTSTRAP_FILE": str(bootstrap),
            "F4_UID": str(os.getuid()),
            "F4_GID": str(os.getgid()),
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
        str(root / RELATIVE / "compose.f4.yaml"),
    ]
    started = False
    success = False
    cleanup = False
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
            # File readable by both container UIDs; host parent0700 protects it.
            with os.fdopen(
                os.open(bootstrap, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444), "w"
            ) as stream:
                stream.write(secrets.token_hex(24))
            stage = "compose-config"
            if command([*compose, "config", "--quiet"], env).returncode:
                raise RuntimeError("compose-config")
            stage = "pull"
            if command([*compose, "pull"], env, 600).returncode:
                raise RuntimeError("pull")
            stage = "database-start"
            started = True  # even partially failed up must be torn down
            if command(
                [*compose, "up", "-d", "--wait", "--wait-timeout", "90", "db"], env, 120
            ).returncode:
                raise RuntimeError("database-start")
            stage = "runner"
            result = command([*compose, "run", "--rm", "-T", "--no-deps", "runner"], env, 1800)
            reports = []
            for line in result.stdout.splitlines():
                if line.startswith(b"F4_RESULT "):
                    report = safe_report(json.loads(line[len(b"F4_RESULT ") :]))
                    reports.append(report)
                    record(report)
            success = (
                result.returncode == 0
                and [r["stage"] for r in reports] == ["migrate", "role", "postgres", "focused"]
                and all(r["ok"] for r in reports)
            )
            record({"stage": "run-exit", "ok": success, "exit": result.returncode})
        except BaseException as error:  # noqa: BLE001 — fixed stage, no exception text
            record({"stage": stage, "ok": False, "error_type": type(error).__name__})
        finally:
            try:
                stage = "cleanup"
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
                record({"stage": stage, "ok": cleanup, "namespace": counts})
            except BaseException as error:  # noqa: BLE001 — exact namespace only, never broad prune
                record({"stage": "cleanup", "ok": False, "error_type": type(error).__name__})
            finally:
                bootstrap.unlink(missing_ok=True)
                record({"stage": "credential-removal", "ok": not bootstrap.exists()})
    return 0 if success and cleanup else 1


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
    except BaseException as error:  # noqa: BLE001 — no raw output, one-shot failure
        print(
            json.dumps({"stage": "operator", "ok": False, "error_type": type(error).__name__}),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
