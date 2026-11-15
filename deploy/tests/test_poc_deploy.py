"""Deployment orchestration against owned files and fake host/process boundaries."""

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from deploy.poc_setup import generate_secrets
from deploy.tests.test_poc_ops import approval, ops


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    m = ops()
    assert m.HOME == Path("/var/lib/notebook-poc")
    assert m.APPROVAL == Path("/etc/notebook-poc/approved-release.json")
    assert m.ARCHIVES == Path("/var/lib/notebook-poc-approved")
    assert str(m.PROVIDER) == "/usr/libexec/docker/cli-plugins/docker-compose"
    assert m.PODMAN == "/usr/bin/podman"
    home, runtime, archives = (tmp_path / name for name in ("home", "runtime", "archives"))
    for path in (home, runtime, archives):
        path.mkdir(mode=0o700)
    (home / "bws-reader-token").write_text("synthetic-reader-token")
    (home / "bws-reader-token").chmod(0o600)
    config = approval(m)
    archive = archives / (config["commit"] + ".tar")
    compose = Path("compose.poc.yaml").read_bytes()
    assert b"127.0.0.1:18191:8080" in compose
    with tarfile.open(archive, "w") as stream:
        member = tarfile.TarInfo("compose.poc.yaml")
        member.size = len(compose)
        stream.addfile(member, io.BytesIO(compose))
    config["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    events = []
    state = SimpleNamespace(
        module=m,
        config=config,
        home=home,
        runtime=runtime,
        archive=archive,
        events=events,
        fail=None,
        wrong_provider=False,
        rootless=True,
    )
    values = generate_secrets(smtp_password="synthetic-smtp-password")
    del values["notebook_supabase_service_role_key"]

    def trusted(path, *, digest=None):
        events.append(("trusted", str(path), digest))
        if path == m.APPROVAL:
            return json.dumps(config).encode()
        data = archive.read_bytes() if path == archive else b"synthetic-binary"
        if path == archive and hashlib.sha256(data).hexdigest() != digest:
            raise m.DeploymentHold("Approved input digest mismatch")
        return data

    def private(path, uid):
        assert uid == 2001
        assert path in (home, runtime)
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
        events.append(("private", str(path)))

    def provision(manifest, token, raw, *, bws):
        assert manifest == config["bws_manifest"]
        assert token == home / "bws-reader-token"
        assert bws == str(m.BWS)
        assert "BWS_ACCESS_TOKEN" not in m.os.environ
        events.append(("fetch",))
        raw.mkdir(mode=0o700)
        for name, value in values.items():
            (raw / name).write_text(value)
            (raw / name).chmod(0o600)
        if state.fail == "fetch":
            raise m.ProvisionError("secret-canary")
        if state.fail == "validation":
            (raw / "notebook_database_url").write_text("secret-canary")

    validate = m.validate_runtime_values

    def validated(bundle):
        validate(bundle)
        events.append(("validated",))

    def run(argv, env, *, timeout=120):
        assert set(env) == {
            "PATH",
            "HOME",
            "LANG",
            "XDG_RUNTIME_DIR",
            "DOCKER_HOST",
            "PODMAN_COMPOSE_PROVIDER",
        }
        assert env["DOCKER_HOST"] == "unix:///run/user/2001/podman/podman.sock"
        assert env["PODMAN_COMPOSE_PROVIDER"] == str(m.PROVIDER)
        events.append(("run", tuple(argv)))
        if argv == [str(m.PROVIDER), "version", "--short"]:
            return b"wrong" if state.wrong_provider else b"2.40.3+ds1-0ubuntu1"
        if argv == [m.PODMAN, "info", "--format", "json"]:
            return json.dumps({"host": {"security": {"rootless": state.rootless}}}).encode()
        if argv[:3] == [m.PODMAN, "unshare", "/usr/bin/chown"]:
            assert any(event[0] == "validated" for event in events)
            if (
                state.fail == "mapping"
                and argv[-2] == "10001:10001"
                or state.fail == "stage_admin"
                and Path(argv[-1]).name == "admin"
            ):
                raise m.DeploymentHold("Owned runtime command failed")
            return b""
        if argv[:3] == [m.PODMAN, "unshare", "/usr/bin/rm"]:
            assert Path(argv[-1]).is_relative_to(runtime)
            if state.fail == "cleanup":
                raise m.DeploymentHold("secret-canary")
            if state.fail == "cleanup_incomplete":
                return b""
            shutil.rmtree(argv[-1])
            return b""
        assert argv[:2] == [m.PODMAN, "compose"]
        assert argv[2] == "--env-file" and argv[4:6] == ["--project-name", "notebook-poc"]
        assert argv[6] == "-f"
        source = Path(argv[7]).parent
        assert source.is_relative_to(home)
        assert (source / "compose.poc.yaml").read_bytes() == compose
        env_text = Path(argv[3]).read_text()
        assert "BWS_ACCESS_TOKEN" not in env_text and "synthetic" not in env_text
        assert config["images"]["api"] in env_text and config["images"]["frontend"] in env_text
        assert not list(runtime.glob("*/raw"))
        assert (
            len([e for e in events if e[0] == "run" and e[1][1:3] == ("unshare", "/usr/bin/chown")])
            == 4
        )
        action = argv[8:]
        if action == ["up", "-d", "--no-build", "db", "auth"]:
            pending = home / "deployment-pending.json"
            record = json.loads(pending.read_text())
            assert stat.S_IMODE(pending.stat().st_mode) == 0o600
            assert record == {
                "commit": config["commit"],
                "phase": "service-changes-pending",
                "admin_containers": {
                    name: f"notebook-poc-admin-{source.name}-{name}"
                    for name in ("migrate", "activate-api-role")
                },
                "consumer_directory": str(next(runtime.glob("*/consumers"))),
            }
            assert [event for event in events if event[0] == "fsync"] == [
                ("fsync", "file"),
                ("fsync", "directory"),
            ]
        if action == ["config", "--quiet"] and state.fail == "config":
            raise m.DeploymentHold("Owned runtime command failed")
        if action == ["pull"] and state.fail == "pull":
            raise m.DeploymentHold("Owned runtime command failed")
        if action[:3] == ["up", "-d", "--no-build"] and state.fail == "startup":
            raise m.DeploymentHold("Owned runtime command failed")
        if action[:1] == ["run"] and state.fail == "migration":
            raise m.DeploymentHold("Owned runtime command failed")
        if action == ["up", "-d", "--no-build"] and state.fail == "final_up":
            raise m.DeploymentHold("Owned runtime command failed")
        return b""

    original_lstat = Path.lstat

    def lstat(path):
        if path == runtime / "podman/podman.sock":
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=2001)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(
        m, "Path", lambda value: runtime if value == "/run/user/2001" else Path(value)
    )
    monkeypatch.setattr(m, "HOME", home)
    monkeypatch.setattr(m, "ARCHIVES", archives)
    monkeypatch.setattr(m, "trusted_file", trusted)
    monkeypatch.setattr(m, "private_directory", private)
    monkeypatch.setattr(m, "provision", provision)
    monkeypatch.setattr(m, "validate_runtime_values", validated)
    monkeypatch.setattr(m, "run", run)
    # Substitute the module's OS view, never change the test process identity/env.
    fake_os = SimpleNamespace(**{name: getattr(os, name) for name in dir(os)})
    fake_os.getuid = lambda: 2001
    fake_os.getgid = lambda: 2001
    fake_os.environ = {"BWS_ACCESS_TOKEN": "inherited-canary", "HTTP_PROXY": "foreign"}

    def fsync(fd):
        kind = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        events.append(("fsync", kind))
        if state.fail == "arm_file_fsync" and kind == "file":
            raise OSError("synthetic-private-error")
        if state.fail == "arm_directory_fsync" and kind == "directory":
            raise OSError("synthetic-private-error")
        if (
            state.fail == "clear_fsync"
            and kind == "directory"
            and not (home / "deployment-pending.json").exists()
        ):
            raise OSError("synthetic-private-error")
        os.fsync(fd)

    fake_os.fsync = fsync
    monkeypatch.setattr(m, "os", fake_os)
    monkeypatch.setattr(m.time, "time", lambda: 1800000010)
    return state


