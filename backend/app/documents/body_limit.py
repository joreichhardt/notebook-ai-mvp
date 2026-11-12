"""Raw route-local ASGI upload budget; use BEFORE constructing a multipart parser.

No request.body(), no Content-Length trust, no FastAPI automatic UploadFile
binding. The future route must additionally bound the actual file to25MiB,
validate parts/headers and close all parser spools in finally on ANY exception.
This limiter alone is not a multipart parser, upload route or spool-cleanup proof.
"""
import asyncio
from time import monotonic

from starlette.types import Message, Receive

MAX_MULTIPART_BYTES = 26 * 1024 * 1024


class UploadBodyError(ValueError):
    def __init__(self, code: str, status_code: int):
        self.code, self.status_code = code, status_code
        super().__init__(code)


class RawBodyBudget:
    def __init__(self, receive: Receive, *, max_bytes: int = MAX_MULTIPART_BYTES,
                 timeout_seconds: float = 60):
        if not 0 < max_bytes <= MAX_MULTIPART_BYTES or not 0 < timeout_seconds <= 60:
            raise ValueError("invalid bounded upload budget")
        self._receive = receive
        self._deadline = monotonic() + timeout_seconds
        self._max_bytes = max_bytes
        self.total = 0

    async def __call__(self) -> Message:
        remaining = self._deadline - monotonic()
        if remaining <= 0:
            raise UploadBodyError("upload_timeout", 408)
        try:
            async with asyncio.timeout(remaining):
                message = await self._receive()
        except TimeoutError:
            raise UploadBodyError("upload_timeout", 408) from None
        if message["type"] == "http.disconnect":
            raise UploadBodyError("upload_interrupted", 400)
        if message["type"] != "http.request":
            raise UploadBodyError("invalid_upload", 400)
        self.total += len(message.get("body", b""))
        if self.total > self._max_bytes:
            # Never deliver an oversized chunk to the parser/spool.
            raise UploadBodyError("upload_too_large", 413)
        return message
