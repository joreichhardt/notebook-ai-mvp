import asyncio
import json

import httpx
import pytest
from app.providers.adapter import Adapter
from app.providers.types import Message, StreamDone, TextDelta
from fastapi import HTTPException
from test_execution_adapter import provider


def test_native_anthropic_stream():
    frames = [
        {
            "type": "message_start",
            "message": {
                "model": "claude-fixture",
                "usage": {"input_tokens": 3, "output_tokens": 0},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 1},
        },
        {"type": "message_stop"},
    ]

    def handler(request):
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "SYNTHETIC"
        body = json.loads(request.content)
        assert body["system"] == "Rules"
        assert body["messages"] == [{"role": "user", "content": "Hello"}]
        assert "stream_options" not in body
        return httpx.Response(200, text="".join("data: " + json.dumps(f) + "\n\n" for f in frames))

    async def run():
        return [
            e
            async for e in Adapter(
                provider("anthropic"), "SYNTHETIC", httpx.MockTransport(handler)
            ).stream(
                model="alias",
                messages=[
                    Message(role="system", content="Rules"),
                    Message(role="user", content="Hello"),
                ],
                max_output_tokens=20,
            )
        ]

    assert asyncio.run(run()) == [TextDelta("Hi"), StreamDone("claude-fixture", "end_turn", 3, 1)]


def test_ollama_stream():
    def handler(request):
        assert request.url.path == "/v1/api/chat"
        body = json.loads(request.content)
        assert body["options"] == {"num_predict": 20}
        return httpx.Response(
            200,
            text='{"model":"local-fixture","message":{"content":"Hi"},"done":false}\n{"model":"local-fixture","message":{"content":""},"done":true,"done_reason":"stop","prompt_eval_count":3,"eval_count":1}\n',
        )

    async def run():
        return [
            e
            async for e in Adapter(
                provider("ollama"), "SYNTHETIC", httpx.MockTransport(handler)
            ).stream(
                model="alias",
                messages=[Message(role="user", content="Hello")],
                max_output_tokens=20,
            )
        ]

    assert asyncio.run(run()) == [TextDelta("Hi"), StreamDone("local-fixture", "stop", 3, 1)]


def test_anthropic_embedding_is_explicitly_unsupported():
    with pytest.raises(HTTPException) as error:
        asyncio.run(Adapter(provider("anthropic"), "SYNTHETIC").embed(model="m", texts=["hi"]))
    assert "provider_unsupported" in str(error.value.detail)
