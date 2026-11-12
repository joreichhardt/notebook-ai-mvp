import asyncio
from contextlib import aclosing

import httpx
import pytest
from app.providers.adapter import Adapter
from app.providers.types import Limits, Message, TextDelta
from fastapi import HTTPException
from test_execution_adapter import provider


class Wire(httpx.AsyncByteStream):
    def __init__(self, chunks, *, stall=False):
        self.chunks, self.stall, self.closed = chunks, stall, False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.stall:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


async def consume(adapter):
    return [
        e
        async for e in adapter.stream(
            model="fixture", messages=[Message(role="user", content="Hi")], max_output_tokens=10
        )
    ]


@pytest.mark.parametrize(
    "kind,body",
    [
        ("openai", b"data: {bad SYNTHETIC_SECRET}\n\n"),
        ("openai", b"data: [DONE]\n\n"),
        ("openai", b"data: {}"),
        (
            "openai",
            b'data: {"model":"x","choices":[{"index":0,"delta":{"tool_calls":[{}]},"finish_reason":null}]}\n\n',
        ),
        ("anthropic", b'data: {"type":"message_stop"}\n\n'),
        ("anthropic", b'data: {"type":"error","error":{"message":"SYNTHETIC_SECRET"}}\n\n'),
        ("ollama", b'{"model":"x","message":{"content":"x"},"done":false}\n'),
        ("ollama", b'{"model":"x","message":{"content":12},"done":true}\n'),
    ],
)
def test_malformed_stream_is_sanitized_and_closed(kind, body):
    wire = Wire([body])
    adapter = Adapter(
        provider(kind),
        "SYNTHETIC_SECRET",
        httpx.MockTransport(lambda _: httpx.Response(200, stream=wire)),
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(consume(adapter))
    assert error.value.status_code == 502
    assert "SYNTHETIC_SECRET" not in str(error.value)
    assert wire.closed


@pytest.mark.parametrize(
    "status,expected", [(401, 400), (403, 400), (429, 429), (302, 502), (500, 502), (404, 400)]
)
def test_status_errors_never_include_body_or_follow_redirect(status, expected):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, text="SYNTHETIC_SECRET", headers={"location": "http://169.254.169.254"}
        )

    with pytest.raises(HTTPException) as error:
        asyncio.run(consume(Adapter(provider(), "SYNTHETIC_SECRET", httpx.MockTransport(handler))))
    assert error.value.status_code == expected
    assert "SYNTHETIC_SECRET" not in str(error.value)
    assert len(calls) == 1


def test_transport_exception_traceback_is_redacted():
    import traceback

    secret = "SYNTHETIC" + "_SECRET"

    def handler(request):
        raise httpx.ConnectError(secret, request=request)

    adapter = Adapter(provider(), secret, httpx.MockTransport(handler))
    with pytest.raises(HTTPException) as error:
        asyncio.run(consume(adapter))
    assert secret not in "".join(traceback.format_exception(error.value))


def test_compressed_response_is_rejected_before_decoding():
    wire = Wire([b"not even gzip"])
    adapter = Adapter(
        provider(),
        "fixture",
        httpx.MockTransport(
            lambda _: httpx.Response(200, stream=wire, headers={"content-encoding": "gzip"})
        ),
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(consume(adapter))
    assert "provider_protocol" in str(error.value.detail)
    assert wire.closed


def test_deadline_closes_stalled_response():
    wire = Wire([], stall=True)
    adapter = Adapter(
        provider(),
        "fixture",
        httpx.MockTransport(lambda _: httpx.Response(200, stream=wire)),
        limits=Limits(deadline=0.01),
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(consume(adapter))
    assert error.value.status_code == 504
    assert wire.closed


def test_task_cancellation_closes_response():
    wire = Wire([], stall=True)

    async def run():
        task = asyncio.create_task(
            consume(
                Adapter(
                    provider(),
                    "fixture",
                    httpx.MockTransport(lambda _: httpx.Response(200, stream=wire)),
                )
            )
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert wire.closed


def test_consumer_close_after_delta_closes_response():
    frame = b'data: {"model":"x","choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
    wire = Wire([frame], stall=True)

    async def run():
        adapter = Adapter(
            provider(), "fixture", httpx.MockTransport(lambda _: httpx.Response(200, stream=wire))
        )
        async with aclosing(
            adapter.stream(
                model="x", messages=[Message(role="user", content="hi")], max_output_tokens=10
            )
        ) as stream:
            assert await asyncio.wait_for(anext(stream), 0.1) == TextDelta("hi")
        assert wire.closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "limits,body",
    [
        (Limits(frame_bytes=32), b"data: " + b"x" * 100 + b"\n\n"),
        (Limits(response_bytes=32), b":" + b"x" * 100 + b"\n\n"),
        (
            Limits(output_bytes=1),
            b'data: {"model":"x","choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n\n',
        ),
    ],
)
def test_wire_and_output_limits(limits, body):
    wire = Wire([body])
    with pytest.raises(HTTPException):
        asyncio.run(
            consume(
                Adapter(
                    provider(),
                    "fixture",
                    httpx.MockTransport(lambda _: httpx.Response(200, stream=wire)),
                    limits=limits,
                )
            )
        )
    assert wire.closed


def test_input_limit_prevents_network_call():
    def handler(_):
        pytest.fail("network must not be reached")

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            consume(
                Adapter(
                    provider(),
                    "fixture",
                    httpx.MockTransport(handler),
                    limits=Limits(input_bytes=1),
                )
            )
        )
    assert error.value.status_code == 400
