"""Actual root engine budgets; run only against own isolated SCRAM fixture."""

import os
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from types import SimpleNamespace

import psycopg
import pytest
from app.db import session as root
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, TimeoutError
from sqlalchemy.pool import QueuePool

URL = os.getenv("NOTEBOOK_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL required")


@pytest.fixture
def factory(monkeypatch):
    url = (
        make_url(URL)
        .set(drivername="postgresql+psycopg")
        ._replace(username="notebook_app", password=None)
        .difference_update_query(["password", "passfile", "user"])
    )
    assert url.password is None
    assert not {"password", "passfile", "user"}.intersection(url.query)
    url = url.update_query_dict(
        {
            "application_name": "a5-root-budgets",
            "sslmode": "prefer",
            "connect_timeout": "90",
            "options": "-c statement_timeout=90000 -c lock_timeout=90000 -c search_path=pg_catalog,public",
        }
    )
    root.engine.cache_clear()
    monkeypatch.setattr(
        root,
        "get_settings",
        lambda: SimpleNamespace(database_url=SecretStr(url.render_as_string())),
    )
    engine = root.engine()
    try:
        yield engine
    finally:
        engine.dispose()
        root.engine.cache_clear()


def test_actual_driver_parameters_and_server_settings(factory):
    args, kwargs = factory.dialect.create_connect_args(factory.url)
    # Observe the final factory-generated DBAPI kwargs, including connect_args.
    from sqlalchemy import event

    observed = {}

    @event.listens_for(factory, "do_connect")
    def capture(dialect, record, cargs, cparams):
        for name in ("connect_timeout", "options", "sslmode", "application_name"):
            observed[name] = cparams.get(name)

    with factory.connect() as db:
        assert db.execute(
            text("SELECT current_user=session_user AND current_user='notebook_app'")
        ).scalar_one()
        assert db.execute(text("SHOW statement_timeout")).scalar_one() == "3s"
        assert db.execute(text("SHOW lock_timeout")).scalar_one() == "1s"
        assert db.execute(text("SHOW search_path")).scalar_one() == "pg_catalog,public"
        assert db.execute(text("SHOW application_name")).scalar_one() == "a5-root-budgets"
        assert db.execute(text("SHOW idle_in_transaction_session_timeout")).scalar_one() == "0"
    assert observed["connect_timeout"] == 3
    assert observed["sslmode"] == "prefer"
    assert factory.pool.timeout() == 1
    assert not args and kwargs["application_name"] == "a5-root-budgets"


def test_real_pool_saturation_is_bounded_and_releases(factory):
    assert factory.pool.timeout() == 1  # RED fails before default30s wait.
    held = []
    try:
        # Exercise actual configured QueuePool, not a replacement/mock pool.
        for _ in range(factory.pool.size() + factory.pool._max_overflow):
            held.append(factory.connect())
        start = monotonic()
        with pytest.raises(TimeoutError):
            factory.connect()
        elapsed = monotonic() - start
        print(f"pool saturation elapsed={elapsed:.3f}s")
        assert 0.8 <= elapsed < 3
    finally:
        for connection in held:
            connection.close()
    assert factory.pool.checkedout() == 0
    with factory.connect() as db:
        assert db.execute(text("SELECT 1")).scalar_one() == 1


def test_real_statement_timeout_rolls_back_and_releases(factory):
    with factory.connect() as db:
        assert db.execute(text("SHOW statement_timeout")).scalar_one() == "3s"
        db.rollback()
        start = monotonic()
        with pytest.raises(DBAPIError) as caught:
            db.execute(text("SELECT pg_sleep(10)"))
        elapsed = monotonic() - start
        assert isinstance(caught.value.orig, psycopg.Error)
        assert caught.value.orig.sqlstate == "57014"
        print(f"statement timeout elapsed={elapsed:.3f}s")
        assert 2.5 <= elapsed < 6
        db.rollback()
        assert db.execute(text("SELECT 1")).scalar_one() == 1
    assert factory.pool.checkedout() == 0


