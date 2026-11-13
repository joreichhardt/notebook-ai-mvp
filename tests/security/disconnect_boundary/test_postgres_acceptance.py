"""OPT-IN, UNRUN acceptance packet: real PostgreSQL + in-process production HTTP.

No imported backend test fixtures, migrations, collection-time connections or fallback.
See docs/review/disconnect-boundary.md for the separately approved kube1 procedure.
"""

import asyncio
import logging
import os
import re
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.auth.security import CSRF_COOKIE, SESSION_COOKIE
from app.auth.supabase import SupabaseAuth
from app.connections import execution, router
from app.db import session as session_layer
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.selectable import Select

ENABLED = os.getenv("F4_RUN_POSTGRES") == "isolated-kube1-approved"
pytestmark = pytest.mark.skipif(not ENABLED, reason="F4 PostgreSQL explicit opt-in absent")
ORIGIN = "https://f4.example.test"
CANARY = "F4_SYNTHETIC_SECRET_CANARY"
LIMIT = 8


def require(condition, label):
    # Never let pytest introspect URLs, SQL, driver errors, response bodies or locals.
    if not condition:
        pytest.fail(label, pytrace=False)


@contextmanager
def redacted(label):
    try:
        yield
    except Exception:  # noqa: BLE001 — fixed diagnostics only, never driver/DSN contents
        pytest.fail(label, pytrace=False)


def configuration():
    """Validate the assigned passwordless endpoints BEFORE creating any engine."""
    database = os.getenv("F4_ASSIGNED_DATABASE", "")
    host = os.getenv("F4_ASSIGNED_HOST", "")
    require(bool(re.fullmatch(r"notebook_f4_[0-9a-f]{12}", database)), "F4 assignment missing")
    require(host == "db", "F4 requires assigned isolated Compose db host")
    urls = []
    for role in ("APP", "ADMIN"):
        with redacted("F4 endpoint configuration rejected"):
            url = make_url(os.environ[f"F4_{role}_URL"])
            path = Path(os.environ[f"F4_{role}_PASSFILE"])
            mode = path.lstat().st_mode
            require(
                stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o600, "F4 private passfile required"
            )
            require(path.is_absolute() and not path.is_symlink(), "F4 passfile path rejected")
            require(
                url.drivername in ("postgresql", "postgresql+psycopg")
                and url.host == host
                and url.port == 5432
                and url.database == database
                and not url.password
                and not url.query,
                "F4 endpoint does not match assignment",
            )
            require(
                url.username == ("notebook_app" if role == "APP" else "postgres"),
                "F4 direct role required",
            )
            urls.append((url.set(drivername="postgresql+psycopg"), str(path)))
    return urls


class Barrier:
    """Bounded thread/async barrier; no sleep-based ordering assertions."""

    def __init__(self, loop):
        self.loop = loop
        self.entered = asyncio.Event()
        self.release = threading.Event()

    def pause(self):
        self.loop.call_soon_threadsafe(self.entered.set)
        if not self.release.wait(LIMIT):
            raise RuntimeError("F4 barrier deadline")

    async def wait(self):
        await asyncio.wait_for(self.entered.wait(), LIMIT)


