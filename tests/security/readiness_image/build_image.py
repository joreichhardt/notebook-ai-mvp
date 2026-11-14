"""One reviewed kube1 legacy build + unassembled-runtime check; no push/deploy.

Run only from ROOT/<fullSHA>, after Main verified the exact Git archive/manifest.
Shares the verifier's retained-inode namespace lock. Never use a privileged helper
builder, global Docker config, foreign registry credentials or mutable image tag.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "native_verifier", Path(__file__).with_name("verify_image.py")
)
assert _spec and _spec.loader
native = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(native)


def build_args(source, release, network, iid):
    return [
        *native.DOCKER,
        "build",
        "--no-cache",
        "--pull=false",
        "--force-rm",
        "--rm",
        "--cpu-period",
        "100000",
        "--cpu-quota",
        "100000",
        "--memory",
        "1g",
        "--memory-swap",
        "1g",
        "--ulimit",
        "nproc=128:128",
        "--ulimit",
        "nofile=1024:1024",
        "--network",
        network,
        "--iidfile",
        str(iid),
        "--label",
        f"com.docker.compose.project={native.PROJECT}",
        "--label",
        f"notebook.test.sha={release}",
        "--build-arg",
        f"NOTEBOOK_RELEASE_SHA={release}",
        "--build-arg",
        "NOTEBOOK_TEST_LIMITS=1",
        "-f",
        str(source / "docker/api.Dockerfile"),
        str(source),
    ]


def build_environment(client):
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(client),
        "DOCKER_CONFIG": str(client),
        "DOCKER_BUILDKIT": "0",
    }


def namespace_empty():
    for query in (("ps", "-aq"), ("network", "ls", "-q"), ("volume", "ls", "-q")):
        native.require(
            not native.docker(
                *query, "--filter", f"label=com.docker.compose.project={native.PROJECT}"
            ),
            "namespace_busy",
        )


def network_owned(info, network, release):
    return (
        info.get("Id") == network
        and info.get("Driver") == "bridge"
        and info.get("Labels", {}).get("com.docker.compose.project") == native.PROJECT
        and info.get("Labels", {}).get("notebook.test.sha") == release
        and not info.get("Containers")
    )


def require_capacity():
    load = os.getloadavg()[0]
    free = shutil.disk_usage(native.ROOT).free
    mem = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    available = int(mem["MemAvailable"].split()[0]) * 1024
    native.require(load < 2 and free >= 5 * 2**30 and available >= 4 * 2**30, "capacity_required")
    return {"load_1m": load, "disk_free_bytes": free, "memory_available_bytes": available}


def build(release):
    native.require_operator()
    native.require(re.fullmatch(r"[0-9a-f]{40}", release), "full_release_required")
    source = Path(__file__).absolute().parents[3]
    native.require(
        source == native.ROOT / release and not source.is_symlink(), "exact_snapshot_required"
    )
    native.require(
        source.stat().st_uid == os.geteuid() and (source.stat().st_mode & 0o777) == 0o700,
        "private_snapshot_required",
    )
    capacity = require_capacity()
    with native.namespace_lock():
        namespace_empty()
        native.require(
            not any(
                name.startswith("notebook-test-")
                for name in native.docker(
                    "ps", "--format", '{{.Label "com.docker.compose.project"}}'
                ).splitlines()
            ),
            "heavy_slot_busy",
        )
        native.require(
            not (native.ROOT / (".runtime-" + release)).exists(), "runtime_evidence_exists"
        )
        evidence = native.ROOT / (".build-" + release)
        evidence.mkdir(mode=0o700)
        client = evidence / "client"
        client.mkdir(mode=0o700)
        with (client / "config.json").open("x") as stream:
            json.dump({"auths": {}}, stream)
        (client / "config.json").chmod(0o600)
        result = {
            "release": release,
            "started": datetime.now(UTC).isoformat(),
            "passed": False,
            "build_exit": None,
            "build_cleanup": False,
            "runtime": False,
            "capacity_before": capacity,
            "operator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        network = None
        try:
            network = native.docker(
                "network",
                "create",
                "--driver",
                "bridge",
                "--label",
                f"com.docker.compose.project={native.PROJECT}",
                "--label",
                f"notebook.test.sha={release}",
                "notebook-readiness-build-" + release[:12],
            )
            native.require(re.fullmatch(r"[0-9a-f]{64}", network), "network_identity")
            info = json.loads(native.docker("network", "inspect", network))[0]
            native.require(network_owned(info, network, release), "network_ownership")
            log = evidence / "build.log"
            iid = evidence / "image.id"
            fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                proc = subprocess.run(
                    build_args(source, release, network, iid),
                    env=build_environment(client),
                    cwd=source,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    timeout=900,
                    check=False,
                )
            result["build_exit"] = proc.returncode
            text = log.read_text(errors="replace")
            result["log_sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()
            native.require(proc.returncode == 0, "build_failed")
            native.require(
                "NOTEBOOK_BUILD_LIMITS_OK" in text.splitlines(),
                "effective_build_limits_unconfirmed",
            )
            children = re.findall(r"(?m)^\s*---> Running in ([0-9a-f]{12})\s*$", text)
            native.require(len(children) >= 3, "build_children_unconfirmed")
            result["build_children"] = children
            for child in children:
                native.require(
                    not native.docker("ps", "-aq", "--filter", f"id={child}"), "build_child_present"
                )
            image = iid.read_text().strip()
            native.require(re.fullmatch(r"sha256:[0-9a-f]{64}", image), "immutable_image_required")
            result["image"] = image
            info = json.loads(native.docker("network", "inspect", network))[0]
            native.require(network_owned(info, network, release), "network_cleanup_ownership")
            native.docker("network", "rm", network)
            network = None
            namespace_empty()
            result["build_cleanup"] = True
            # Same retained lock is still held. verify_locked rechecks image ownership
            # and namespace and records its own separate .runtime-<SHA>/results.json.
            result["runtime"] = native.verify_locked(image, release)
            result["passed"] = result["runtime"] is True
        except native.PACKET_FAILURES:
            result["error"] = "native_build_or_runtime_failed"
        finally:
            if network:
                try:
                    info = json.loads(native.docker("network", "inspect", network))[0]
                    native.require(
                        network_owned(info, network, release), "network_cleanup_ownership"
                    )
                    native.docker("network", "rm", network)
                except native.PACKET_FAILURES:
                    result["build_cleanup"] = False
            result["finished"] = datetime.now(UTC).isoformat()
            fd = os.open(evidence / "results.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(result, stream, indent=2, sort_keys=True)
        return result["passed"]


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    args = parser.parse_args()
    try:
        passed = build(args.release)
    except native.PACKET_FAILURES:
        passed = False
    print(json.dumps({"passed": passed}))
    raise SystemExit(0 if passed else 1)
