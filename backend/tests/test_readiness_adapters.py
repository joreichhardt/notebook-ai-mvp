"""Synthetic transports/DB and owned temporary files; never live credentials."""

import asyncio
import json
import socket
import threading
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from app import readiness
from app.documents.storage import StorageConfig
from app.health import ProbeUnavailable
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external I/O forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.delenv("BWS_ACCESS_TOKEN", raising=False)


def response(value, status=200, headers=None):
    return httpx.Response(
        status,
        headers={"Content-Type": "application/json", **(headers or {})},
        stream=httpx.ByteStream(json.dumps(value).encode()),
    )


def test_database_uses_independent_readonly_bounded_connection(monkeypatch):
    factory = MagicMock()
    connection = factory.return_value.connect.return_value.__enter__.return_value
    connection.scalar.return_value = True
    monkeypatch.setattr(readiness, "create_engine", factory)
    probe = readiness.database_probe(SecretStr("postgresql://notebook_app@db/test"))
    factory.return_value.connect.assert_not_called()  # construction never connects
    assert asyncio.run(probe()) is True
    options = factory.call_args.kwargs
    assert options["poolclass"] is readiness.NullPool
    assert options["hide_parameters"] is True and options["echo"] is False
    assert options["connect_args"] == {
        "connect_timeout": 1,
        "options": "-c default_transaction_read_only=on -c statement_timeout=500 -c lock_timeout=250",
    }
    connection.scalar.assert_called_once()
    compiled = connection.scalar.call_args.args[0].compile()
    sql = str(compiled)
    assert sql.startswith("SELECT ")
    assert "SESSION_USER = CURRENT_USER" in sql
    assert "NOT pg_catalog.pg_roles.rolbypassrls" in sql
    assert "notebook_app" not in sql
    assert "notebook_app" in compiled.params.values()
    factory.return_value.connect.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_database_requires_literal_true(monkeypatch, value):
    factory = MagicMock()
    factory.return_value.connect.return_value.__enter__.return_value.scalar.return_value = value
    monkeypatch.setattr(readiness, "create_engine", factory)
    assert (
        asyncio.run(readiness.database_probe(SecretStr("postgresql://notebook_app@db/test"))())
        is False
    )


def test_database_failures_do_not_escape_with_diagnostics(monkeypatch):
    factory = MagicMock()
    factory.return_value.connect.side_effect = OperationalError(
        "private SQL", {}, RuntimeError("private DSN")
    )
    monkeypatch.setattr(readiness, "create_engine", factory)
    with pytest.raises(ProbeUnavailable) as error:
        asyncio.run(readiness.database_probe(SecretStr("postgresql://notebook_app@db/test"))())
    assert str(error.value) == ""
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "url",
    [
        "",
        "sqlite:///test",
        "mysql://db/test",
        "postgresql://db/test",
        "postgresql://postgres@db/test",
        "postgresql://notebook_app@/test",
        "postgresql://notebook_app@db",
    ],
)
def test_database_rejects_unsupported_configuration(url):
    with pytest.raises(ProbeUnavailable):
        readiness.database_probe(SecretStr(url))


def test_auth_probe_uses_only_reviewed_get_and_exact_version(monkeypatch):
    calls = []
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")

    def handler(request):
        calls.append(request)
        return response({"name": "GoTrue", "version": "synthetic-version"})

    probe = readiness.auth_probe(
        "https://auth.example.test/",
        SecretStr("synthetic"),
        expected_version="synthetic-version",
        transport=httpx.MockTransport(handler),
    )
    assert asyncio.run(probe()) is True
    assert len(calls) == 1
    assert str(calls[0].url) == "https://auth.example.test/auth/v1/health"
    assert calls[0].method == "GET"
    assert calls[0].headers["apikey"] == "synthetic"
    assert "authorization" not in calls[0].headers
    assert "cookie" not in calls[0].headers


@pytest.mark.parametrize(
    "value", [{}, {"name": "different", "version": "v"}, {"name": "GoTrue", "version": "old"}]
)
def test_auth_mismatch_is_not_ready(value):
    probe = readiness.auth_probe(
        "https://auth.example.test",
        SecretStr("synthetic"),
        expected_version="v",
        transport=httpx.MockTransport(lambda request: response(value)),
    )
    assert asyncio.run(probe()) is False


