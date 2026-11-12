import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import HTTPException


def test_foreign_or_inactive_connection_never_resolves_secret():
    from app.connections.execution import ConnectionState, ExecutionService

    actor, connection = uuid4(), uuid4()
    calls = []

    async def read(*args):
        calls.append(args)
        return "SYNTHETIC"

    for owner, status in [(uuid4(), "active"), (actor, "disconnected")]:

        async def lookup(a, c, owner=owner, status=status):
            return ConnectionState(c, owner, "fixture", uuid4(), status)

        service = ExecutionService(lookup=lookup, read_secret=read)
        with pytest.raises(HTTPException):
            asyncio.run(
                service.embed(actor_id=actor, connection_id=connection, model="x", texts=["hi"])
            )
    assert calls == []


def test_revocation_during_secret_resolution_prevents_call():
    from app.connections.execution import ConnectionState, ExecutionService

    actor, connection = uuid4(), uuid4()
    state = ConnectionState(connection, actor, "fixture", uuid4(), "active")

    async def lookup(a, c):
        return state

    async def read(*args):
        nonlocal state
        state = replace(state, status="disconnected")
        return "SYNTHETIC"

    service = ExecutionService(lookup=lookup, read_secret=read)
    with pytest.raises(HTTPException):
        asyncio.run(
            service.embed(actor_id=actor, connection_id=connection, model="x", texts=["hi"])
        )


@pytest.mark.parametrize("operation", ["embed", "stream"])
def test_revocation_cancels_stalled_upstream_and_closes(operation):
    from app.connections.execution import ConnectionState, ExecutionService
    from app.providers.types import Message, TextDelta

    actor, connection = uuid4(), uuid4()
    state = ConnectionState(connection, actor, "fixture", uuid4(), "active")
    closed = False
    checks = 0

    async def lookup(a, c):
        nonlocal checks
        checks += 1
        return state

    async def read(s):
        return "SYNTHETIC"

    class FakeAdapter:
        async def embed(self, **kwargs):
            nonlocal closed
            try:
                await asyncio.Event().wait()
            finally:
                closed = True

        async def stream(self, **kwargs):
            nonlocal closed
            try:
                yield TextDelta("hello")
                await asyncio.Event().wait()
            finally:
                closed = True

    service = ExecutionService(
        lookup=lookup,
        read_secret=read,
        adapter_factory=lambda p, k: FakeAdapter(),
        resolve_provider=lambda p: p,
        poll_interval=0.001,
    )

    async def run():
        nonlocal state

        async def consume():
            if operation == "embed":
                return await service.embed(
                    actor_id=actor, connection_id=connection, model="x", texts=["hi"]
                )
            return [
                e
                async for e in service.stream(
                    actor_id=actor,
                    connection_id=connection,
                    model="x",
                    messages=[Message(role="user", content="hi")],
                    max_output_tokens=20,
                )
            ]

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        state = replace(state, status="disconnected")
        with pytest.raises(HTTPException):
            await asyncio.wait_for(task, 0.5)

    asyncio.run(run())
    assert closed
    assert checks >= 3


def test_owned_stream_preserves_order_terminal_and_close():
    from contextlib import aclosing

    from app.connections.execution import ConnectionState, ExecutionService
    from app.providers.types import Message, StreamDone, TextDelta

    actor, connection = uuid4(), uuid4()
    state = ConnectionState(connection, actor, "fixture", uuid4(), "active")
    in_transaction = False
    closed = False

    async def lookup(a, c):
        nonlocal in_transaction
        assert not in_transaction
        in_transaction = True
        try:
            assert a == actor and c == connection
            return state
        finally:
            in_transaction = False

    async def read(s):
        assert not in_transaction
        return "SYNTHETIC"

    expected = [TextDelta("one"), TextDelta("two"), StreamDone("actual", "stop")]

    class FakeAdapter:
        async def stream(self, **kwargs):
            nonlocal closed
            try:
                for event in expected:
                    assert not in_transaction
                    yield event
            finally:
                closed = True

    service = ExecutionService(
        lookup=lookup,
        read_secret=read,
        adapter_factory=lambda p, k: FakeAdapter(),
        resolve_provider=lambda p: p,
    )

    async def run():
        async with aclosing(
            service.stream(
                actor_id=actor,
                connection_id=connection,
                model="x",
                messages=[Message(role="user", content="hi")],
                max_output_tokens=20,
            )
        ) as stream:
            return [e async for e in stream]

    assert asyncio.run(run()) == expected
    assert closed
