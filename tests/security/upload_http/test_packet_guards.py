"""No-DB guards for the opt-in B5 packet; synthetic filesystem only."""

import asyncio
import socket

import psycopg
import pytest
import test_live_flow as flow
from sqlalchemy.engine import Engine


@pytest.fixture(autouse=True)
def no_network_or_database(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("B5 local guard attempted external I/O", pytrace=False)

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(Engine, "connect", forbidden)


def test_missing_opt_in_never_inspects_credentials(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("disabled packet touched credentials")

    monkeypatch.setattr(flow.Path, "lstat", forbidden)
    with pytest.raises(flow.PacketRejected, match="b5_opt_in_required"):
        flow.configuration({}, root=tmp_path)


@pytest.fixture
def packet_dir(tmp_path):
    tmp_path.chmod(0o700)
    for name in (
        "notebook_app.pgpass",
        "a5_phase_a_io.pgpass",
        "supabase_admin.pgpass",
        "service.jwt",
    ):
        target = tmp_path / name
        target.write_text("synthetic-canary-not-a-credential")
        target.chmod(0o600)
    return tmp_path


def test_exact_fixed_contract_has_no_url_secrets(packet_dir):
    config = flow.configuration({"B5_UPLOAD_HTTP": "isolated-stage-b-approved"}, root=packet_dir)
    require_roles = ("notebook_app", "a5_phase_a_io", "supabase_admin")
    assert tuple(config.passfiles) == require_roles
    assert config.token_path == packet_dir / "service.jwt"
    assert config.endpoint == "http://gateway:10000/storage/v1"
    assert config.bucket == "a5-private"
    for role in require_roles:
        url = flow.database_url(role)
        assert url.host == "db" and url.port == 5432 and url.database == "postgres"
        assert url.username == role and url.password is None and not url.query
    assert "synthetic-canary" not in repr(config)


@pytest.mark.parametrize(
    "name",
    [
        "PGPASSWORD",
        "PGPASSFILE",
        "PGOPTIONS",
        "PGSERVICE",
        "PGHOST",
        "NOTEBOOK_DATABASE_URL_FILE",
        "NOTEBOOK_TEST_POSTGRES_URL",
        "DATABASE_URL",
        "SERVICE_KEY",
        "ANON_KEY",
        "AUTH_JWT_SECRET",
    ],
)
def test_inherited_libpq_configuration_rejected_before_files(name, tmp_path):
    with pytest.raises(flow.PacketRejected, match="b5_inherited_configuration"):
        flow.configuration(
            {"B5_UPLOAD_HTTP": "isolated-stage-b-approved", name: "synthetic-canary"},
            root=tmp_path / "absent",
        )


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o400])
def test_passfile_permissions_fail_closed(packet_dir, mode):
    (packet_dir / "notebook_app.pgpass").chmod(mode)
    with pytest.raises(flow.PacketRejected, match="b5_private_file_required"):
        flow.configuration({"B5_UPLOAD_HTTP": "isolated-stage-b-approved"}, root=packet_dir)


def test_symlink_file_rejected(packet_dir):
    target = packet_dir / "service.jwt"
    target.unlink()
    target.symlink_to(packet_dir / "notebook_app.pgpass")
    with pytest.raises(flow.PacketRejected, match="b5_private_file_required"):
        flow.configuration({"B5_UPLOAD_HTTP": "isolated-stage-b-approved"}, root=packet_dir)


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o777])
def test_shared_secret_directory_rejected(packet_dir, mode):
    packet_dir.chmod(mode)
    with pytest.raises(flow.PacketRejected, match="b5_private_directory_required"):
        flow.configuration({"B5_UPLOAD_HTTP": "isolated-stage-b-approved"}, root=packet_dir)


def test_symlink_directory_rejected(packet_dir):
    alias = packet_dir.parent / "alias"
    alias.symlink_to(packet_dir, target_is_directory=True)
    with pytest.raises(flow.PacketRejected, match="b5_private_directory_required"):
        flow.configuration({"B5_UPLOAD_HTTP": "isolated-stage-b-approved"}, root=alias)


def test_engine_configuration_is_explicit_bounded_and_no_connect(packet_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(flow, "create_engine", lambda *args, **kwargs: calls.append((args, kwargs)))
    flow.make_engine("notebook_app", packet_dir / "notebook_app.pgpass")
    args, kwargs = calls[0]
    assert args == (flow.database_url("notebook_app"),)
    assert kwargs == {
        "hide_parameters": True,
        "pool_size": 1,
        "max_overflow": 0,
        "pool_timeout": 1,
        "connect_args": {
            "passfile": str(packet_dir / "notebook_app.pgpass"),
            "connect_timeout": 3,
            "options": "-c statement_timeout=3000 -c lock_timeout=1000",
        },
    }


@pytest.mark.parametrize("value", ["", "1", "true", "approved"])
def test_nonexact_flags_do_not_opt_in(value, tmp_path):
    with pytest.raises(flow.PacketRejected, match="b5_opt_in_required"):
        flow.configuration({"B5_UPLOAD_HTTP": value}, root=tmp_path / "absent")


def test_opaque_reporter_never_formats_or_chains_driver_error(capsys):
    class Hostile(RuntimeError):
        def __str__(self) -> str:
            raise AssertionError("source exception formatted")

    def fail():
        raise Hostile("SYNTHETIC_DRIVER_CANARY")

    with pytest.raises(pytest.fail.Exception) as error:
        flow.opaque(fail)
    assert str(error.value) == "b5_live_flow_failed"
    assert error.value.__context__ is None
    output = capsys.readouterr()
    assert not output.out and not output.err


def test_opaque_reporter_propagates_cancellation():
    def cancel():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        flow.opaque(cancel)


def test_unknown_stage_cannot_print_source_value(capsys):
    with pytest.raises(flow.PacketRejected, match="b5_unknown_stage"):
        flow.stage("SYNTHETIC_STAGE_CANARY")
    assert not capsys.readouterr().out
