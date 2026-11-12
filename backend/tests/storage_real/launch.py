"""kube1-only one-shot Phase A supervisor; never retries failed runs."""

import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT = "notebook-test-a5-storage"
ROOT = Path.cwd().resolve()
BASE = ROOT / "backend/tests/storage_real"


def main():
    if sys.argv[1:] != ["--run-phase-a"] or not str(ROOT).startswith(
        "/home/jre/notebook-tests/a5/storage-"
    ):
        raise SystemExit("REFUSED_NON_ASSIGNED_SNAPSHOT")
    for filename, expected in {
        "roles.sql": "3ad717b225daa38aa982da26750f35641eb404e1eb5e69a763c22236ab96c1b2",
        "jwt.sql": "c1d3f5c01e44646c2e533c5a1c111821cc49cb5a4c2534e08cddad61d8fb0339",
    }.items():
        assert hashlib.sha256((BASE / filename).read_bytes()).hexdigest() == expected
    os.umask(0o077)
    runtime = ROOT / ".phase-a-runtime"
    runtime.mkdir(mode=0o700)  # Existing run is a refusal, never overwrite evidence.
    secret_dir = runtime / "credentials"
    secret_dir.mkdir(mode=0o700)
    env = dict(os.environ)
    env.update(
        A5_PHASE_A_SECRETS=str(secret_dir),
        A5_PHASE_A_UID=str(os.getuid()),
        A5_PHASE_A_GID=str(os.getgid()),
    )
    compose = [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "-p",
        PROJECT,
        "-f",
        str(BASE / "compose.yaml"),
    ]

    def inventory():
        counts = []
        for kind in ("container", "volume", "network"):
            args = [
                "docker",
                kind,
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={PROJECT}",
            ]
            if kind == "container":
                args.insert(3, "-a")
            result = subprocess.run(args, check=True, capture_output=True, timeout=15)
            counts.append(len(result.stdout.splitlines()))
        return counts

    # Refuse touching pre-existing resources, even though the project is ours.
    assert inventory() == [0, 0, 0]
    exit_code = 1
    cleanup_code = 1
    try:
        with (runtime / "build.log").open("xb") as log:
            result = subprocess.run(
                compose + ["build", "runner"],
                env=env,
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=600,
            )
        print(f"BUILD_EXIT={result.returncode}", flush=True)
        if result.returncode:
            return 1
        with (runtime / "pull.log").open("xb") as log:
            result = subprocess.run(
                compose + ["pull", "db"],
                env=env,
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
        print(f"PULL_EXIT={result.returncode}", flush=True)
        if result.returncode:
            return 1
        with (secret_dir / "bootstrap").open("x") as stream:
            stream.write(secrets.token_hex(32))
        assert (secret_dir / "bootstrap").stat().st_mode & 0o777 == 0o600
        deadline = time.monotonic() + 120
        with (runtime / "start.log").open("xb") as log:
            result = subprocess.run(
                compose + ["up", "-d", "--wait", "--wait-timeout", "100", "db"],
                env=env,
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=max(1, deadline - time.monotonic()),
            )
        print(f"START_EXIT={result.returncode}", flush=True)
        if result.returncode:
            return 1
        with (runtime / "run.log").open("xb") as log:
            result = subprocess.run(
                compose + ["run", "--rm", "-T", "runner"],
                env=env,
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=max(1, deadline - time.monotonic()),
            )
        exit_code = result.returncode
        print(f"RUNNER_EXIT={exit_code}", flush=True)
        return exit_code
    except (
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
        OSError,
        AssertionError,
    ) as exc:
        print(f"SUPERVISOR_FAILED_TYPE={type(exc).__name__}", flush=True)
        return 1
    finally:
        try:
            with (runtime / "cleanup.log").open("xb") as log:
                result = subprocess.run(
                    compose + ["down", "--volumes", "--remove-orphans", "--timeout", "5"],
                    env=env,
                    check=False,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=45,
                )
                cleanup_code = result.returncode
        finally:
            shutil.rmtree(secret_dir)
        counts = inventory()
        print(f"CLEANUP_EXIT={cleanup_code}", flush=True)
        print(f"OWN_RESOURCES={counts}", flush=True)
        print(f"EPHEMERAL_CREDENTIALS_REMOVED={int(not secret_dir.exists())}", flush=True)
        if cleanup_code or counts != [0, 0, 0]:
            raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
