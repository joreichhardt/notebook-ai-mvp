"""Host allowlist contracts with synthetic files and mocked command execution only."""

import importlib.util
import io
import os
import tarfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from deploy.poc_setup import generate_secrets


def ops():
    path = Path(__file__).resolve().parents[1] / "private-ops/notebook-poc-deploy.py"
    spec = importlib.util.spec_from_file_location("poc_deploy", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def approval(m):
    return {
        "commit": "a" * 40,
        "archive_sha256": "b" * 64,
        "approved_at": 1800000000,
        "expires_at": 1800003600,
        "runtime_uid": 2001,
        "runtime_gid": 2001,
        "runner_uid": 2002,
        "postgres_uid": 100,
        "postgres_gid": 101,
        "compose_provider_sha256": "c" * 64,
        "compose_provider_version": "2.40.3+ds1-0ubuntu1",
        "bws_sha256": "d" * 64,
        "images": {
            "api": "ghcr.io/joreichhardt/notebook-ai-mvp-api@sha256:" + "1" * 64,
            "frontend": "ghcr.io/joreichhardt/notebook-ai-mvp-frontend@sha256:" + "2" * 64,
            "auth": m.AUTH_IMAGE,
            "db": m.DB_IMAGE,
        },
        "bws_manifest": {
            "server_url": "https://vault.example.invalid",
            "project_id": "00000000-0000-4000-8000-000000000099",
            "secrets": {
                name: f"00000000-0000-4000-8000-{index:012d}"
                for index, name in enumerate(sorted(m.RUNTIME_SECRET_NAMES), 1)
            },
        },
        "smtp_host": "mail.example.invalid",
    }


@pytest.mark.parametrize("roles", [("api",), ("frontend",), ("api", "frontend")])
def test_host_approval_rejects_old_poc_image_namespace(roles):
    m = ops()
    value = approval(m)
    for role in roles:
        value["images"][role] = value["images"][role].replace(
            "notebook-ai-mvp-", "notebook-ai-poc-"
        )
    with pytest.raises(m.DeploymentHold):
        m.validate_approval(value, "a" * 40, now=1800000010, uid=2001, gid=2001)


def test_explicit_root_approval_binds_vendor_identity_and_actor():
    m = ops()
    value = approval(m)
    assert m.validate_approval(value, "a" * 40, now=1800000010, uid=2001, gid=2001) is value


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "expired",
        "future",
        "commit",
        "root",
        "operator",
        "same_actor",
        "pg_uid",
        "pg_gid",
        "bool_uid",
        "provider",
        "service_secret",
        "http",
        "smtp_injection",
        "image_tag",
    ],
)
def test_bad_host_approval_fails_before_external_io(mutation):
    m = ops()
    value = approval(m)
    if mutation == "extra":
        value["command"] = "anything"
    elif mutation == "expired":
        value["expires_at"] = 1800000001
    elif mutation == "future":
        value["approved_at"] = 1800000100
    elif mutation == "commit":
        value["commit"] = "b" * 40
    elif mutation == "root":
        value["runtime_uid"] = 0
    elif mutation == "operator":
        value["runner_uid"] = 1001
    elif mutation == "same_actor":
        value["runner_uid"] = 2001
    elif mutation == "pg_uid":
        value["postgres_uid"] = 999
    elif mutation == "pg_gid":
        value["postgres_gid"] = 100
    elif mutation == "bool_uid":
        value["postgres_uid"] = True
    elif mutation == "provider":
        value["compose_provider_version"] = "latest"
    elif mutation == "service_secret":
        value["bws_manifest"]["secrets"]["notebook_supabase_service_role_key"] = (
            "00000000-0000-4000-8000-000000000098"
        )
    elif mutation == "http":
        value["bws_manifest"]["server_url"] = "http://vault.example.invalid"
    elif mutation == "smtp_injection":
        value["smtp_host"] = "mail.invalid\nBWS_ACCESS_TOKEN=x"
    elif mutation == "image_tag":
        value["images"]["api"] = "ghcr.io/joreichhardt/notebook-ai-mvp-api:latest"
    with pytest.raises(m.DeploymentHold):
        m.validate_approval(value, "a" * 40, now=1800000010, uid=2001, gid=2001)


def test_child_environment_has_no_inherited_credentials_or_socket(monkeypatch):
    m = ops()
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "canary")
    monkeypatch.setenv("DOCKER_HOST", "tcp://foreign.invalid")
    monkeypatch.setenv("HTTP_PROXY", "http://foreign.invalid")
    env = m.clean_environment(2001)
    assert set(env) == {
        "PATH",
        "HOME",
        "LANG",
        "XDG_RUNTIME_DIR",
        "DOCKER_HOST",
        "PODMAN_COMPOSE_PROVIDER",
    }
    assert env["DOCKER_HOST"] == "unix:///run/user/2001/podman/podman.sock"
    assert "canary" not in str(env)


