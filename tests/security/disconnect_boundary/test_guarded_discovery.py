"""Synthetic external/session seams; no database, credentials or network."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.auth.supabase import Identity
from app.connections import execution as boundary
from app.connections.execution import ConnectionState, DiscoveryRepository, ExecutionService
from fastapi import HTTPException

CANARY = "synthetic-private-driver-provider-canary"


class World:
    def __init__(self):
        self.actor, self.connection = uuid4(), uuid4()
        self.state = ConnectionState(self.connection, self.actor, "fixture", uuid4(), "active")
        self.admitted = True
        self.calls = self.reads = self.publications = 0
        self.after_read = lambda: None
        self.after_models = lambda: None
        self.stalled = False
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    def revoke(self, change):
        if change == "delete":
            self.state = None
        elif change == "admission":
            self.admitted = False
        else:
            field, value = {
                "disconnect": ("status", "disconnected"),
                "reference": ("secret_id", uuid4()),
                "provider": ("provider_id", "other"),
                "owner": ("user_id", uuid4()),
            }[change]
            assert self.state is not None
            self.state = replace(self.state, **{field: value})

    async def lookup(self, actor, connection):
        assert (actor, connection) == (self.actor, self.connection)
        if not self.admitted:
            raise HTTPException(403, {"code": "admission_required", "message": CANARY})
        return self.state

    async def read(self, state):
        self.reads += 1
        self.after_read()
        return "synthetic-key"

    def adapter(self, provider, key):
        assert key == "synthetic-key"
        return self

    async def models(self):
        self.calls += 1
        self.started.set()
        try:
            if self.stalled:
                await asyncio.Event().wait()
            self.after_models()
            return [{"id": "fixture-model"}]
        finally:
            self.closed.set()

    def publish(self, state, model):
        assert state == self.state and self.admitted
        self.publications += 1
        return {"id": str(state.id), "model": model}

    def service(self):
        return ExecutionService(
            lookup=self.lookup,
            read_secret=self.read,
            adapter_factory=self.adapter,
            resolve_provider=lambda value: value,
            poll_interval=0.001,
        )

    async def discover(self, selected_model=None, publish=None):
        return await self.service().discover(
            actor_id=self.actor,
            connection_id=self.connection,
            selected_model=selected_model,
            publish=publish or self.publish,
        )


CHANGES = ["disconnect", "delete", "reference", "provider", "owner", "admission"]


@pytest.mark.parametrize("model", [None, "fixture-model"])
@pytest.mark.parametrize("change", CHANGES)
def test_revocation_during_secret_read_prevents_discovery(change, model):
    world = World()
    world.after_read = lambda: world.revoke(change)
    with pytest.raises(HTTPException) as error:
        asyncio.run(world.discover(model))
    assert error.value.status_code in (403, 409)
    assert (world.calls, world.publications) == (0, 0)
    assert CANARY not in str(error.value.detail)


@pytest.mark.parametrize("model", [None, "fixture-model"])
@pytest.mark.parametrize("change", CHANGES)
def test_stalled_discovery_is_closed_on_revocation(change, model):
    async def exercise():
        world = World()
        world.stalled = True
        pending = asyncio.create_task(world.discover(model))
        await asyncio.wait_for(world.started.wait(), 1)
        world.revoke(change)
        with pytest.raises(HTTPException):
            await asyncio.wait_for(pending, 1)
        assert world.closed.is_set()
        assert (world.calls, world.publications) == (1, 0)

    asyncio.run(exercise())


@pytest.mark.parametrize("model", [None, "fixture-model"])
@pytest.mark.parametrize("change", CHANGES)
def test_result_after_revocation_is_not_published(change, model):
    world = World()
    world.after_models = lambda: world.revoke(change)
    with pytest.raises(HTTPException):
        asyncio.run(world.discover(model))
    assert (world.calls, world.publications) == (1, 0)


def test_consumer_cancellation_closes_discovery():
    async def exercise():
        world = World()
        world.stalled = True
        task = asyncio.create_task(world.discover())
        await asyncio.wait_for(world.started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert world.closed.is_set()
        assert world.publications == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("stage", ["secret", "provider", "publish"])
@pytest.mark.parametrize("http", [False, True, "malformed"])
def test_hostile_failures_are_sanitized_without_replay(stage, http):
    world = World()

    def explode(*args):
        if http:
            code = [CANARY] if http == "malformed" else "private"
            raise HTTPException(503, {"code": code, "message": CANARY})
        raise RuntimeError(CANARY)

    if stage == "secret":
        world.after_read = explode
    if stage == "provider":
        world.after_models = explode
    with pytest.raises(HTTPException) as error:
        asyncio.run(world.discover(publish=explode if stage == "publish" else None))
    assert error.value.status_code == 503
    assert CANARY not in str(error.value.detail)
    assert world.calls == (0 if stage == "secret" else 1)


@pytest.mark.parametrize("model", [None, "fixture-model"])
def test_success_uses_existing_response_fields(model):
    world = World()
    result = asyncio.run(world.discover(model))
    assert result == (
        {"models": [{"id": "fixture-model"}], "status": "ok"}
        if model is None
        else {"id": str(world.connection), "model": model}
    )
    assert (world.reads, world.calls, world.publications) == (1, 1, 1)


def test_unavailable_model_never_publishes():
    world = World()
    with pytest.raises(HTTPException) as error:
        asyncio.run(world.discover("missing"))
    assert error.value.status_code == 400
    assert world.publications == 0


class TransactionFixture:
    """Instrument actual repository against a synthetic session boundary."""

    def __init__(self):
        self.world = World()
        self.user = Identity(id=self.world.actor, email="fixture@example.test")
        self.active = False
        self.commits = self.rollbacks = 0
        self.fail_commit = self.fail_rollback = False
        self.admission = True
        self.changed = True
        self.before_publish = lambda: None
        self.statements = []

    @contextmanager
    def factory(self, user):
        assert user is self.user
        assert not self.active
        self.active = True
        try:
            yield self
            if self.fail_commit:
                raise RuntimeError(CANARY)
            self.commits += 1
        except BaseException:
            self.rollbacks += 1
            if self.fail_rollback:
                raise RuntimeError(CANARY) from None
            raise
        finally:
            self.active = False

    def scalar(self, statement):
        from sqlalchemy.sql.dml import Update

        self.statements.append(statement)
        if isinstance(statement, Update):
            return self.world.connection if self.changed else None
        if "app_admissions" in str(statement):
            return self.world.actor if self.admission else None
        state = self.world.state
        if state is None:
            return None
        return SimpleNamespace(**state.__dict__, label="Fixture", model=None, last_tested_at=None)

    def repository(self):
        return DiscoveryRepository(self.user, self.factory)

    def publish(self, state, model):
        self.before_publish()
        return self.repository().publish(state, model)

    async def run(self, model=None):
        async def lookup(actor, connection):
            return self.repository().lookup(actor, connection)

        async def read(state):
            assert not self.active
            return await self.world.read(state)

        async def models():
            assert not self.active
            return [{"id": "fixture-model"}]

        service = ExecutionService(
            lookup=lookup,
            read_secret=read,
            adapter_factory=lambda *args: SimpleNamespace(models=models),
            resolve_provider=lambda value: value,
        )
        return await service.discover(
            actor_id=self.world.actor,
            connection_id=self.world.connection,
            selected_model=model,
            publish=self.publish,
        )


@pytest.mark.parametrize("model", [None, "fixture-model"])
@pytest.mark.parametrize("change", CHANGES)
def test_actual_publisher_rejects_revocation_after_final_guard(change, model):
    fixture = TransactionFixture()
    if change == "admission":
        fixture.before_publish = lambda: setattr(fixture, "admission", False)
    else:
        fixture.before_publish = lambda: fixture.world.revoke(change)
    with pytest.raises(HTTPException) as error:
        asyncio.run(fixture.run(model))
    assert error.value.status_code == 409
    assert not fixture.active
    assert fixture.rollbacks == 1


def test_missing_conditional_update_row_denies_success():
    fixture = TransactionFixture()
    fixture.changed = False
    with pytest.raises(HTTPException) as error:
        asyncio.run(fixture.run())
    assert error.value.status_code == 409
    assert fixture.rollbacks == 1


@pytest.mark.parametrize("rollback_error", [False, True])
def test_actual_publisher_commit_failure_returns_no_success(rollback_error):
    fixture = TransactionFixture()
    fixture.before_publish = lambda: setattr(fixture, "fail_commit", True)
    fixture.fail_rollback = rollback_error
    with pytest.raises(HTTPException) as error:
        asyncio.run(fixture.run())
    assert error.value.status_code == 503
    assert CANARY not in str(error.value.detail)
    assert fixture.world.reads == 1
    assert fixture.rollbacks == 1
    assert not fixture.active


@pytest.mark.parametrize("model", [None, "fixture-model"])
def test_actual_repository_lifecycle_and_conditional_statement(model):
    from sqlalchemy.dialects import postgresql

    fixture = TransactionFixture()
    result = asyncio.run(fixture.run(model))
    assert not fixture.active
    assert fixture.commits == 4  # pre/post-secret, post-result, publication
    assert fixture.rollbacks == 0
    if model:
        assert result["model"] == model
        assert "secret_id" not in result
    sql = [str(s.compile(dialect=postgresql.dialect())) for s in fixture.statements]
    assert any("FOR UPDATE" in s for s in sql)
    assert not any("FOR SHARE" in s for s in sql)
    conditional = next(s for s in sql if s.startswith("UPDATE"))
    assert "EXISTS" in conditional and "app_admissions" in conditional
    assert "RETURNING" in conditional and "secret_id" in conditional


@pytest.mark.parametrize("safe,admitted", [(False, True), (True, False), (True, True)])
def test_real_factory_principal_claims_and_lifecycle(monkeypatch, safe, admitted):
    fixture = TransactionFixture()
    events = []

    class Engine:
        def execution_options(self, **kwargs):
            assert kwargs == {"isolation_level": "READ COMMITTED"}
            return self

    class Session:
        def __init__(self, engine):
            pass

        def __enter__(self):
            events.append("open")
            return self

        def __exit__(self, *error):
            events.append("close")

        @contextmanager
        def begin(self):
            try:
                yield
            except BaseException:
                events.append("rollback")
                raise
            else:
                events.append("commit")

        def execute(self, statement, params=None):
            sql = str(statement)
            events.append((sql, params))
            if params is None:
                assert "session_user=current_user" in sql
                assert "rolname = 'notebook_app'" in sql
                assert "NOT rolsuper AND NOT rolbypassrls" in sql
                return SimpleNamespace(scalar=lambda: safe)
            return None

        def get(self, table, actor):
            assert actor == fixture.user.id
            return object() if admitted else None

    monkeypatch.setattr(boundary, "engine", Engine)
    monkeypatch.setattr(boundary, "Session", Session)
    if safe and admitted:
        with boundary.discovery_session(fixture.user):
            events.append("body")
        assert events[-2:] == ["commit", "close"]
        claims = [params for event in events if isinstance(event, tuple) for params in [event[1]]]
        assert {"uid": str(fixture.user.id)} in claims
        assert {"email": fixture.user.email} in claims
    else:
        with pytest.raises(HTTPException) as error, boundary.discovery_session(fixture.user):
            pytest.fail("unsafe factory yielded")
        assert error.value.status_code == (503 if not safe else 403)
        assert events[-2:] == ["rollback", "close"]


@pytest.mark.parametrize("operation", ["test", "select"])
@pytest.mark.parametrize("scenario", ["success", "foreign", "csrf", "hostile"])
def test_http_routes_use_injected_boundary_not_request_db(monkeypatch, operation, scenario):
    from app.auth.security import CSRF_COOKIE
    from app.connections import router
    from app.db.session import identity, user_session
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    world = World()
    user = Identity(world.actor, "fixture@example.test")
    if scenario == "foreign":
        assert world.state is not None
        world.state = replace(world.state, user_id=uuid4())
    if scenario == "hostile":

        def explode():
            raise RuntimeError(CANARY)

        world.after_models = explode
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[identity] = lambda: user
    app.dependency_overrides[router.discovery_repository] = lambda: SimpleNamespace(
        publish=world.publish
    )
    app.dependency_overrides[router.discovery_service] = world.service

    def forbidden_request_session():
        pytest.fail("discovery used request-lifetime transaction")

    app.dependency_overrides[user_session] = forbidden_request_session
    monkeypatch.setattr(
        router, "get_settings", lambda: SimpleNamespace(public_origin="https://example.test")
    )
    with TestClient(app, base_url="https://example.test") as client:
        client.cookies.set(CSRF_COOKIE, "fixture-csrf")
        headers = (
            {}
            if scenario == "csrf"
            else {"origin": "https://example.test", "x-csrf-token": "fixture-csrf"}
        )
        if operation == "test":
            response = client.post(f"/api/connections/{world.connection}/test", headers=headers)
        else:
            response = client.patch(
                f"/api/connections/{world.connection}",
                headers=headers,
                json={"model": "fixture-model"},
            )
    assert (
        response.status_code
        == {"success": 200, "foreign": 409, "csrf": 403, "hostile": 503}[scenario]
    )
    assert CANARY not in response.text and "secret_id" not in response.text
    assert "synthetic-key" not in response.text
    assert world.calls == (1 if scenario in ("success", "hostile") else 0)