@pytest.mark.parametrize(
    "origin",
    [
        "http://auth.example.test",
        "https://auth.example.test/auth/v1",
        "https://auth.example.test?query=x",
        "https://auth.example.test/#fragment",
    ],
)
def test_auth_requires_explicit_trusted_origin(origin):
    with pytest.raises(ProbeUnavailable):
        readiness.auth_probe(origin, SecretStr("synthetic"), expected_version="v")


def test_explicit_internal_auth_http_is_allowed():
    probe = readiness.auth_probe(
        "http://auth:9999",
        SecretStr("synthetic"),
        expected_version="v",
        allow_internal_http=True,
        transport=httpx.MockTransport(lambda request: response({"name": "GoTrue", "version": "v"})),
    )
    assert asyncio.run(probe()) is True


@pytest.mark.parametrize("status", [301, 302, 401, 403, 500, 503])
def test_http_status_is_not_followed_or_retried(status):
    calls = []

    def handler(request):
        calls.append(request)
        return response({}, status=status, headers={"Location": "https://other.example.test"})

    with pytest.raises(ProbeUnavailable):
        asyncio.run(
            readiness.auth_probe(
                "https://auth.example.test",
                SecretStr("synthetic"),
                expected_version="v",
                transport=httpx.MockTransport(handler),
            )()
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "headers,body",
    [
        ({"Content-Type": "text/html"}, b"private diagnostics"),
        ({"Content-Type": "application/jsonbad"}, b"{}"),
        ({"Content-Encoding": "gzip"}, b"compressed"),
        ({}, b"{"),
        ({}, b"[]"),
        ({}, b"x" * 4097),
    ],
)
def test_bounded_json_failures_are_opaque(headers, body):
    def handler(request):
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json", **headers},
            stream=httpx.ByteStream(body),
        )

    probe = readiness.auth_probe(
        "https://auth.example.test",
        SecretStr("synthetic"),
        expected_version="v",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProbeUnavailable) as error:
        asyncio.run(probe())
    assert str(error.value) == ""


@pytest.mark.parametrize("public", [True, None, 0, "false"])
def test_storage_requires_literal_private_bucket(public):
    config = StorageConfig(
        "https://storage.example.test/storage/v1", "notebook", SecretStr("synthetic")
    )
    probe = readiness.storage_probe(
        config,
        transport=httpx.MockTransport(
            lambda request: response({"id": "notebook", "public": public})
        ),
    )
    assert asyncio.run(probe()) is False


def test_storage_reads_only_exact_bucket_metadata():
    calls = []

    def handler(request):
        calls.append(request)
        return response({"id": "notebook", "public": False})

    config = StorageConfig(
        "https://storage.example.test/storage/v1", "notebook", SecretStr("synthetic")
    )
    assert (
        asyncio.run(readiness.storage_probe(config, transport=httpx.MockTransport(handler))())
        is True
    )
    assert len(calls) == 1 and calls[0].method == "GET"
    assert calls[0].url.path == "/storage/v1/bucket/notebook"
    assert calls[0].headers["authorization"] == "Bearer synthetic"


def test_secret_files_only_assigned_private_consumer_files(tmp_path):
    path = tmp_path / "consumer"
    path.write_bytes(b"synthetic")
    path.chmod(0o400)
    assert asyncio.run(readiness.secret_files_probe([path])()) is True
    path.chmod(0o600)
    assert asyncio.run(readiness.secret_files_probe([path])()) is True
    assert asyncio.run(readiness.secret_files_probe([])()) is False


@pytest.mark.parametrize(
    "mode,value",
    [
        (0o644, b"synthetic"),
        (0o640, b"synthetic"),
        (0o700, b"synthetic"),
        (0o600, b""),
        (0o600, b" \n"),
        (0o600, b"x" * 65537),
    ],
)
def test_secret_files_reject_bad_permissions_or_contents(tmp_path, mode, value):
    path = tmp_path / "consumer"
    path.write_bytes(value)
    path.chmod(mode)
    assert asyncio.run(readiness.secret_files_probe([path])()) is False