def test_real_row_lock_timeout_rolls_back_and_releases(factory):
    # Only own fixture's persistent table/row, no application data involved.
    with psycopg.connect(URL) as admin:
        admin.execute(
            "CREATE TABLE public.a5_factory_lock_probe(id integer PRIMARY KEY, value integer)"
        )
        admin.execute("INSERT INTO public.a5_factory_lock_probe VALUES(1,0)")
        admin.execute("GRANT SELECT,UPDATE ON public.a5_factory_lock_probe TO notebook_app")
    try:
        with psycopg.connect(URL) as holder:
            holder.execute("UPDATE public.a5_factory_lock_probe SET value=1 WHERE id=1")
            with factory.connect() as db:
                assert db.execute(text("SHOW lock_timeout")).scalar_one() == "1s"
                db.rollback()
                start = monotonic()
                with pytest.raises(DBAPIError) as caught:
                    db.execute(text("UPDATE public.a5_factory_lock_probe SET value=2 WHERE id=1"))
                elapsed = monotonic() - start
                assert isinstance(caught.value.orig, psycopg.Error)
                assert caught.value.orig.sqlstate == "55P03"
                print(f"row lock timeout elapsed={elapsed:.3f}s")
                assert 0.8 <= elapsed < 3
                db.rollback()
                assert db.execute(text("SELECT 1")).scalar_one() == 1
        with factory.connect() as db:
            assert (
                db.execute(
                    text("SELECT value FROM public.a5_factory_lock_probe WHERE id=1")
                ).scalar_one()
                == 1
            )
        assert factory.pool.checkedout() == 0
    finally:
        with psycopg.connect(URL) as admin:
            admin.execute("DROP TABLE public.a5_factory_lock_probe")


def test_owned_loopback_connect_timeout(monkeypatch):
    # Real accepted TCP connection that never speaks PostgreSQL; owned by this test.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(8)
        stop = Event()
        accepted = Event()

        def sink():
            peer, _ = listener.accept()
            with peer:
                accepted.set()
                stop.wait(8)

        url = (
            make_url(URL)
            .set(
                drivername="postgresql+psycopg",
                host="127.0.0.1",
                port=listener.getsockname()[1],
            )
            ._replace(username="notebook_app", password=None)
            .difference_update_query(
                [
                    "password",
                    "passfile",
                    "user",
                    "host",
                    "hostaddr",
                    "port",
                    "service",
                    "servicefile",
                ]
            )
        )
        assert url.password is None
        assert not {"password", "passfile", "user"}.intersection(url.query)
        url = url.update_query_dict({"sslmode": "disable", "connect_timeout": "90"})
        root.engine.cache_clear()
        monkeypatch.setattr(
            root,
            "get_settings",
            lambda: SimpleNamespace(database_url=SecretStr(url.render_as_string())),
        )
        engine = root.engine()
        try:
            assert isinstance(engine.pool, QueuePool)
            assert engine.pool.timeout() == 1  # Prevent long old-factory probe.
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(sink)
                try:
                    start = monotonic()
                    with pytest.raises(DBAPIError):
                        engine.connect()
                    elapsed = monotonic() - start
                    assert accepted.is_set()
                    print(f"owned connect timeout elapsed={elapsed:.3f}s")
                    assert 2 <= elapsed < 6
                finally:
                    stop.set()
                    future.result(timeout=9)
            assert engine.pool.checkedout() == 0
        finally:
            engine.dispose()
            root.engine.cache_clear()


def test_empty_configuration_stays_opaque(monkeypatch):
    from fastapi import HTTPException

    root.engine.cache_clear()
    monkeypatch.setattr(root, "get_settings", lambda: SimpleNamespace(database_url=SecretStr("")))
    try:
        with pytest.raises(HTTPException) as caught:
            root.engine()
        assert caught.value.status_code == 503
        assert caught.value.detail == {
            "code": "database_unconfigured",
            "message": "Datenbank ist noch nicht eingerichtet.",
        }
    finally:
        root.engine.cache_clear()