class Rig:
    def __init__(self, app_engine, admin_engine):
        self.engine, self.admin_engine = app_engine, admin_engine
        self.app = FastAPI()
        self.actor, self.other, self.connection, self.secret = (uuid4() for _ in range(4))
        self.name = f"f4_{uuid4().hex}"
        self.reads = self.calls = self.updates = 0
        self.rowcounts = []
        self.cleanup_pending = False
        self.secret_entered = asyncio.Event()
        self.secret_release = asyncio.Event()
        self.provider_entered = asyncio.Event()
        self.provider_release = asyncio.Event()
        self.closed = asyncio.Event()
        self.hold_secret = self.hold_provider = False
        self.before_lock = self.after_lock = None
        self.before_update = self.after_update = None
        self.fail_commit = False
        self.disconnect_attempt = asyncio.Event()
        self.loop = None

    def admin(self, statement, params=None):
        with redacted("F4 administrative operation failed"), self.admin_engine.begin() as db:
            result = db.execute(text(statement), params or {})
            return result.all() if result.returns_rows else None

    def mutate(self, kind):
        commands = {
            "admission": "DELETE FROM app_admissions WHERE user_id=:actor",
            "reference": "UPDATE user_provider_connections SET secret_id=:replacement WHERE id=:id",
            "provider": "UPDATE user_provider_connections SET provider_id='changed' WHERE id=:id",
            "inactive": "UPDATE user_provider_connections SET status='disconnected' WHERE id=:id",
        }
        self.admin(
            commands[kind], {"actor": self.actor, "id": self.connection, "replacement": uuid4()}
        )

    def no_open_transaction(self):
        rows = self.admin(
            "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
            "AND application_name=:name AND xact_start IS NOT NULL",
            {"name": self.name},
        )
        require(rows == [(0,)], "F4 application transaction spans synthetic I/O")

    async def wait_for_connection_contention(self):
        deadline = time.monotonic() + 1.5  # below database lock_timeout
        while time.monotonic() < deadline:
            rows = await asyncio.to_thread(
                self.admin,
                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                "AND application_name=:name AND cardinality(pg_blocking_pids(pid))>0",
                {"name": self.name},
            )
            if rows == [(1,)]:
                return
        require(False, "F4 competing disconnect never waited on publisher lock")

    def before(self, conn, cursor, statement, parameters, context, executemany):
        compiled = context.compiled
        node = compiled.statement if compiled else None
        if isinstance(node, Select) and node._for_update_arg is not None and self.before_lock:
            self.before_lock.pause()
        if (
            isinstance(node, Update)
            and getattr(node.table, "name", None) == "user_provider_connections"
        ):
            if node._returning:
                self.updates += 1
                conn.info["f4_publication"] = True
                if self.before_update:
                    self.before_update()
            elif self.loop:
                self.loop.call_soon_threadsafe(self.disconnect_attempt.set)

    def after(self, conn, cursor, statement, parameters, context, executemany):
        node = context.compiled.statement if context.compiled else None
        if isinstance(node, Select) and node._for_update_arg is not None and self.after_lock:
            self.after_lock.pause()
        if isinstance(node, Update) and node._returning:
            self.rowcounts.append(cursor.rowcount)
            if self.after_update:
                self.after_update()

    def committing(self, conn):
        publication = conn.info.pop("f4_publication", False)
        if publication and self.fail_commit:
            raise RuntimeError("F4 injected pre-commit failure")

    async def secret_read(self, state):
        self.reads += 1
        require(state.user_id == self.actor, "F4 foreign secret request")
        self.no_open_transaction()
        self.secret_entered.set()
        if self.hold_secret:
            await asyncio.wait_for(self.secret_release.wait(), LIMIT)
        return CANARY

    async def models(self):
        self.calls += 1
        self.no_open_transaction()
        self.provider_entered.set()
        try:
            if self.hold_provider:
                await asyncio.wait_for(self.provider_release.wait(), LIMIT)
            return [{"id": "f4-model"}]
        finally:
            self.closed.set()

    async def request(self, client, operation, actor="owner"):
        cookies = f"{SESSION_COOKIE}={actor}; {CSRF_COOKIE}=f4-csrf"
        headers = {"origin": ORIGIN, "x-csrf-token": "f4-csrf", "cookie": cookies}
        path = f"/api/connections/{self.connection}"
        if operation == "test":
            return await client.post(path + "/test", headers=headers)
        if operation == "select":
            return await client.patch(path, headers=headers, json={"model": "f4-model"})
        return await client.delete(path, headers=headers)

    def disconnect_http(self):
        # disconnect's legacy sync flush can block its ASGI loop on a row lock.
        # Give the competing HTTP request its own loop/thread, never block the
        # discovery loop that releases the deterministic publication barrier.
        async def request():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
                base_url=ORIGIN,
            ) as client:
                return await self.request(client, "delete")

        return asyncio.run(request())

    def response(self, response, status):
        require(response.status_code == status, "F4 unexpected HTTP status")
        require(CANARY not in response.text and "secret_id" not in response.text, "F4 unsafe DTO")

    def unchanged(self):
        rows = self.admin(
            "SELECT model,last_tested_at FROM user_provider_connections WHERE id=:id",
            {"id": self.connection},
        )
        require(rows in ([], [(None, None)]), "F4 revoked publication changed metadata")


