"""Synthetic protocol fixtures, not live provider acceptance."""

import asyncio
import json

import httpx
from app.providers.adapter import Adapter
from app.providers.config import Provider


def provider(kind="openai"):
    return Provider(
        id="fixture",
        label="Fixture",
        kind=kind,
        base_url="http://127.0.0.1:9999/v1",
        allow_internal=True,
    )


def test_openai_text_stream_and_usage():
    from app.providers.types import Message, StreamDone, TextDelta

    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert body["stream"] and "tools" not in body
        chunks = [
            {
                "model": "actual",
                "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}],
            },
            {"model": "actual", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {
                "model": "actual",
                "choices": [],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        ]
        return httpx.Response(
            200,
            text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n",
        )

    async def run():
        return [
            e
            async for e in Adapter(provider(), "SYNTHETIC", httpx.MockTransport(handler)).stream(
                model="alias", messages=[Message(role="user", content="Hi")], max_output_tokens=20
            )
        ]

    events = asyncio.run(run())
    assert events == [
        TextDelta(text="Hello"),
        StreamDone(model="actual", finish_reason="stop", input_tokens=2, output_tokens=1),
    ]