def commands(state):
    return [event[1] for event in state.events if event[0] == "run"]


def test_deploy_orders_validated_closed_consumers_before_service_changes(deployment):
    s = deployment
    s.module.deploy(s.config["commit"])
    calls = commands(s)
    compose = [call[8:] for call in calls if call[1] == "compose"]
    assert compose[:3] == [
        ("config", "--quiet"),
        ("pull",),
        ("up", "-d", "--no-build", "db", "auth"),
    ]
    assert [call[-1] for call in compose[3:5]] == ["migrate", "activate-api-role"]
    assert compose[-1] == ("up", "-d", "--no-build")
    assert len(compose) == 6
    assert all(call[0] in (s.module.PODMAN, str(s.module.PROVIDER)) for call in calls)
    assert not list(s.runtime.glob("*/consumers/admin"))
    record = list(s.home.glob("release-*/started.json"))
    assert len(record) == 1
    assert json.loads(record[0].read_text()) == {
        "commit": s.config["commit"],
        "profile": "login-workspace-v1",
        "publication": False,
    }
    assert "BWS_ACCESS_TOKEN" not in s.module.os.environ
    assert not (s.home / "deployment-pending.json").exists()
    assert [event for event in s.events if event[0] == "fsync"] == [
        ("fsync", "file"),
        ("fsync", "directory"),
        ("fsync", "directory"),
    ]


