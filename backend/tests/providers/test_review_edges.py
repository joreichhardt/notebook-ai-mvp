"""Review regressions: synthetic input only, no live endpoints or database."""

import asyncio
from contextlib import aclosing
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from app.connections.execution import ConnectionState, ExecutionService
from app.providers.adapter import Adapter
from app.providers.types import Message, TextDelta
from fastapi import HTTPException
from test_execution_adapter import provider
from test_execution_limits import Wire, consume


@pytest.mark.parametrize(
    "body",
    [
        b'data: {"deep":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}\n\n",
        b'data: {"model":"x","choices":[{"index":false,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
    ],
)
def test_malformed_nested_or_boolean_index_stream_fails_closed(body):
    wire = Wire([body])
    adapter = Adapter(
        provider(), "synthetic", httpx.MockTransport(lambda _: httpx.Response(200, stream=wire))
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(consume(adapter))
    assert error.value.status_code == 502
    assert wire.closed


@pytest.mark.parametrize("action", ["revoke", "close", "replace"])
def test_paused_consumer_does_not_keep_upstream_running(action):
    actor, connection = uuid4(), uuid4()
    state = ConnectionState(connection, actor, "fixture", uuid4(), "active")
    closed = asyncio.Event()

    async def lookup(a, c):
        return state

    async def secret(s):
        return "synthetic"

    class Source:
        async def stream(self, **kwargs):
            try:
                while True:
                    yield TextDelta("piece")
            finally:
                closed.set()

    service = ExecutionService(
        lookup=lookup,
        read_secret=secret,
        resolve_provider=lambda p: p,
        adapter_factory=lambda p, k: Source(),
        poll_interval=0.001,
    )

    async def run():
        nonlocal state
        async with aclosing(
            service.stream(
                actor_id=actor,
                connection_id=connection,
                model="fixture",
                messages=[Message(role="user", content="hello")],
                max_output_tokens=10,
            )
        ) as stream:
            assert await anext(stream) == TextDelta("piece")
            if action == "close":
                await stream.aclose()
            else:
                state = replace(
                    state,
                    **(
                        {"status": "disconnected"} if action == "revoke" else {"secret_id": uuid4()}
                    ),
                )
                await asyncio.wait_for(closed.wait(), 0.5)
                with pytest.raises(HTTPException):
                    await anext(stream)
        assert closed.is_set()

    asyncio.run(run())