@pytest.fixture
def quiet_driver_logs():
    # Pool cleanup can log original driver exceptions even with echo=False.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


@pytest.fixture
def rig(monkeypatch, quiet_driver_logs):
    # Fixture body is not reached on ordinary collection/default skipped execution.
    app_config, admin_config = configuration()
    engines = []
    runtime = None
    setup_started = False
    try:
        with redacted("F4 database fixture failed (diagnostics suppressed)"):
            for url, passfile in (app_config, admin_config):
                engines.append(
                    create_engine(
                        url,
                        hide_parameters=True,
                        echo=False,
                        pool_size=3,
                        max_overflow=0,
                        pool_timeout=2,
                        connect_args={
                            "passfile": passfile,
                            "connect_timeout": 3,
                            "options": "-c statement_timeout=4000 -c lock_timeout=2000",
                        },
                    )
                )
            runtime = Rig(*engines)
            preflight = runtime.admin(
                "SELECT current_user, current_setting('server_version_num')::int / 10000, "
                "(SELECT count(*) FROM app_admissions), "
                "(SELECT count(*) FROM user_provider_connections)"
            )
            require(
                preflight == [("postgres", 16, 0, 0)],
                "F4 requires fresh isolated PostgreSQL16 schema",
            )

            # Checked on EVERY new physical application connection, not SET ROLE.
            @event.listens_for(runtime.engine, "connect")
            def direct_role(dbapi, record):
                with dbapi.cursor() as cursor:
                    cursor.execute(
                        "SELECT current_user,session_user,rolsuper,rolbypassrls "
                        "FROM pg_roles WHERE rolname=current_user"
                    )
                    require(
                        cursor.fetchone() == ("notebook_app", "notebook_app", False, False),
                        "F4 unsafe application login",
                    )
                    cursor.execute(
                        "SELECT set_config('application_name',%s,false)", (runtime.name,)
                    )
                dbapi.commit()

            setup_started = True
            runtime.admin(
                "INSERT INTO app_admissions(user_id) VALUES (:owner),(:other)",
                {"owner": runtime.actor, "other": runtime.other},
            )
            runtime.admin(
                "INSERT INTO user_provider_connections(id,user_id,provider_id,label,secret_id,status) "
                "VALUES (:id,:actor,'fixture','F4 synthetic',:secret,'active')",
                {"id": runtime.connection, "actor": runtime.actor, "secret": runtime.secret},
            )
            monkeypatch.setattr(execution, "engine", lambda: runtime.engine)
            monkeypatch.setattr(session_layer, "engine", lambda: runtime.engine)
            monkeypatch.setattr(
                router, "get_settings", lambda: SimpleNamespace(public_origin=ORIGIN)
            )
            monkeypatch.setattr(router, "settings", lambda: None)

            # Exercise production identity verifier; fake ONLY its remote /user response.
            async def auth_request(self, method, path, token="", data=None):
                require(method == "GET" and path == "user", "F4 unexpected auth endpoint")
                actor = {"owner": runtime.actor, "other": runtime.other}.get(token)
                if actor is None:
                    raise HTTPException(401, {"code": "invalid_identity"})
                return {
                    "id": str(actor),
                    "email": "f4@example.test",
                    "email_confirmed_at": "synthetic",
                }

            monkeypatch.setattr(SupabaseAuth, "request", auth_request)

            class Store:
                def __init__(self, config):
                    pass

                def delete(self, *args):
                    if runtime.cleanup_pending:
                        raise HTTPException(503, {"code": "synthetic_cleanup_pending"})

            monkeypatch.setattr(router, "BitwardenStore", Store)
            app = runtime.app
            app.include_router(router.router)

            # Real route dependency builds the actual repository with discovery_session.
            # Only external secret/provider operations are replaced, never lookup/publish.
            def service(repository: router.DiscoveryRepo):
                result = router.discovery_service(repository)
                result.read_secret = runtime.secret_read
                result.resolve_provider = lambda provider: provider
                result.adapter_factory = lambda provider, key: runtime
                result.poll_interval = 0.01
                return result

            app.dependency_overrides[router.discovery_service] = service
            for name, callback in (
                ("before_cursor_execute", runtime.before),
                ("after_cursor_execute", runtime.after),
                ("commit", runtime.committing),
            ):
                event.listen(runtime.engine, name, callback)
            yield runtime
    finally:
        if runtime and setup_started:
            for barrier in (runtime.before_lock, runtime.after_lock):
                if barrier:
                    barrier.release.set()
            try:
                runtime.admin(
                    "DELETE FROM user_provider_connections WHERE id=:id", {"id": runtime.connection}
                )
                runtime.admin(
                    "DELETE FROM app_admissions WHERE user_id IN (:owner,:other)",
                    {"owner": runtime.actor, "other": runtime.other},
                )
            finally:
                for engine in engines:
                    engine.dispose()
        else:
            for engine in engines:
                engine.dispose()


