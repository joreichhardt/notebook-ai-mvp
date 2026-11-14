"""Synthetic build workflow/limits only; Docker/subprocess are never real here."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def builder():
    return load("readiness_builder", "build_image.py")


def test_build_flags_are_nonprivileged_bounded_and_tagless(builder):
    args = builder.build_args(Path("/snapshot"), "a" * 40, "own-net", Path("/own/image.id"))
    assert args[:3] == ["docker", "--host", "unix:///var/run/docker.sock"]
    for flag in ("--no-cache", "--force-rm", "--rm", "NOTEBOOK_TEST_LIMITS=1", "nproc=128:128"):
        assert flag in args
    assert args[args.index("--memory") + 1] == "1g"
    assert args[args.index("--cpu-quota") + 1] == "100000"
    assert args[args.index("--network") + 1] == "own-net"
    assert "--privileged" not in args and "--tag" not in args and "-t" not in args
    assert builder.build_environment(Path("/client")) == {
        "PATH": "/usr/bin:/bin",
        "HOME": "/client",
        "DOCKER_CONFIG": "/client",
        "DOCKER_BUILDKIT": "0",
    }


@pytest.mark.parametrize(
    "field,value",
    [("Id", "foreign"), ("Driver", "host"), ("Labels", {}), ("Containers", {"foreign": {}})],
)
def test_build_network_ownership_is_exact(builder, field, value):
    info = {
        "Id": "own",
        "Driver": "bridge",
        "Labels": {
            "com.docker.compose.project": builder.native.PROJECT,
            "notebook.test.sha": "a" * 40,
        },
        "Containers": {},
    }
    assert builder.network_owned(info, "own", "a" * 40)
    info[field] = value
    assert not builder.network_owned(info, "own", "a" * 40)


@pytest.mark.parametrize(
    "kind", ["valid", "memory", "swap", "cpu", "uid", "pids", "nofile", "caps", "seccomp"]
)
def test_effective_dependency_build_gate(tmp_path, monkeypatch, kind):
    limits = load("build_limits", "check_build_limits.py")
    values = {"memory.max": "1073741824", "memory.swap.max": "0", "cpu.max": "100000 100000"}
    if kind == "memory":
        values["memory.max"] = "max"
    if kind == "swap":
        values["memory.swap.max"] = "1"
    if kind == "cpu":
        values["cpu.max"] = "200000 100000"
    for name, value in values.items():
        (tmp_path / name).write_text(value)
    status = tmp_path / "status"
    status.write_text(
        f"CapEff:\t{'1' if kind == 'caps' else '0'}\nSeccomp:\t{'0' if kind == 'seccomp' else '2'}\n"
    )
    monkeypatch.setattr(limits.os, "geteuid", lambda: 0 if kind == "uid" else 10001)
    monkeypatch.setattr(limits.os, "getegid", lambda: 10001)

    def rlimit(which):
        if which == limits.resource.RLIMIT_NPROC:
            return (256, 256) if kind == "pids" else (128, 128)
        return (4096, 4096) if kind == "nofile" else (1024, 1024)

    monkeypatch.setattr(limits.resource, "getrlimit", rlimit)
    if kind == "valid":
        limits.check(tmp_path, status)
    else:
        with pytest.raises(RuntimeError, match="build_limits_failed"):
            limits.check(tmp_path, status)


@pytest.mark.parametrize(
    "failure", [None, "build", "marker", "foreign_network", "child", "runtime"]
)
def test_synthetic_build_lifecycle(builder, tmp_path, monkeypatch, failure):
    release, network, image = "a" * 40, "b" * 64, "sha256:" + "c" * 64
    root = tmp_path / "readiness"
    root.mkdir(mode=0o700)
    source = root / release
    source.mkdir(mode=0o700)
    script = source / "tests/security/readiness_image/build_image.py"
    script.parent.mkdir(parents=True)
    script.write_text("synthetic operator")
    monkeypatch.setattr(builder, "__file__", str(script))
    monkeypatch.setattr(builder.native, "ROOT", root)
    monkeypatch.setattr(builder.native, "require_operator", lambda: None)
    monkeypatch.setattr(builder, "require_capacity", lambda: {"synthetic": True})
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("network", "create"):
            return network
        if args[:2] == ("network", "inspect"):
            return json.dumps(
                [
                    {
                        "Id": network,
                        "Driver": "bridge",
                        "Labels": {
                            "com.docker.compose.project": "foreign"
                            if failure == "foreign_network"
                            else builder.native.PROJECT,
                            "notebook.test.sha": release,
                        },
                        "Containers": {},
                    }
                ]
            )
        if args[:2] == ("ps", "-aq") and "id=" in args[-1] and failure == "child":
            return "present"
        return ""

    monkeypatch.setattr(builder.native, "docker", docker)
    runtime_calls = []

    def verify(image_id, sha):
        runtime_calls.append((image_id, sha))
        return failure != "runtime"

    monkeypatch.setattr(builder.native, "verify_locked", verify)

    def run(args, **kwargs):
        assert kwargs["env"]["DOCKER_BUILDKIT"] == "0"
        assert "BWS_ACCESS_TOKEN" not in kwargs["env"]
        Path(args[args.index("--iidfile") + 1]).write_text(image)
        kwargs["stdout"].write(
            b" ---> Running in 111111111111\n ---> Running in 222222222222\n ---> Running in 333333333333\n"
        )
        if failure != "marker":
            kwargs["stdout"].write(b"NOTEBOOK_BUILD_LIMITS_OK\n")
        return SimpleNamespace(returncode=1 if failure == "build" else 0)

    monkeypatch.setattr(builder.subprocess, "run", run)
    assert builder.build(release) is (failure is None)
    result_path = root / (".build-" + release) / "results.json"
    assert result_path.stat().st_mode & 0o777 == 0o600
    assert json.loads(result_path.read_text())["passed"] is (failure is None)
    if failure in (None, "runtime"):
        assert runtime_calls == [(image, release)]
    else:
        assert runtime_calls == []
    if failure == "foreign_network":
        assert not any(args[:2] == ("network", "rm") for args in calls)
    else:
        assert ("network", "rm", network) in calls
    with pytest.raises(FileExistsError):
        builder.build(release)