@pytest.mark.parametrize(
    "failure",
    [
        "fetch",
        "validation",
        "mapping",
        "stage_admin",
        "config",
        "pull",
        "startup",
        "migration",
        "final_up",
    ],
)
def test_deploy_failure_preserves_unknown_authority_without_claiming_rollback(
    deployment, monkeypatch, capsys, failure
):
    s = deployment
    s.fail = failure
    monkeypatch.setattr(s.module.sys, "argv", ["helper", s.config["commit"]])
    assert s.module.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == "PoC deployment held; no public route changed. Operator inspection required.\n"
    )
    assert not list(s.home.glob("release-*/started.json"))
    assert not list(s.runtime.glob("*/raw"))
    assert bool(list(s.runtime.glob("*/consumers/admin"))) is (failure == "migration")
    assert (s.home / "deployment-pending.json").exists() is (
        failure in ("startup", "migration", "final_up")
    )
    calls = commands(s)
    if failure in ("fetch", "validation", "mapping"):
        assert not any(call[1] == "compose" for call in calls)
    assert not any("down" in call or "rollback" in call for call in calls)
    # Partial owned source/runtime state is retained, not falsely called restored.
    assert list(s.home.glob("release-*"))


@pytest.mark.parametrize("failure", ["archive", "provider", "rootful", "actor"])
def test_deploy_preflight_rejects_before_fetch_or_service_change(deployment, failure):
    s = deployment
    if failure == "archive":
        s.config["archive_sha256"] = "0" * 64
    elif failure == "provider":
        s.wrong_provider = True
    elif failure == "rootful":
        s.rootless = False
    else:
        s.config["runtime_uid"] = 2002
    with pytest.raises(s.module.DeploymentHold):
        s.module.deploy(s.config["commit"])
    assert not any(event[0] == "fetch" for event in s.events)
    assert not any(call[1] == "compose" for call in commands(s))


@pytest.mark.parametrize("kind", ["owner", "mode", "symlink"])
def test_trusted_file_rejects_untrusted_path_before_open(monkeypatch, kind):
    m = ops()
    mode = stat.S_IFREG | (0o666 if kind == "mode" else 0o644)
    if kind == "symlink":
        mode = stat.S_IFLNK | 0o777
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda path: SimpleNamespace(st_uid=1000 if kind == "owner" else 0, st_mode=mode),
    )
    opened = Mock(side_effect=AssertionError("must not open"))
    monkeypatch.setattr(m.os, "open", opened)
    with pytest.raises(m.DeploymentHold):
        m.trusted_file(Path("/approved"))
    opened.assert_not_called()


@pytest.mark.parametrize("kind", ["owner", "mode"])
def test_trusted_file_rechecks_open_descriptor_metadata(tmp_path, monkeypatch, kind):
    m = ops()
    path = tmp_path / "approved"
    path.write_bytes(b"synthetic-approved-input")
    monkeypatch.setattr(
        Path, "lstat", lambda path: SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644)
    )
    fake_os = SimpleNamespace(**{name: getattr(os, name) for name in dir(os)})
    original_fstat = os.fstat

    def fstat(fd):
        metadata = list(original_fstat(fd))
        metadata[0] = stat.S_IFREG | (0o666 if kind == "mode" else 0o644)
        metadata[4] = 1000 if kind == "owner" else 0
        return os.stat_result(metadata)

    fake_os.fstat = fstat
    monkeypatch.setattr(m, "os", fake_os)
    with pytest.raises(m.DeploymentHold):
        m.trusted_file(path)