async def exercise(rig, body):
    rig.loop = asyncio.get_running_loop()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=rig.app, raise_app_exceptions=False), base_url=ORIGIN
    ) as client:
        tasks = []
        try:
            async with asyncio.timeout(LIMIT * 3):
                await body(client, tasks)
        finally:
            rig.secret_release.set()
            rig.provider_release.set()
            for barrier in (rig.before_lock, rig.after_lock):
                if barrier:
                    barrier.release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def run(rig, body):
    with redacted("F4 acceptance scenario failed (diagnostics suppressed)"):
        asyncio.run(exercise(rig, body))


@pytest.mark.parametrize("operation", ["test", "select"])
@pytest.mark.parametrize("revocation", ["202", "204", "admission", "reference", "provider"])
def test_secret_return_rechecks_committed_revocation(rig, operation, revocation):
    rig.hold_secret = True

    async def body(client, tasks):
        task = asyncio.create_task(rig.request(client, operation))
        tasks.append(task)
        await asyncio.wait_for(rig.secret_entered.wait(), LIMIT)
        rig.no_open_transaction()
        if revocation in ("202", "204"):
            rig.cleanup_pending = revocation == "202"
            rig.response(await rig.request(client, "delete"), int(revocation))
            rows = rig.admin(
                "SELECT status FROM user_provider_connections WHERE id=:id", {"id": rig.connection}
            )
            require(
                rows == ([("disconnected",)] if revocation == "202" else []),
                "F4 disconnect not committed",
            )
        else:
            rig.mutate(revocation)
        rig.secret_release.set()
        rig.response(await task, 403 if revocation == "admission" else 409)
        require(rig.calls == 0 and rig.updates == 0 and rig.reads == 1, "F4 stale key used")
        rig.unchanged()

    run(rig, body)


@pytest.mark.parametrize("operation", ["test", "select"])
@pytest.mark.parametrize("revocation", ["inactive", "admission", "reference", "provider"])
def test_stalled_provider_is_cooperatively_closed(rig, operation, revocation):
    rig.hold_provider = True

    async def body(client, tasks):
        task = asyncio.create_task(rig.request(client, operation))
        tasks.append(task)
        await asyncio.wait_for(rig.provider_entered.wait(), LIMIT)
        # I/O-entry check ran before the watch started; a later check could
        # legitimately overlap the watch's fresh short authorization transaction.
        rig.mutate(revocation)
        rig.response(await asyncio.wait_for(task, LIMIT), 403 if revocation == "admission" else 409)
        require(
            rig.closed.is_set() and rig.calls == 1 and rig.updates == 0,
            "F4 cancellation/publication failure",
        )
        rig.unchanged()

    run(rig, body)


