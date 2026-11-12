"""Synthetic adversarial wire fixtures; no real provider or credentials."""

import asyncio
import json
import traceback

import httpx
import pytest
from app.providers.adapter import Adapter, Message, StreamDone
from app.providers.config import Provider
from app.providers.protocol import TextDelta
from fastapi import HTTPException


class Wire(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body
        self.closed = False

    async def __aiter__(self):
        # Every byte boundary, including split UTF-8 and SSE delimiters.
        for value in self.body:
            yield bytes([value])

    async def aclose(self):
        self.closed = True


def run(body, *, kind="openai", embed=False):
    wire = Wire(body)
    adapter = Adapter(
        Provider(id="fixture", label="Synthetic", kind=kind, base_url="https://fixture.invalid"),
        "synthetic-key-never-transmitted",
        httpx.MockTransport(lambda _: httpx.Response(200, stream=wire)),
    )

    async def consume():
        if embed:
            return await adapter.embed(model="fixture", texts=["synthetic"])
        return [
            event
            async for event in adapter.stream(
                model="fixture",
                messages=[Message(role="user", content="synthetic")],
                max_output_tokens=10,
            )
        ]

    try:
        return asyncio.run(consume())
    finally:
        assert wire.closed


@pytest.mark.parametrize(
    "body,kind,embed",
    [
        (
            b'data: {"model":"x","choices":[{"index":0,"delta":{"content":"\\ud800"}}]}\n\n',
            "openai",
            False,
        ),
        (b'data: {"model":"x","choices":[]}\xff\n\n', "openai", False),
        (b'data: {"type":null,"synthetic":"secret-marker"}\n\n', "anthropic", False),
        (b'{"model":"x","data":[{"index":0,"embedding":[1e999]}]}', "openai", True),
        (b'{"model":"x","data":[{"index":0,"embedding":[true]}]}', "openai", True),
        (b'{"deep":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}", "openai", True),
    ],
)
def test_malformed_payloads_have_sanitized_errors_and_close(body, kind, embed):
    with pytest.raises(HTTPException) as error:
        run(body, kind=kind, embed=embed)
    assert error.value.status_code == 502
    assert error.value.detail == {
        "code": "provider_protocol",
        "message": "Ungültige Antwort des Anbieters.",
    }
    rendered = "".join(traceback.format_exception(error.value))
    assert "secret-marker" not in rendered
    assert "synthetic-key-never-transmitted" not in rendered


def test_fragmented_utf8_preserves_text_and_single_terminal():
    first = {
        "model": "fixture",
        "choices": [{"index": 0, "delta": {"content": "Grüße 🌱"}, "finish_reason": None}],
    }
    last = {"model": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    body = (
        "data: "
        + json.dumps(first, ensure_ascii=False)
        + "\r\n\r\n"
        + "data: "
        + json.dumps(last)
        + "\r\n\r\ndata: [DONE]\r\n\r\n"
    ).encode()
    events = run(body)
    assert isinstance(events, list)
    assert [event.type for event in events] == ["delta", "done"]
    assert isinstance(events[0], TextDelta)
    assert isinstance(events[1], StreamDone)
    assert events[0].text == "Grüße 🌱"
    assert events[1].model == "fixture"