@pytest.mark.parametrize("failure", ["cleanup", "cleanup_incomplete"])
def test_deploy_cleanup_failure_is_held_once_with_partial_state(
    deployment, monkeypatch, capsys, failure
):
    s = deployment
    s.fail = failure
    monkeypatch.setattr(s.module.sys, "argv", ["helper", s.config["commit"]])
    assert s.module.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert (
        output.err
        == "PoC deployment held; no public route changed. Operator inspection required.\n"
    )
    assert len(list(s.runtime.glob("*/consumers/admin"))) == 1
    assert (s.home / "deployment-pending.json").is_file()
    assert not list(s.home.glob("release-*/started.json"))
    calls = commands(s)
    assert sum(call[1:3] == ("unshare", "/usr/bin/rm") for call in calls) == 1
    assert not any(
        call[1] == "compose" and call[8:] == ("up", "-d", "--no-build") for call in calls
    )
    assert not any("down" in call or "rollback" in call for call in calls)


def test_deploy_binds_every_host_input_and_exact_admin_commands(deployment):
    s = deployment
    s.module.deploy(s.config["commit"])
    assert [event for event in s.events if event[0] == "trusted"] == [
        ("trusted", str(s.module.APPROVAL), None),
        ("trusted", str(s.archive), s.config["archive_sha256"]),
        ("trusted", str(s.module.PROVIDER), s.config["compose_provider_sha256"]),
        ("trusted", str(s.module.BWS), s.config["bws_sha256"]),
        ("trusted", s.module.PODMAN, None),
    ]
    calls = commands(s)
    admin_calls = [call for call in calls if call[1] == "compose" and call[8] == "run"]
    for call, action in zip(admin_calls, ("migrate", "activate-api-role"), strict=True):
        source = Path(call[7]).parent
        admin = next(s.runtime.glob("*/consumers")) / "admin"
        assert call[8:] == (
            "run",
            "--rm",
            "--no-deps",
            "--name",
            f"notebook-poc-admin-{source.name}-{action}",
            "--entrypoint",
            "python",
            "--env",
            "PYTHONPATH=/app",
            "--volume",
            str(source / "deploy") + ":/app/deploy:ro",
            "--volume",
            str(source / "docker/poc-admin.py") + ":/opt/poc-admin.py:ro",
            "--volume",
            str(admin) + ":/run/poc-admin:ro",
            "api",
            "/opt/poc-admin.py",
            action,
        )
    assert sum(call[1:3] == ("unshare", "/usr/bin/rm") for call in calls) == 1
    assert all("synthetic" not in str(call) for call in calls)


@pytest.mark.parametrize("operation", ["initial_up", "migrate", "activate-api-role"])
@pytest.mark.parametrize("failure", ["timeout", "transport", "nonzero"])
def test_deploy_blocks_next_invocation_while_daemon_admin_survives_cli(
    deployment, monkeypatch, failure, operation
):
    s = deployment
    daemon = SimpleNamespace(active=False, authority_loaded=False, launches=0)
    real_boundary = ops()
    fixture_run = s.module.run

    def subprocess_run(argv, **kwargs):
        assert argv[1] == "compose"
        assert kwargs["timeout"] == (120 if operation == "initial_up" else 180)
        assert kwargs["check"] is False
        daemon.active = True
        daemon.authority_loaded = operation != "initial_up"
        daemon.launches += 1
        if failure == "timeout":
            raise subprocess.TimeoutExpired(
                argv, kwargs["timeout"], output=b"synthetic-private-error"
            )
        if failure == "nonzero":
            return SimpleNamespace(returncode=1, stdout=b"synthetic-private-error")
        raise OSError("synthetic-private-error")

    monkeypatch.setattr(
        real_boundary,
        "subprocess",
        SimpleNamespace(
            run=subprocess_run,
            SubprocessError=subprocess.SubprocessError,
        ),
    )

    def run(argv, env, *, timeout=120):
        if argv[1] == "compose" and (
            operation == "initial_up"
            and argv[8:] == ["up", "-d", "--no-build", "db", "auth"]
            or argv[8] == "run"
            and argv[-1] == operation
        ):
            assert (s.home / "deployment-pending.json").is_file()
            return real_boundary.run(argv, env, timeout=timeout)
        return fixture_run(argv, env, timeout=timeout)

    monkeypatch.setattr(s.module, "run", run)
    with pytest.raises(real_boundary.DeploymentHold):
        s.module.deploy(s.config["commit"])
    # CLI failure and unlinking host files do not revoke loaded DB authority.
    assert daemon.active and daemon.launches == 1
    assert daemon.authority_loaded is (operation != "initial_up")
    assert bool(list(s.runtime.glob("*/consumers/admin"))) is (operation != "initial_up")
    assert (s.home / "deployment-pending.json").is_file()
    s.events.clear()
    # A fresh invocation reacquires the real filesystem flock. No fake daemon
    # state is cleared when the CLI exits or the invocation releases that lock.
    with pytest.raises((s.module.DeploymentHold, real_boundary.DeploymentHold)):
        s.module.deploy(s.config["commit"])
    assert not any(call[1] == "compose" for call in commands(s))
    assert daemon.active and daemon.launches == 1
    assert daemon.authority_loaded is (operation != "initial_up")


