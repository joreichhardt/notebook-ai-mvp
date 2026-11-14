"""One-shot kube1 runtime check of an already built, owned immutable API image.

No build/pull/deploy/DB/BWS/Cloudflare calls. Never installs positive readiness probes.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

PROJECT = "notebook-test-readiness"
DOCKER = ("docker", "--host", "unix:///var/run/docker.sock")
ROOT = Path("/home/jre/notebook-tests/readiness")

SMOKE = r"""
import json, os, time, urllib.request, urllib.error
from pathlib import Path
for attempt in range(100):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health/live", timeout=1) as r:
            assert r.status == 200 and json.load(r) == {"status": "ok"}
        break
    except (OSError, ValueError):
        if attempt == 99: raise
        time.sleep(0.1)
try:
    urllib.request.urlopen("http://127.0.0.1:8000/api/health/ready", timeout=1)
    raise AssertionError("unassembled_runtime_reported_ready")
except urllib.error.HTTPError as r:
    assert r.code == 503 and json.load(r) == {"status": "not_ready"}
    assert r.headers.get("Cache-Control") == "no-store"
assert os.geteuid() == 10001 and os.getegid() == 10001
assert Path("/app/app/_release_sha").read_text() == os.environ["EXPECTED_IMAGE_RELEASE"]
assert os.environ["NOTEBOOK_RELEASE_SHA"] != os.environ["EXPECTED_IMAGE_RELEASE"]
assert "BWS_ACCESS_TOKEN" not in os.environ
cgroup = Path("/sys/fs/cgroup")
assert (cgroup / "memory.max").read_text().strip() == "536870912"
assert (cgroup / "memory.swap.max").read_text().strip() == "0"
quota, period = map(int, (cgroup / "cpu.max").read_text().split())
assert quota == period and quota > 0
assert (cgroup / "pids.max").read_text().strip() == "64"
space = os.statvfs("/tmp")
assert space.f_blocks * space.f_frsize == 32 * 1024 * 1024
status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
assert int(status["CapEff"].strip(), 16) == 0 and status["NoNewPrivs"].strip() == "1"
assert status["Seccomp"].strip() == "2"
root = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines() if line.split()[4] == "/"]
assert len(root) == 1 and "ro" in root[0][5].split(",")
print(json.dumps({"live": True, "unassembled_not_ready": True, "stamp": True, "nonroot": True}))
"""


class PacketError(Exception):
    pass


PACKET_FAILURES = (
    PacketError,
    OSError,
    ValueError,
    KeyError,
    IndexError,
    TypeError,
    AttributeError,
    subprocess.TimeoutExpired,
)


def require(condition, code):
    if not condition:
        raise PacketError(code)


def docker(*args, timeout=30):
    result = subprocess.run(
        [*DOCKER, *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    require(result.returncode == 0, "docker_operation_failed")
    return result.stdout.strip()


def create_args(image, release, name):
    # Deliberately contradictory runtime env tests that metadata is not env-controlled.
    spoof = "b" * 40 if release != "b" * 40 else "c" * 40
    return [
        "create",
        "--name",
        name,
        "--label",
        f"com.docker.compose.project={PROJECT}",
        "--label",
        f"notebook.test.sha={release}",
        "--network",
        "none",
        "--cgroupns",
        "private",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--user",
        "10001:10001",
        "--cpus",
        "1",
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
        "--pids-limit",
        "64",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777",
        "--env",
        f"EXPECTED_IMAGE_RELEASE={release}",
        "--env",
        f"NOTEBOOK_RELEASE_SHA={spoof}",
        image,
    ]


def owned(state, image, release):
    labels = state.get("Config", {}).get("Labels") or {}
    return (
        labels.get("com.docker.compose.project") == PROJECT
        and labels.get("notebook.test.sha") == release
        and state.get("Image") == image
    )


def runtime_constraints(state):
    config = state["HostConfig"]
    require(state["Config"]["User"] == "10001:10001", "runtime_user")
    require(config["ReadonlyRootfs"] is True, "runtime_readonly")
    require(config["NetworkMode"] == "none", "runtime_network")
    require(config["CgroupnsMode"] == "private", "runtime_cgroup_namespace")
    require(config["NanoCpus"] == 1_000_000_000, "runtime_cpu")
    require(config["Memory"] == 512 * 1024 * 1024, "runtime_memory")
    require(config["MemorySwap"] == config["Memory"], "runtime_swap")
    require(config["PidsLimit"] == 64, "runtime_pids")
    require(config["CapDrop"] == ["ALL"], "runtime_caps")
    require("no-new-privileges:true" in config["SecurityOpt"], "runtime_privileges")
    require(not config.get("Privileged"), "runtime_privileged")
    require(not config.get("PortBindings"), "runtime_ports")
    require(not config.get("Binds"), "runtime_binds")
    require(
        all(
            m.get("Type") == "tmpfs" and m.get("Destination") == "/tmp"
            for m in state.get("Mounts", [])
        ),
        "runtime_mounts",
    )
    require(not config.get("PidMode") and config.get("IpcMode") != "host", "runtime_host_namespace")
    require(set(config.get("Tmpfs", {})) == {"/tmp"}, "runtime_tmpfs")


def verify(image, release):
    require(socket.gethostname() == "kube1" and os.getuid() == 1001, "kube1_only")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image), "immutable_image_required")
    require(re.fullmatch(r"[0-9a-f]{40}", release), "full_release_required")
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(
        not ROOT.is_symlink()
        and (ROOT.stat().st_mode & 0o777) == 0o700
        and ROOT.stat().st_uid == os.geteuid(),
        "private_parent_required",
    )
    fd = os.open(ROOT / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        require(
            stat.S_ISREG(os.fstat(fd).st_mode)
            and (os.fstat(fd).st_mode & 0o777) == 0o600
            and os.fstat(fd).st_uid == os.geteuid(),
            "private_lock_required",
        )
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return verify_locked(image, release)
    finally:
        os.close(fd)


def verify_locked(image, release):
    # Retained-inode lock held by verify(); no overwrite/retry or distributed-lock claim.
    require(
        not docker("ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"),
        "namespace_busy",
    )
    require(
        not docker(
            "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"
        ),
        "namespace_busy",
    )
    require(
        not docker("volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"),
        "namespace_busy",
    )
    info = json.loads(docker("image", "inspect", image))[0]
    labels = info.get("Config", {}).get("Labels") or {}
    require(labels.get("com.docker.compose.project") == PROJECT, "unowned_image")
    require(labels.get("notebook.test.sha") == release, "image_source_mismatch")
    require(labels.get("org.opencontainers.image.revision") == release, "image_revision_mismatch")
    require(info["Config"]["User"] == "10001:10001", "image_user")
    require(not info["Config"].get("Volumes"), "image_implicit_volumes")
    env_names = {entry.split("=", 1)[0] for entry in info["Config"].get("Env", [])}
    require(
        not any(name.startswith("NOTEBOOK_") or name == "BWS_ACCESS_TOKEN" for name in env_names),
        "image_runtime_configuration",
    )
    evidence = ROOT / (".runtime-" + release)
    evidence.mkdir(mode=0o700)
    name = "notebook-readiness-" + release[:12]
    result = {
        "release": release,
        "image": image,
        "operator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "started": datetime.now(UTC).isoformat(),
        "passed": False,
        "cleanup": False,
    }
    attempted = False
    try:
        attempted = True
        docker(*create_args(image, release, name))
        state = json.loads(docker("inspect", name))[0]
        require(owned(state, image, release), "container_ownership")
        runtime_constraints(state)
        docker("start", name)
        smoke = json.loads(docker("exec", name, "python", "-I", "-c", SMOKE, timeout=45))
        require(
            smoke == {"live": True, "unassembled_not_ready": True, "stamp": True, "nonroot": True},
            "smoke_mismatch",
        )
        result["smoke"] = smoke
        result["passed"] = True
    except PACKET_FAILURES:
        result["error"] = "runtime_verification_failed"
    finally:
        if attempted:
            try:
                state = json.loads(docker("inspect", name))[0]
                require(owned(state, image, release), "cleanup_ownership")
                docker("rm", "-f", state["Id"])
                remaining = [
                    docker(*query, "--filter", f"label=com.docker.compose.project={PROJECT}")
                    for query in (("ps", "-aq"), ("network", "ls", "-q"), ("volume", "ls", "-q"))
                ]
                result["cleanup"] = not any(remaining)
            except PACKET_FAILURES:
                result["cleanup"] = False
        result["passed"] = result["passed"] and result["cleanup"]
        result["finished"] = datetime.now(UTC).isoformat()
        fd = os.open(evidence / "results.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
    return result["passed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--release", required=True)
    args = parser.parse_args()
    try:
        passed = verify(args.image, args.release)
    except PACKET_FAILURES:
        passed = False
    print(json.dumps({"passed": passed}))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
