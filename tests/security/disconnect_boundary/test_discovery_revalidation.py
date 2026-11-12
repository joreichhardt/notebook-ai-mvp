"""F4 RED regressions: real route bodies, synthetic state/store/adapter, no DB/I/O.

The second snapshot represents a committed concurrent disconnect while a secret
read is returning an already obtained value. This does not simulate PostgreSQL
locking/RLS/commit behavior; that separate acceptance test still requires kube1.
"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from app.connections import router
from app.connections.execution import ConnectionState, ExecutionService
from fastapi import HTTPException


@pytest.mark.parametrize("operation", ["test", "select_model"])
@pytest.mark.parametrize("revoke_during_read", [False, True])
def test_discovery_must_not_begin_after_disconnect(monkeypatch, operation, revoke_during_read):
    actor, connection, secret = uuid4(), uuid4(), uuid4()
    current = SimpleNamespace(status="active" if revoke_during_read else "disconnected")
    # A materialized row is not live state: a different transaction can change it.
    snapshot = SimpleNamespace(
        id=connection,
        user_id=actor,
        provider_id="fixture",
        secret_id=secret,
        status=current.status,
        label="Synthetic",
        model=None,
        last_tested_at=None,
    )
    reads, provider_calls = [], []

    class Session:
        def scalar(self, statement):
            return snapshot

        def flush(self):
            pass

    class Store:
        def read(self, secret_id, owner_id, connection_id):
            assert (secret_id, owner_id, connection_id) == (secret, actor, connection)
            reads.append(secret_id)
            current.status = "disconnected"
            return "synthetic-not-a-credential"

    class Adapter:
        def __init__(self, provider, key):
            assert key == "synthetic-not-a-credential"

        async def models(self):
            provider_calls.append(current.status)
            return [{"id": "fixture-model"}]

    monkeypatch.setattr(router, "check_csrf", lambda *args: None)
    monkeypatch.setattr(
        router, "get_settings", lambda: SimpleNamespace(public_origin="https://example.test")
    )
    monkeypatch.setattr(router, "settings", lambda: None)
    monkeypatch.setattr(router, "provider_config", lambda provider: provider)
    monkeypatch.setattr(router, "BitwardenStore", lambda config: Store())
    monkeypatch.setattr(router, "Adapter", Adapter)

    async def exercise():
        # Dependency injection deliberately supplies minimal test doubles.
        kwargs: dict[str, Any] = {
            "connection_id": connection,
            "request": None,
            "user": SimpleNamespace(id=actor),
            "db": Session(),
        }
        try:
            if operation == "test":
                await router.test(**kwargs)
            else:
                await router.select_model(body=router.SelectModel(model="fixture-model"), **kwargs)
        except HTTPException as error:
            assert error.status_code in (403, 404, 409)

    asyncio.run(exercise())
    assert len(reads) == int(revoke_during_read)
    assert provider_calls == [], "discovery began with a cached key after disconnect"


def test_execution_service_control_rechecks_after_same_secret_read_boundary():
    actor, connection = uuid4(), uuid4()
    state = ConnectionState(connection, actor, "fixture", uuid4(), "active")
    adapters = []

    async def lookup(actor_id, connection_id):
        assert (actor_id, connection_id) == (actor, connection)
        return state

    async def read(snapshot):
        nonlocal state
        state = replace(snapshot, status="disconnected")
        return "synthetic-not-a-credential"

    service = ExecutionService(
        lookup=lookup,
        read_secret=read,
        adapter_factory=lambda *args: adapters.append(args),
        resolve_provider=lambda provider: provider,
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            service.embed(actor_id=actor, connection_id=connection, model="fixture", texts=["hi"])
        )
    assert error.value.status_code == 409
    assert adapters == []
