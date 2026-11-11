"""Bounded, credential-private native protocols over controlled egress."""

import asyncio
import math
import socket
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import NoReturn
from urllib.parse import urlsplit

import httpcore
import httpx
from fastapi import HTTPException

from app.providers.protocol import (
    anthropic_events,
    invalid,
    model_name,
    object_json,
    ollama_events,
    openai_events,
)
from app.providers.transport import ControlledTransport, check_addresses
from app.providers.types import EmbeddingResult, Limits, Message, StreamDone, StreamEvent


def fail(status: int, code: str, message: str) -> NoReturn:
    # Suppress raw transport/provider exception chains in server error logs.
    raise HTTPException(status, detail={"code": code, "message": message}) from None


def validate_destination(provider):
    """Compatibility preflight only; ControlledTransport enforces the actual dial."""
    url = urlsplit(provider.base_url)
    if url.scheme != "https" and not provider.allow_internal:
        fail(503, "provider_configuration", "Anbieter ist nicht erreichbar.")
    try:
        addresses = socket.getaddrinfo(
            url.hostname,
            url.port or (443 if url.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError:
        fail(503, "provider_unavailable", "Anbieter ist nicht erreichbar.")
        return
    check_addresses(provider, [row[4][0] for row in addresses])


class Adapter:
    def __init__(self, provider, key, transport=None, *, limits=None):
        self.provider = provider
        self.key = key
        # Injection is for trusted test fixtures, never request data/configuration.
        self.transport = transport
        self.limits = limits or Limits()

    def _headers(self):
        if self.provider.kind == "anthropic":
            return {
                "x-api-key": self.key,
                "anthropic-version": "2023-06-01",
                "Accept-Encoding": "identity",
            }
        return {"Authorization": f"Bearer {self.key}", "Accept-Encoding": "identity"}

    @staticmethod
    def _status(response) -> None:
        if response.status_code in (401, 403):
            fail(400, "provider_credentials", "Zugang wurde vom Anbieter abgewiesen.")
        if response.status_code == 429:
            fail(429, "provider_rate_limit", "Anbieterlimit erreicht. Bitte später versuchen.")
        if response.status_code in (404, 405, 501):
            fail(400, "provider_unsupported", "Diese Operation wird nicht unterstützt.")
        if not 200 <= response.status_code < 300:
            fail(502, "provider_unavailable", "Anbieter ist nicht erreichbar.")

    @asynccontextmanager
    async def _response(self, method, path, payload=None) -> AsyncIterator[httpx.Response]:
        try:
            async with (
                asyncio.timeout(self.limits.deadline),
                httpx.AsyncClient(
                    timeout=self.limits.read_timeout,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self.transport or ControlledTransport(self.provider),
                ) as client,
                client.stream(
                    method,
                    self.provider.base_url + path,
                    headers=self._headers(),
                    json=payload,
                ) as response,
            ):
                self._status(response)
                if response.headers.get("content-encoding", "identity") != "identity":
                    fail(502, "provider_protocol", "Komprimierte Anbieterantwort nicht erlaubt.")
                yield response
        except (TimeoutError, httpx.TimeoutException, httpcore.TimeoutException):
            fail(504, "provider_timeout", "Zeitlimit des Anbieters erreicht.")
        except (httpx.HTTPError, httpcore.NetworkError, httpcore.ProtocolError, OSError):
            fail(502, "provider_unavailable", "Anbieter ist nicht erreichbar.")
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):
            fail(502, "provider_protocol", "Ungültige Antwort des Anbieters.")

    async def _json(self, response):
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=4096):
            body.extend(chunk)
            if len(body) > self.limits.response_bytes:
                invalid()
        return object_json(body)

    def _input(self, model, texts):
        if (
            not isinstance(model, str)
            or not 0 < len(model) <= 200
            or not texts
            or any(not isinstance(t, str) or not t for t in texts)
            or sum(len(t.encode()) for t in texts) > self.limits.input_bytes
        ):
            fail(400, "provider_input", "Anfrage überschreitet die Anbietergrenzen.")

    async def models(self):
        path = "/api/tags" if self.provider.kind == "ollama" else "/models"
        async with self._response("GET", path) as response:
            data = await self._json(response)
            rows = (
                data.get("models", []) if self.provider.kind == "ollama" else data.get("data", [])
            )
            if not isinstance(rows, list):
                invalid()
            models = [
                {"id": r.get("name") if self.provider.kind == "ollama" else r.get("id")}
                for r in rows[:500]
            ]
            return [m for m in models if isinstance(m["id"], str) and 0 < len(m["id"]) <= 200]

    async def stream(
        self, *, model: str, messages: list[Message], max_output_tokens: int
    ) -> AsyncGenerator[StreamEvent, None]:
        if (
            not messages
            or len(messages) > 128
            or any(not isinstance(m, Message) for m in messages)
            or type(max_output_tokens) is not int
            or not 1 <= max_output_tokens <= self.limits.max_output_tokens
        ):
            fail(400, "provider_input", "Anfrage überschreitet die Anbietergrenzen.")
        self._input(model, [m.content for m in messages])
        payload = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "max_tokens": max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        path, decode = "/chat/completions", openai_events
        if self.provider.kind == "anthropic":
            path, decode = "/messages", anthropic_events
            payload.pop("stream_options")
            systems = [m.content for m in messages if m.role == "system"]
            payload["messages"] = [m.model_dump() for m in messages if m.role != "system"]
            if not payload["messages"]:
                fail(400, "provider_input", "Eine Nutzernachricht ist erforderlich.")
            if systems:
                payload["system"] = "\n\n".join(systems)
        elif self.provider.kind == "ollama":
            path, decode = "/api/chat", ollama_events
            payload.pop("stream_options")
            payload.pop("max_tokens")
            payload["options"] = {"num_predict": max_output_tokens}
        async with self._response("POST", path, payload) as response:
            size = 0
            async for event in decode(response, self.limits):
                if not isinstance(event, StreamDone):
                    size += len(event.text.encode())
                    if size > self.limits.output_bytes:
                        invalid()
                yield event

    async def embed(self, *, model: str, texts: list[str]) -> EmbeddingResult:
        if self.provider.kind == "anthropic":
            fail(400, "provider_unsupported", "Diese Operation wird nicht unterstützt.")
        self._input(model, texts)
        if len(texts) > self.limits.batch_size:
            fail(400, "provider_input", "Zu viele Texte in einer Anfrage.")
        path = "/embeddings"
        payload = {"model": model, "input": texts, "encoding_format": "float"}
        if self.provider.kind == "ollama":
            path = "/api/embed"
            payload = {"model": model, "input": texts, "truncate": False}
        async with self._response("POST", path, payload) as response:
            data = await self._json(response)
            actual = model_name(data.get("model"))
            if self.provider.kind == "ollama":
                vectors = data["embeddings"]
            else:
                rows = data["data"]
                if not isinstance(rows, list) or len(rows) != len(texts):
                    invalid()
                indices = [r["index"] for r in rows]
                if any(type(i) is not int for i in indices) or sorted(indices) != list(
                    range(len(texts))
                ):
                    invalid()
                vectors = [r["embedding"] for r in sorted(rows, key=lambda r: r["index"])]
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                invalid()
            dimension = None
            for vector in vectors:
                if not isinstance(vector, list) or not 1 <= len(vector) <= self.limits.dimensions:
                    invalid()
                if dimension is not None and dimension != len(vector):
                    invalid()
                dimension = len(vector)
                if any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
                    invalid()
            return EmbeddingResult(actual, tuple(tuple(float(v) for v in row) for row in vectors))