@pytest.mark.parametrize("operation", ["test", "select"])
@pytest.mark.parametrize("ordering", ["revoke-first", "publish-first"])
def test_connection_lock_orders_publication_and_http_disconnect(rig, operation, ordering):
    async def body(client, tasks):
        barrier = Barrier(asyncio.get_running_loop())
        if ordering == "revoke-first":
            rig.before_lock = barrier
        else:
            rig.after_lock = barrier
        task = asyncio.create_task(rig.request(client, operation))
        tasks.append(task)
        await barrier.wait()  # post-result guard completed; production publication owns next step
        rig.cleanup_pending = True
        deletion = asyncio.create_task(asyncio.to_thread(rig.disconnect_http))
        tasks.append(deletion)
        if ordering == "revoke-first":
            rig.response(await deletion, 202)
        else:
            # Publisher already owns row lock. Disconnect reaches its competing UPDATE.
            await asyncio.wait_for(rig.disconnect_attempt.wait(), LIMIT)
            await rig.wait_for_connection_contention()
        barrier.release.set()
        rig.response(await task, 409 if ordering == "revoke-first" else 200)
        rig.response(await deletion, 202)
        require(rig.calls == 1 and rig.reads == 1, "F4 external operation replayed")
        rows = rig.admin(
            "SELECT status,model,last_tested_at FROM user_provider_connections WHERE id=:id",
            {"id": rig.connection},
        )
        require(len(rows) == 1 and rows[0][0] == "disconnected", "F4 final disconnect missing")
        if ordering == "revoke-first":
            rig.unchanged()
        else:
            require(
                (rows[0][1] == "f4-model") if operation == "select" else rows[0][2] is not None,
                "F4 winning publication not committed",
            )

    run(rig, body)


@pytest.mark.parametrize("operation", ["test", "select"])
@pytest.mark.parametrize("fault", ["no-row", "commit-failure", "admission-after-statement"])
def test_conditional_publication_and_commit_boundary(rig, operation, fault):
    if fault == "no-row":
        rig.before_update = lambda: rig.mutate("admission")
    elif fault == "admission-after-statement":
        rig.after_update = lambda: rig.mutate("admission")
    else:
        rig.fail_commit = True  # injected before physical COMMIT, not unknown wire outcome proof

    async def body(client, tasks):
        response = await rig.request(client, operation)
        rig.response(
            response,
            {"no-row": 409, "commit-failure": 503, "admission-after-statement": 200}[fault],
        )
        require(rig.reads == rig.calls == rig.updates == 1, "F4 side effect replayed")
        require(
            rig.rowcounts == ([0] if fault == "no-row" else [1]), "F4 conditional rowcount differs"
        )
        if fault != "admission-after-statement":
            rig.unchanged()
        else:
            rows = rig.admin(
                "SELECT count(*) FROM app_admissions WHERE user_id=:actor", {"actor": rig.actor}
            )
            require(rows == [(0,)], "F4 late admission deletion missing")
            # Deliberately asserts the documented LIMIT, not atomic admission-at-commit.
            rows = rig.admin(
                "SELECT model,last_tested_at FROM user_provider_connections WHERE id=:id",
                {"id": rig.connection},
            )
            require(
                (rows[0][0] == "f4-model") if operation == "select" else rows[0][1] is not None,
                "F4 late-snapshot control did not commit",
            )

    run(rig, body)


@pytest.mark.parametrize("operation", ["test", "select"])
def test_other_verified_actor_cannot_use_connection(rig, operation):
    async def body(client, tasks):
        # Two simultaneous physical restricted sessions, distinct claims, actual RLS.
        from app.auth.supabase import Identity

        with (
            execution.discovery_session(Identity(rig.actor, "owner@example.test")) as owner,
            execution.discovery_session(Identity(rig.other, "other@example.test")) as other,
        ):
            owner_pid = owner.scalar(text("SELECT pg_backend_pid()"))
            other_pid = other.scalar(text("SELECT pg_backend_pid()"))
            require(owner_pid != other_pid, "F4 actor sessions not distinct")
            require(
                owner.execute(
                    text(
                        "SELECT has_table_privilege(current_user,'app_admissions','SELECT'), "
                        "has_table_privilege(current_user,'app_admissions','UPDATE'), "
                        "has_table_privilege(current_user,'user_provider_connections','UPDATE')"
                    )
                ).one()
                == (True, False, True),
                "F4 restricted grants differ from approved publication design",
            )
            require(
                other.scalar(
                    text("SELECT count(*) FROM user_provider_connections WHERE id=:id"),
                    {"id": rig.connection},
                )
                == 0,
                "F4 RLS exposes foreign connection",
            )
        rig.response(await rig.request(client, operation, actor="other"), 409)
        require(rig.reads == rig.calls == rig.updates == 0, "F4 foreign credentials used")

    run(rig, body)