@pytest.mark.parametrize("kind", ["record", "malformed", "empty", "symlink", "directory"])
def test_deploy_existing_pending_node_blocks_before_fetch(deployment, kind):
    s = deployment
    pending = s.home / "deployment-pending.json"
    if kind == "symlink":
        pending.symlink_to(s.home / "absent-target")
    elif kind == "directory":
        pending.mkdir(mode=0o700)
    else:
        pending.write_text(
            {
                "record": '{"phase":"service-changes-pending"}',
                "malformed": "broken-json",
                "empty": "",
            }[kind]
        )
        pending.chmod(0o600)
    with pytest.raises(s.module.DeploymentHold, match="Unresolved deployment"):
        s.module.deploy(s.config["commit"])
    assert not list(s.home.glob("release-*"))
    assert not any(event[0] == "fetch" for event in s.events)
    assert not any(call[1] == "compose" for call in commands(s))
    assert pending.is_symlink() or pending.exists()


@pytest.mark.parametrize("failure", ["arm_file_fsync", "arm_directory_fsync"])
def test_deploy_pending_durability_failure_prevents_launch_and_stays_closed(
    deployment, monkeypatch, capsys, failure
):
    s = deployment
    s.fail = failure
    monkeypatch.setattr(s.module.sys, "argv", ["helper", s.config["commit"]])
    assert s.module.main() == 1
    output = capsys.readouterr()
    assert output.out == "" and "synthetic" not in output.err
    assert (s.home / "deployment-pending.json").is_file()
    assert not list(s.runtime.glob("*/consumers/admin"))
    assert not any(call[1] == "compose" and call[8] in ("up", "run") for call in commands(s))
    s.events.clear()
    with pytest.raises(s.module.DeploymentHold, match="Unresolved deployment"):
        s.module.deploy(s.config["commit"])
    assert not any(call[1] == "compose" for call in commands(s))


@pytest.mark.parametrize("failure", ["clear_unlink", "clear_fsync", "started_record"])
def test_deploy_completion_io_failure_is_held_without_false_sticky_claim(
    deployment, monkeypatch, capsys, failure
):
    s = deployment
    s.fail = failure
    pending = s.home / "deployment-pending.json"
    original_unlink, original_write = Path.unlink, Path.write_text

    def unlink(path, *args, **kwargs):
        if path == pending and failure == "clear_unlink":
            raise OSError("synthetic-private-error")
        return original_unlink(path, *args, **kwargs)

    def write(path, *args, **kwargs):
        if path.name == "started.json" and failure == "started_record":
            raise OSError("synthetic-private-error")
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(Path, "write_text", write)
    monkeypatch.setattr(s.module.sys, "argv", ["helper", s.config["commit"]])
    assert s.module.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert (
        output.err
        == "PoC deployment held; no public route changed. Operator inspection required.\n"
    )
    # Post-unlink directory-fsync failure cannot promise that a marker remains.
    # Both admin calls already acknowledged completion before this boundary.
    assert pending.exists() is (failure != "clear_fsync")
    assert not list(s.runtime.glob("*/consumers/admin"))
    admin = [call for call in commands(s) if call[1] == "compose" and call[8] == "run"]
    assert [call[-1] for call in admin] == ["migrate", "activate-api-role"]
    assert bool(list(s.home.glob("release-*/started.json"))) is (failure != "started_record")
