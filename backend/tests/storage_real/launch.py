"""kube1-only one-shot Phase A supervisor; never retries failed runs."""

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

if __package__:
    from .diagnostics import selected_state, supervise
else:
    from diagnostics import selected_state, supervise

STATE_FORMAT = (
    '{"ExitCode":{{.State.ExitCode}},"OOMKilled":{{.State.OOMKilled}},'
    '"Running":{{.State.Running}},"Health":"{{if .State.Health}}{{.State.Health.Status}}'
    '{{else}}absent{{end}}","Error":"{{if .State.Error}}present{{end}}"}'
)

PROJECT = "notebook-test-a5-storage"
ROOT = Path.cwd().resolve()
BASE = ROOT / "backend/tests/storage_real"


def main():
    if sys.argv[1:] != ["--run-phase-a"] or not str(ROOT).startswith(
        "/home/jre/notebook-tests/a5/storage-"
    ):
        raise SystemExit("REFUSED_NON_ASSIGNED_SNAPSHOT")
    for filename, expected in {
        "webhooks.sql": "05a61316b899374253891cbba66ce073d9273f33983c3f47961eda3aa26c2823",
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
    container_id = None

    def state():
        if container_id is None:
            return ("metadata_unknown",)
        result = subprocess.run(
            ["docker", "inspect", "--type", "container", "--format", STATE_FORMAT, container_id],
            capture_output=True,
            check=False,
            timeout=1,
        )
        return selected_state(result.stdout) if result.returncode == 0 else ("metadata_unknown",)

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
        with (runtime / "create.log").open("xb") as log:
            result = subprocess.run(
                compose + ["create", "db"],
                env=env,
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=max(1, deadline - time.monotonic()),
            )
        print(f"CREATE_EXIT={result.returncode}", flush=True)
        if result.returncode:
            return 1
        result = subprocess.run(
            compose + ["ps", "--all", "--quiet", "db"],
            env=env,
            check=True,
            capture_output=True,
            timeout=5,
        )
        candidate = result.stdout.strip()
        assert re.fullmatch(rb"[a-f0-9]{64}", candidate), "invalid_own_container_id"
        container_id = candidate.decode("ascii")
        last_probe = 0.0
        current_state = ("metadata_unknown",)

        def ready():
            nonlocal last_probe, current_state
            if time.monotonic() - last_probe >= 0.5:
                current_state = state()
                last_probe = time.monotonic()
            return "running_true" in current_state and "health_healthy" in current_state

        outcome = supervise(
            ["docker", "start", "--attach", container_id],
            seconds=max(0.001, deadline - time.monotonic()),
            ready=ready,
            detach=True,
        )
        final_state = state()
        evidence = {
            "client_exit": outcome.exit_code,
            "events": outcome.events,
            "state": final_state,
        }
        with (runtime / "native-start.json").open("x") as log:
            json.dump(evidence, log)
        print("NATIVE_START=" + json.dumps(evidence), flush=True)
        failed = any(
            event in outcome.events
            for event in (
                "limit_time",
                "limit_bytes",
                "classifier_failure",
                "spawn_failure",
            )
        )
        if failed or "health_ready" not in outcome.events or "running_true" not in final_state:
            print("START_EXIT=1", flush=True)
            return 1
        print("START_EXIT=0", flush=True)
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
            # Capture selected state before cleanup, even if readiness/runner failed.
            try:
                print("PRE_CLEANUP_STATE=" + json.dumps(state()), flush=True)
            except (OSError, subprocess.SubprocessError):
                print('PRE_CLEANUP_STATE=["metadata_unknown"]', flush=True)
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
