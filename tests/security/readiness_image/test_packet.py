"""No Docker calls: test argv, inspect gates, locking and owned cleanup with fakes."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def packet():
    spec = importlib.util.spec_from_file_location(
        "readiness_packet", Path(__file__).with_name("verify_image.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def state(packet):
    return {
        "Id": "owned-container-id",
        "Image": "sha256:" + "a" * 64,
        "Config": {
            "User": "10001:10001",
            "Labels": {
                "com.docker.compose.project": packet.PROJECT,
                "notebook.test.sha": "a" * 40,
            },
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "NetworkMode": "none",
            "CgroupnsMode": "private",
            "NanoCpus": 1_000_000_000,
            "Memory": 512 * 1024 * 1024,
            "MemorySwap": 512 * 1024 * 1024,
            "PidsLimit": 64,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Tmpfs": {"/tmp": "rw,nosuid,nodev,noexec,size=32m,mode=1777"},
            "IpcMode": "private",
        },
        "Mounts": [],
    }


def test_argv_has_only_isolated_native_resources(packet):
    args = packet.create_args("sha256:" + "a" * 64, "a" * 40, "owned")
    assert args[0] == "create"
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--user") + 1] == "10001:10001"
    assert args[args.index("--cpus") + 1] == "1"
    assert args[args.index("--memory") + 1] == "512m"
    assert "--read-only" in args and "--cap-drop" in args
    assert not {
        "--privileged",
        "--pid",
        "--ipc",
        "--publish",
        "-p",
        "--volume",
        "-v",
        "--mount",
        "--env-file",
    }.intersection(args)
    assert "NOTEBOOK_RELEASE_SHA=" + "b" * 40 in args
    assert "BWS_ACCESS_TOKEN" not in " ".join(args)
    compile(packet.SMOKE, "native-smoke", "exec")


def test_inspect_baseline_and_optional_tmpfs_are_valid(packet, state):
    packet.runtime_constraints(state)
    state["Mounts"] = [{"Type": "tmpfs", "Destination": "/tmp"}]
    packet.runtime_constraints(state)


@pytest.mark.parametrize(
    "key,value",
    [
        ("ReadonlyRootfs", False),
        ("NetworkMode", "host"),
        ("NanoCpus", 0),
        ("Memory", 0),
        ("MemorySwap", -1),
        ("PidsLimit", -1),
        ("CapDrop", []),
        ("SecurityOpt", []),
        ("Privileged", True),
        ("Binds", ["/:/host"]),
        ("PortBindings", {"8000/tcp": [{"HostPort": "8000"}]}),
        ("PidMode", "host"),
        ("IpcMode", "host"),
        ("Tmpfs", {}),
    ],
)
def test_inspect_rejects_each_missing_safety_gate(packet, state, key, value):
    state["HostConfig"][key] = value
    with pytest.raises(packet.PacketError):
        packet.runtime_constraints(state)


def test_inspect_rejects_host_mount_or_root_user(packet, state):
    state["Mounts"] = [{"Type": "bind", "Destination": "/run/docker.sock"}]
    with pytest.raises(packet.PacketError):
        packet.runtime_constraints(state)
    state["Mounts"] = []
    state["Config"]["User"] = "0"
    with pytest.raises(packet.PacketError):
        packet.runtime_constraints(state)


def test_ownership_requires_exact_image_and_release(packet, state):
    image = "sha256:" + "a" * 64
    assert packet.owned(state, image, "a" * 40)
    assert not packet.owned(state, "sha256:" + "b" * 64, "a" * 40)
    assert not packet.owned(state, image, "b" * 40)
    state["Config"]["Labels"]["com.docker.compose.project"] = "foreign"
    assert not packet.owned(state, image, "a" * 40)


def test_laptop_refusal_precedes_all_docker_io(packet, monkeypatch):
    monkeypatch.setattr(packet.socket, "gethostname", lambda: "laptop")
    monkeypatch.setattr(
        packet, "docker", lambda *args, **kwargs: pytest.fail("must not call Docker")
    )
    with pytest.raises(packet.PacketError, match="kube1_only"):
        packet.verify("sha256:" + "a" * 64, "a" * 40)


@pytest.mark.parametrize("uid,euid", [(0, 0), (1001, 0), (1000, 1001), (1001, 1000)])
def test_real_and_effective_operator_uid_must_match(packet, monkeypatch, uid, euid):
    monkeypatch.setattr(packet.socket, "gethostname", lambda: "kube1")
    monkeypatch.setattr(packet.os, "getuid", lambda: uid)
    monkeypatch.setattr(packet.os, "geteuid", lambda: euid)
    with pytest.raises(packet.PacketError, match="kube1_only"):
        packet.require_operator()


@pytest.fixture
def fake_run(packet, state, tmp_path, monkeypatch):
    calls = []
    image = state["Image"]
    release = "a" * 40
    labels = copy.deepcopy(state["Config"]["Labels"])
    labels["org.opencontainers.image.revision"] = release
    image_info = {
        "Config": {"User": "10001:10001", "Labels": labels, "Env": ["PATH=/app/.venv/bin:/usr/bin"]}
    }

    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps([image_info])
        if args[0] == "inspect":
            return json.dumps([state])
        if args[0] == "exec":
            return json.dumps(
                {"live": True, "unassembled_not_ready": True, "stamp": True, "nonroot": True}
            )
        return ""

    monkeypatch.setattr(packet, "ROOT", tmp_path / "native")
    monkeypatch.setattr(packet, "require_operator", lambda: None)  # explicitly synthetic host
    monkeypatch.setattr(packet, "docker", docker)
    return image, release, calls, image_info


def test_fake_lifecycle_retains_private_evidence_and_cleans_exact_owned_id(packet, fake_run):
    image, release, calls, _ = fake_run
    assert packet.verify(image, release) is True
    assert ("rm", "-f", "owned-container-id") in calls
    result_file = packet.ROOT / (".runtime-" + release) / "results.json"
    assert result_file.stat().st_mode & 0o777 == 0o600
    result = json.loads(result_file.read_text())
    assert result["passed"] is True and result["cleanup"] is True
    assert packet.ROOT.joinpath(".lock").exists()
    with pytest.raises(FileExistsError):
        packet.verify(image, release)


def test_invalid_owned_runtime_is_cleaned_but_never_passes(packet, fake_run, state):
    image, release, calls, _ = fake_run
    state["HostConfig"]["Privileged"] = True
    assert packet.verify(image, release) is False
    assert not any(args[0] == "start" for args in calls)
    assert ("rm", "-f", "owned-container-id") in calls


def test_foreign_container_is_never_removed(packet, fake_run, state):
    image, release, calls, _ = fake_run
    state["Config"]["Labels"]["com.docker.compose.project"] = "foreign"
    assert packet.verify(image, release) is False
    assert not any(args[0] in ("rm", "start", "exec") for args in calls)


@pytest.mark.parametrize("kind", ["foreign", "revision", "volumes", "bootstrap", "appenv"])
def test_unreviewed_image_refused_before_create(packet, fake_run, kind):
    image, release, calls, info = fake_run
    if kind == "foreign":
        info["Config"]["Labels"]["com.docker.compose.project"] = "foreign"
    elif kind == "revision":
        info["Config"]["Labels"]["org.opencontainers.image.revision"] = "b" * 40
    elif kind == "volumes":
        info["Config"]["Volumes"] = {"/data": {}}
    else:
        info["Config"]["Env"].append(
            "BWS_ACCESS_TOKEN=synthetic"
            if kind == "bootstrap"
            else "NOTEBOOK_DATABASE_URL=synthetic"
        )
    with pytest.raises(packet.PacketError):
        packet.verify(image, release)
    assert not any(args[0] == "create" for args in calls)


def test_contended_namespace_lock_blocks_before_docker(packet, fake_run):
    image, release, calls, _ = fake_run
    packet.ROOT.mkdir(mode=0o700)
    lock_path = packet.ROOT / ".lock"
    with lock_path.open("x+") as lock:
        lock_path.chmod(0o600)
        inode = lock_path.stat().st_ino
        packet.fcntl.flock(lock, packet.fcntl.LOCK_EX | packet.fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            packet.verify(image, release)
    assert calls == []
    assert lock_path.stat().st_ino == inode
