"""Bounded wire decoding. Errors never include upstream text."""

import json
from collections.abc import AsyncIterator
from typing import NoReturn

import httpx
from fastapi import HTTPException

from app.auth.security import fail
from app.providers.types import Limits, StreamDone, TextDelta


def invalid() -> NoReturn:
    raise HTTPException(
        502, detail={"code": "provider_protocol", "message": "Ungültige Antwort des Anbieters."}
    ) from None


def object_json(value):
    try:
        result = json.loads(value)
    except (ValueError, TypeError, RecursionError):
        invalid()
    if not isinstance(result, dict):
        invalid()
    if "error" in result:
        fail(502, "provider_error", "Anbieter konnte die Anfrage nicht ausführen.")
    return result


def token(value):
    if value is not None and (type(value) is not int or not 0 <= value <= 10**9):
        invalid()
    return value


def model_name(value):
    if not isinstance(value, str) or not 0 < len(value) <= 200:
        invalid()
    return value


async def frames(response: httpx.Response, limits: Limits, *, sse: bool) -> AsyncIterator[str]:
    buffer = b""
    data = []
    size = total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limits.response_bytes:
            invalid()
        buffer += chunk
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            if len(raw) > limits.frame_bytes:
                invalid()
            line = raw.rstrip(b"\r").decode("utf-8", errors="strict")
            if not sse:
                if line:
                    yield line
            elif line == "":
                if data:
                    yield "\n".join(data)
                data, size = [], 0
            elif line.startswith("data:"):
                size += len(raw)
                if size > limits.frame_bytes:
                    invalid()
                data.append(line[5:].removeprefix(" "))
        if len(buffer) > limits.frame_bytes:
            invalid()
    if buffer or data:
        # Require complete framing; truncated responses must not be accepted.
        invalid()


async def anthropic_events(response, limits) -> AsyncIterator[TextDelta | StreamDone]:
    model = reason = None
    input_tokens = output_tokens = None
    active = None
    next_index = 0
    async for frame in frames(response, limits, sse=True):
        data = object_json(frame)
        kind = data["type"]
        if kind.startswith("content_block_") and type(data.get("index")) is not int:
            invalid()
        if kind == "ping":
            continue
        if kind == "message_start":
            if model:
                invalid()
            message = data["message"]
            model = model_name(message["model"])
            input_tokens = token(message["usage"].get("input_tokens"))
        elif not model:
            invalid()
        elif kind == "content_block_start":
            block = data["content_block"]
            if active is not None or reason or data["index"] != next_index:
                invalid()
            if block["type"] != "text" or block.get("text", "") != "":
                invalid()
            active = data["index"]
        elif kind == "content_block_delta":
            if active is None or data["index"] != active or reason:
                invalid()
            delta = data["delta"]
            if delta["type"] != "text_delta" or not isinstance(delta["text"], str):
                invalid()
            if delta["text"]:
                yield TextDelta(delta["text"])
        elif kind == "content_block_stop":
            if active is None or data["index"] != active:
                invalid()
            active = None
            next_index += 1
        elif kind == "message_delta":
            if active is not None or reason:
                invalid()
            reason = data["delta"]["stop_reason"]
            if reason not in ("end_turn", "max_tokens", "stop_sequence", "refusal"):
                invalid()
            output_tokens = token(data["usage"].get("output_tokens"))
        elif kind == "message_stop":
            if active is not None or not reason:
                invalid()
            yield StreamDone(model, reason, input_tokens, output_tokens)
            return
        else:
            # No tool/thinking/fallback content is enabled by this adapter.
            invalid()
    invalid()


async def ollama_events(response, limits) -> AsyncIterator[TextDelta | StreamDone]:
    model = None
    async for frame in frames(response, limits, sse=False):
        data = object_json(frame)
        actual = model_name(data["model"])
        if model and model != actual:
            invalid()
        model = actual
        message = data["message"]
        if message.get("tool_calls") or message.get("thinking"):
            invalid()
        text = message["content"]
        if not isinstance(text, str) or type(data["done"]) is not bool:
            invalid()
        if text:
            yield TextDelta(text)
        if data["done"]:
            reason = data["done_reason"]
            if reason not in ("stop", "length"):
                invalid()
            yield StreamDone(
                model, reason, token(data.get("prompt_eval_count")), token(data.get("eval_count"))
            )
            return
    invalid()


async def openai_events(response, limits) -> AsyncIterator[TextDelta | StreamDone]:
    model = reason = None
    input_tokens = output_tokens = None
    async for frame in frames(response, limits, sse=True):
        if frame == "[DONE]":
            if not model or not reason:
                invalid()
            yield StreamDone(model, reason, input_tokens, output_tokens)
            return
        data = object_json(frame)
        actual = model_name(data.get("model"))
        if model and model != actual:
            invalid()
        model = actual
        if data.get("usage") is not None:
            usage = data["usage"]
            input_tokens = token(usage.get("prompt_tokens"))
            output_tokens = token(usage.get("completion_tokens"))
        choices = data["choices"]
        if not isinstance(choices, list) or len(choices) > 1:
            invalid()
        if not choices:
            continue
        choice = choices[0]
        if type(choice["index"]) is not int or choice["index"] != 0 or reason:
            invalid()
        delta = choice["delta"]
        if any(delta.get(k) for k in ("tool_calls", "function_call", "audio", "refusal")):
            invalid()
        text = delta.get("content")
        if text is not None:
            if not isinstance(text, str):
                invalid()
            if text:
                yield TextDelta(text)
        reason = choice.get("finish_reason")
        if reason is not None and reason not in ("stop", "length", "content_filter"):
            invalid()
    invalid()