@pytest.mark.parametrize(
    "mutation",
    [None, "shared_password", "superuser_dsn", "bad_signature", "smtp_newline", "extra_secret"],
)
def test_credentials_are_validated_before_service_changes(mutation):
    m = ops()
    values = generate_secrets(smtp_password="synthetic-smtp-password")
    del values["notebook_supabase_service_role_key"]
    if mutation == "shared_password":
        values["notebook_database_url"] = (
            "postgresql+psycopg://notebook_app:"
            + values["notebook_postgres_password"]
            + "@db:5432/postgres"
        )
    elif mutation == "superuser_dsn":
        values["notebook_database_url"] = values["notebook_database_url"].replace(
            "notebook_app", "postgres"
        )
    elif mutation == "bad_signature":
        values["notebook_auth_jwt_secret"] = "z" * 64
    elif mutation == "smtp_newline":
        values["notebook_smtp_password"] = "synthetic\n"
    elif mutation == "extra_secret":
        values["BWS_ACCESS_TOKEN"] = "synthetic"
    if mutation:
        with pytest.raises(m.DeploymentHold):
            m.validate_runtime_values(values)
    else:
        assert m.validate_runtime_values(values) is None


@pytest.mark.parametrize("kind", ["parent", "absolute", "symlink", "hardlink", "private_artifact"])
def test_archive_cannot_escape_or_publish_private_artifacts(tmp_path, kind):
    m = ops()
    archive = tmp_path / "source.tar"
    member = tarfile.TarInfo(
        {
            "parent": "../escape",
            "absolute": "/tmp/escape",
            "symlink": "link",
            "hardlink": "hardlink",
            "private_artifact": ".pi/proof",
        }[kind]
    )
    if kind in ("symlink", "hardlink"):
        member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
        member.linkname = "../escape"
    with tarfile.open(archive, "w") as stream:
        stream.addfile(member)
    target = tmp_path / "source"
    target.mkdir()
    with pytest.raises(m.DeploymentHold):
        m.extract_archive(archive, target)
    assert list(target.iterdir()) == []


def test_reviewed_regular_archive_extracts_only_local_bytes(tmp_path):
    m = ops()
    archive = tmp_path / "source.tar"
    member = tarfile.TarInfo("compose.poc.yaml")
    member.size = 4
    with tarfile.open(archive, "w") as stream:
        stream.addfile(member, io.BytesIO(b"test"))
    target = tmp_path / "source"
    target.mkdir()
    m.extract_archive(archive, target)
    assert (target / "compose.poc.yaml").read_bytes() == b"test"


def test_consumer_files_are_closed_private_and_uid_mapping_uses_unshare(tmp_path, monkeypatch):
    m = ops()
    raw, target = tmp_path / "raw", tmp_path / "consumers"
    raw.mkdir()
    for name in m.RUNTIME_SECRET_NAMES:
        (raw / name).write_text("synthetic-private-value")
    calls = Mock()
    monkeypatch.setattr(m, "run", calls)
    env = m.clean_environment(2001)
    m.stage_consumers(raw, target, postgres_uid=100, postgres_gid=101, env=env)
    expected = {
        "db": {"notebook_postgres_password", "notebook_auth_database_url"},
        "auth": {
            "notebook_auth_database_url",
            "notebook_auth_jwt_secret",
            "notebook_smtp_password",
        },
        "api": {"notebook_database_url", "notebook_supabase_anon_key"},
        "admin": {"notebook_postgres_password", "notebook_database_url"},
    }
    assert {p.name for p in target.iterdir()} == set(expected)
    for name, files in expected.items():
        assert (target / name).stat().st_mode & 0o777 == 0o700
        assert {p.name for p in (target / name).iterdir()} == files
        for path in (target / name).iterdir():
            assert path.stat().st_mode & 0o777 == 0o400
    assert calls.call_count == 4
    assert calls.call_args_list[0].args == (
        [
            m.PODMAN,
            "unshare",
            "/usr/bin/chown",
            "--recursive",
            "--no-dereference",
            "100:101",
            str(target / "db"),
        ],
        env,
    )
    assert os.getuid() == (target / "db").stat().st_uid  # No real chown ran.


def test_failed_process_cannot_echo_raw_diagnostics(monkeypatch):
    m = ops()
    result = Mock(returncode=1, stdout=b"secret-canary", stderr=b"secret-canary")
    monkeypatch.setattr(m.subprocess, "run", Mock(return_value=result))
    with pytest.raises(m.DeploymentHold, match="^Owned runtime command failed$"):
        m.run([m.PODMAN, "info"], m.clean_environment(2001))
