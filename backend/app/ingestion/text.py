"""Bounded UTF-8 TXT/Markdown only. No PDF parser or credential access."""

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Protocol
from unicodedata import category, normalize

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000


class TextRejected(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ByteReader(Protocol):
    async def read(self, size: int) -> bytes: ...


@dataclass(frozen=True)
class TextUpload:
    filename: str
    media_type: str
    data: bytes = field(repr=False)
    text: str = field(repr=False)
    sha256: str


@dataclass(frozen=True)
class TextChunk:
    start: int
    end: int
    text: str = field(repr=False)
    sha256: str


def decode_text(data: bytes) -> str:
    if len(data) > MAX_UPLOAD_BYTES:
        raise TextRejected("upload_too_large")
    if data.startswith((b"%PDF-", b"PK\x03\x04", b"\x89PNG", b"GIF8")):
        raise TextRejected("invalid_text")
    try:
        value = data.decode("utf-8", errors="strict")
    except UnicodeError:
        raise TextRejected("invalid_text") from None
    if len(value) > MAX_TEXT_CHARS:
        raise TextRejected("text_too_large")
    if any(category(c) == "Cc" and c not in "\t\r\n" for c in value):
        raise TextRejected("invalid_text")
    if not value.strip():
        raise TextRejected("empty_text")
    return value


async def read_text_upload(reader: ByteReader, filename: str, media_type: str) -> TextUpload:
    name = normalize("NFC", filename.replace("\\", "/").rsplit("/", 1)[-1])
    name = "".join(c for c in name if not category(c).startswith("C")).strip()[:255]
    extension = PurePosixPath(name).suffix.lower()
    expected = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown"}
    # No content sniffing can turn binary/PDF into accepted text merely by renaming it.
    media = media_type.lower().strip()
    if not name or extension not in expected or media != expected[extension]:
        raise TextRejected("unsupported_media")
    data = bytearray()
    while True:
        block = await reader.read(min(64 * 1024, MAX_UPLOAD_BYTES + 1 - len(data)))
        if not block:
            break
        if len(data) + len(block) > MAX_UPLOAD_BYTES:
            raise TextRejected("upload_too_large")
        data.extend(block)
    original = bytes(data)
    value = decode_text(original)
    return TextUpload(name, media, original, value, sha256(original).hexdigest())


def chunks(value: str) -> tuple[TextChunk, ...]:
    if not value or len(value) > MAX_TEXT_CHARS:
        raise TextRejected("text_too_large" if value else "empty_text")
    result = []
    for start in range(0, len(value), 1000):
        end = min(start + 1200, len(value))
        part = value[start:end]
        result.append(TextChunk(start, end, part, sha256(part.encode("utf-8")).hexdigest()))
        if end == len(value):
            break
    return tuple(result)
