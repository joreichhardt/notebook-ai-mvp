"""Real restricted-role execution lookup; provider and vault remain synthetic.

Only run against the isolated migrated test database, never a shared instance.
"""

import asyncio
import os
from uuid import uuid4

import psycopg
import pytest
from app.connections import execution
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

URL = os.getenv("NOTEBOOK_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL URL not configured")


@pytest.fixture
def persisted_connection(monkeypatch):
    actor, other, connection, secret, workspace = [uuid4() for _ in range(5)]
    runtime = create_engine(
        make_url(URL).set(drivername="postgresql+psycopg", username="notebook_app"),
        pool_size=1,
        max_overflow=0,
        hide_parameters=True,
    )
    monkeypatch.setattr(execution, "engine", lambda: runtime)
    with psycopg.connect(URL) as db:
        # Fixture setup is privileged; execution lookups use notebook_app instead.
        for user in (actor, other):
            db.execute("INSERT INTO app_admissions(user_id) VALUES (%s)", (user,))
        db.execute("INSERT INTO workspaces(id,name) VALUES (%s,'Synthetic')", (workspace,))
        for user in (actor, other):
            db.execute(
                "INSERT INTO workspace_members(workspace_id,user_id,role) VALUES (%s,%s,'editor')",
                (workspace, user),
            )
        db.execute(
            "INSERT INTO user_provider_connections"
            "(id,user_id,provider_id,label,secret_id,status) "
            "VALUES (%s,%s,'fixture','Synthetic',%s,'active')",
            (connection, actor, secret),
        )
    try:
        yield actor, other, connection
    finally:
        runtime.dispose()
        with psycopg.connect(URL) as db:
            db.execute("DELETE FROM user_provider_connections WHERE id=%s", (connection,))
            db.execute("DELETE FROM workspaces WHERE id=%s", (workspace,))
            db.execute("DELETE FROM app_admissions WHERE user_id IN (%s,%s)", (actor, other))


def test_lookup_scopes_ownership_and_fresh_admission(persisted_connection):
    actor, other, connection = persisted_connection
    assert execution._lookup(actor, connection) is not None
    # Same workspace and reused single pooled connection cannot leak identity.
    assert execution._lookup(other, connection) is None
    assert execution._lookup(actor, connection) is not None
    with psycopg.connect(URL) as db:
        db.execute("DELETE FROM app_admissions WHERE user_id=%s", (actor,))
    with pytest.raises(HTTPException) as error:
        execution._lookup(actor, connection)
    assert error.value.status_code == 403


def test_disconnect_during_secret_resolution_prevents_adapter(persisted_connection):
    actor, other, connection = persisted_connection
    calls = []

    async def read_secret(state):
        calls.append(state.id)
        with psycopg.connect(URL) as db:
            db.execute(
                "UPDATE user_provider_connections SET status='disconnected' WHERE id=%s",
                (connection,),
            )
        return "synthetic-never-send"

    def adapter_factory(*args):
        pytest.fail("Adapter must not be constructed after credential revocation")

    service = execution.ExecutionService(read_secret=read_secret, adapter_factory=adapter_factory)

    async def check():
        # Foreign workspace member must not reach even the synthetic secret reader.
        with pytest.raises(HTTPException):
            await service.embed(
                actor_id=other, connection_id=connection, model="fixture", texts=["x"]
            )
        assert calls == []
        with pytest.raises(HTTPException) as error:
            await service.embed(
                actor_id=actor, connection_id=connection, model="fixture", texts=["x"]
            )
        assert error.value.status_code == 409
        assert calls == [connection]

    asyncio.run(check())