def test_secret_symlink_and_missing_file_are_unavailable(tmp_path):
    target = tmp_path / "consumer"
    target.write_bytes(b"synthetic")
    target.chmod(0o400)
    alias = tmp_path / "alias"
    alias.symlink_to(target)
    for path in (alias, tmp_path / "absent"):
        with pytest.raises(ProbeUnavailable):
            asyncio.run(readiness.secret_files_probe([path])())


def test_bootstrap_environment_is_not_accepted_by_application_probe(tmp_path, monkeypatch):
    path = tmp_path / "consumer"
    path.write_bytes(b"synthetic")
    path.chmod(0o400)
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "synthetic")
    assert asyncio.run(readiness.secret_files_probe([path])()) is False


def test_database_cancellation_does_not_allow_another_outstanding_connection(monkeypatch):
    started, release = threading.Event(), threading.Event()
    factory = MagicMock()
    connection = factory.return_value.connect.return_value.__enter__.return_value

    def blocked(statement):
        started.set()
        assert release.wait(3)
        return True

    connection.scalar.side_effect = blocked
    monkeypatch.setattr(readiness, "create_engine", factory)
    probe = readiness.database_probe(SecretStr("postgresql://notebook_app@db/test"))

    async def exercise():
        first = asyncio.create_task(probe())
        try:
            async with asyncio.timeout(1):
                while not started.is_set():
                    await asyncio.sleep(0.001)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert await probe() is False
            assert factory.return_value.connect.call_count == 1
        finally:
            release.set()

    asyncio.run(exercise())  # joins the finite executor thread before returning


def test_http_cancellation_closes_the_upstream_stream():
    closed = []

    async def exercise():
        entered = asyncio.Event()

        class Hanging(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"{"
                entered.set()
                await asyncio.Event().wait()

            async def aclose(self):
                closed.append(True)

        probe = readiness.auth_probe(
            "https://auth.example.test",
            SecretStr("synthetic"),
            expected_version="v",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    stream=Hanging(),
                )
            ),
        )
        task = asyncio.create_task(probe())
        async with asyncio.timeout(1):
            await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert closed


def test_http_timeout_has_no_raw_diagnostics():
    def timeout(request):
        raise httpx.ReadTimeout("synthetic private upstream diagnostics")

    probe = readiness.auth_probe(
        "https://auth.example.test",
        SecretStr("synthetic"),
        expected_version="v",
        transport=httpx.MockTransport(timeout),
    )
    with pytest.raises(ProbeUnavailable) as error:
        asyncio.run(probe())
    assert str(error.value) == "" and error.value.__suppress_context__


def test_secret_probe_rejects_relative_paths():
    with pytest.raises(ProbeUnavailable):
        readiness.secret_files_probe([Path("relative")])


def test_secret_probe_rejects_wrong_owner_and_directories(tmp_path, monkeypatch):
    path = tmp_path / "consumer"
    path.write_bytes(b"synthetic")
    path.chmod(0o400)
    uid = readiness.os.geteuid()
    monkeypatch.setattr(readiness.os, "geteuid", lambda: uid + 1)
    assert asyncio.run(readiness.secret_files_probe([path])()) is False
    assert asyncio.run(readiness.secret_files_probe([tmp_path])()) is False


def test_secret_probe_closes_rejected_directory_descriptor(tmp_path, monkeypatch):
    opened, closed = [], []
    original_open, original_close = readiness.os.open, readiness.os.close

    def open_owned(path, flags):
        fd = original_open(path, flags)
        opened.append(fd)
        return fd

    def close_owned(fd):
        closed.append(fd)
        original_close(fd)

    monkeypatch.setattr(readiness.os, "open", open_owned)
    monkeypatch.setattr(readiness.os, "close", close_owned)
    assert asyncio.run(readiness.secret_files_probe([tmp_path])()) is False
    assert len(opened) == 1 and opened[0] in closed
