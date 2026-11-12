"""Trusted private Supabase Storage I/O; never accepts browser object keys.

Administrator configuration only. Each network action needs fresh scoped
application authorization; a Storage service token does NOT replace that check.
No filesystem/memory fallback, public/signed URLs, deletes or parser credentials.
"""
import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import SecretStr

from app.ingestion.text import MAX_UPLOAD_BYTES


class StorageError(RuntimeError):
    def __init__(self, code: str = "storage_unavailable"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ObjectScope:
    workspace_id: UUID
    notebook_id: UUID
    document_id: UUID
    version_id: UUID

    def __post_init__(self):
        if any(not isinstance(v, UUID) for v in (
            self.workspace_id, self.notebook_id, self.document_id, self.version_id,
        )):
            raise ValueError("UUID scope required")

    @property
    def key(self) -> str:
        return (f"{self.workspace_id}/{self.notebook_id}/{self.document_id}/"
                f"{self.version_id}/original")


@dataclass(frozen=True)
class StorageConfig:
    endpoint: str
    bucket: str
    token: SecretStr = field(repr=False)
    allow_internal_http: bool = False

    def __post_init__(self):
        url = urlsplit(self.endpoint)
        if (not url.hostname or url.username or url.password or url.query or url.fragment
                or url.scheme not in ("https", "http")
                or (url.scheme == "http" and not self.allow_internal_http)
                or not url.path.endswith("/storage/v1")
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", self.bucket)
                or not self.token.get_secret_value()):
            raise ValueError("invalid administrator Storage configuration")


Authorize = Callable[[UUID, ObjectScope, Literal["read", "write"]], Awaitable[None]]


class SupabaseStorage:
    def __init__(self, config: StorageConfig | None, *, authorize: Authorize,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._config = config
        self._authorize = authorize
        # Explicit test seam only. Production must leave transport=None.
        self._transport = transport

    async def read(self, actor_id: UUID, scope: ObjectScope) -> bytes:
        return await self._perform(actor_id, scope, "read")

    async def put(self, actor_id: UUID, scope: ObjectScope, data: bytes,
                  media_type: str) -> None:
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise StorageError("upload_too_large")
        if media_type not in ("text/plain", "text/markdown"):
            raise StorageError("unsupported_media")
        await self._perform(actor_id, scope, "write", data, media_type)

    async def _perform(self, actor: UUID, scope: ObjectScope, action: Literal["read", "write"],
                       data: bytes | None = None, media_type: str = "text/plain") -> bytes:
        config = self._config
        if config is None:
            raise StorageError("storage_unconfigured")
        try:
            async with asyncio.timeout(30), httpx.AsyncClient(
                timeout=httpx.Timeout(10, connect=5, pool=2), follow_redirects=False,
                trust_env=False, transport=self._transport,
                headers={"authorization": f"Bearer {config.token.get_secret_value()}",
                         "apikey": config.token.get_secret_value(), "accept-encoding": "identity"},
            ) as client:
                await self._authorize(actor, scope, action)
                bucket = await self._request(client, "GET", f"{config.endpoint}/bucket/{config.bucket}",
                                             limit=16 * 1024)
                info = json.loads(bucket)
                if (not isinstance(info, dict) or info.get("public") is not False
                        or info.get("id") != config.bucket):
                    raise StorageError("storage_not_private")
                await self._authorize(actor, scope, action)
                if action == "read":
                    result = await self._request(
                        client, "GET", f"{config.endpoint}/object/authenticated/{config.bucket}/{scope.key}",
                        limit=MAX_UPLOAD_BYTES,
                    )
                else:
                    result = await self._request(
                        client, "POST", f"{config.endpoint}/object/{config.bucket}/{scope.key}",
                        limit=16 * 1024, content=data,
                        headers={"content-type": media_type, "x-upsert": "false"},
                    )
                    info = json.loads(result)
                    if not isinstance(info, dict) or info.get("Key") != f"{config.bucket}/{scope.key}":
                        raise StorageError()
                # Revocation during I/O discards data, never passes it to parsing/publication.
                await self._authorize(actor, scope, action)
                return result
        except StorageError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, RecursionError):
            raise StorageError() from None

    @staticmethod
    async def _request(client: httpx.AsyncClient, method: str, url: str, *, limit: int,
                       content: bytes | None = None, headers: dict[str, str] | None = None) -> bytes:
        async with client.stream(method, url, content=content, headers=headers) as response:
            if response.status_code not in (200, 201) or response.headers.get("content-encoding", "identity") != "identity":
                raise StorageError()
            result = bytearray()
            async for block in response.aiter_bytes():
                if len(result) + len(block) > limit:
                    raise StorageError("storage_response_too_large")
                result.extend(block)
            return bytes(result)
