"""Explicit bounded multipart-to-text boundary, not an authenticated HTTP route.

Caller MUST authenticate/authorize/CSRF-check before invoking. Uses locked
Starlette1.10 parser hooks; compatibility and spool cleanup tests are required
on upgrades. No Storage/DB/credential access, PDF parsing or persistence here.
"""

import asyncio
import re

from python_multipart.multipart import parse_options_header
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import Request

from app.documents.body_limit import RawBodyBudget, UploadBodyError
from app.ingestion.text import MAX_UPLOAD_BYTES, TextRejected, TextUpload, read_text_upload


class _TextMultipart(MultiPartParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, max_files=1, max_fields=0, max_part_size=1024, **kwargs)
        self.completed = False
        self._part_bytes = 0
        self._header_bytes = 0
        self._header_count = 0

    def on_part_begin(self):
        super().on_part_begin()
        self._part_bytes = self._header_bytes = self._header_count = 0

    def on_part_data(self, data: bytes, start: int, end: int):
        self._part_bytes += end - start
        if self._part_bytes > MAX_UPLOAD_BYTES:
            raise UploadBodyError("upload_too_large", 413)
        super().on_part_data(data, start, end)

    def on_header_field(self, data: bytes, start: int, end: int):
        self._header_bytes += end - start
        if self._header_bytes > 8192:
            raise UploadBodyError("invalid_upload", 400)
        super().on_header_field(data, start, end)

    def on_header_value(self, data: bytes, start: int, end: int):
        self._header_bytes += end - start
        if self._header_bytes > 8192:
            raise UploadBodyError("invalid_upload", 400)
        super().on_header_value(data, start, end)

    def on_header_end(self):
        self._header_count += 1
        if self._header_count > 8:
            raise UploadBodyError("invalid_upload", 400)
        super().on_header_end()

    def on_headers_finished(self):
        headers = self._current_part.item_headers
        names = [k for k, _ in headers]
        if len(names) != len(set(names)) or set(names) != {b"content-disposition", b"content-type"}:
            raise UploadBodyError("invalid_upload", 400)
        disposition, options = parse_options_header(dict(headers)[b"content-disposition"])
        if (
            disposition != b"form-data"
            or options.get(b"name") != b"file"
            or set(options) != {b"name", b"filename"}
            or not 1 <= len(options[b"filename"]) <= 1024
        ):
            raise UploadBodyError("invalid_upload", 400)
        if dict(headers)[b"content-type"] not in (b"text/plain", b"text/markdown"):
            raise UploadBodyError("unsupported_media", 415)
        super().on_headers_finished()

    def on_end(self):
        self.completed = True

    def close_spools(self):
        # The parser owns every spool, including incomplete/unreturned parts.
        # Synchronous close in finally cannot itself be interrupted by task cancellation.
        for spool in self._files_to_close_on_error:
            spool.close()


async def parse_text_form(request: Request) -> TextUpload:
    """Read one TXT/MD file only, with actual envelope/file limits and guaranteed close."""
    headers = request.scope.get("headers", [])
    if (
        len(headers) > 32
        or sum(len(k) + len(v) for k, v in headers) > 8192
        or sum(k.lower() == b"content-type" for k, _ in headers) != 1
    ):
        raise UploadBodyError("invalid_upload", 400)
    media, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary", b"")
    if (
        media != b"multipart/form-data"
        or set(params) != {b"boundary"}
        or re.fullmatch(rb"[A-Za-z0-9'()+_,./:=?-]{1,70}", boundary) is None
    ):
        raise UploadBodyError("unsupported_media", 415)
    bounded = Request(request.scope, receive=RawBodyBudget(request.receive))
    parser = _TextMultipart(request.headers, bounded.stream())
    try:
        async with asyncio.timeout(60):
            form = await parser.parse()
            if not parser.completed or len(form.multi_items()) != 1:
                raise UploadBodyError("invalid_upload", 400)
            original = form.get("file")
            if not isinstance(original, UploadFile):
                raise UploadBodyError("invalid_upload", 400)
            return await read_text_upload(
                original, original.filename or "", original.content_type or ""
            )
    except MultiPartException:
        raise UploadBodyError("invalid_upload", 400) from None
    except TextRejected as exc:
        status = (
            415
            if exc.code == "unsupported_media"
            else 413
            if exc.code.endswith("too_large")
            else 422
        )
        raise UploadBodyError(exc.code, status) from None
    except TimeoutError:
        raise UploadBodyError("upload_timeout", 408) from None
    finally:
        parser.close_spools()
