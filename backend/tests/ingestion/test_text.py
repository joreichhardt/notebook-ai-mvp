"""Pure text seam; no parser, network, secrets or database."""
import pytest
from app.ingestion.text import TextRejected, chunks, read_text_upload


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Reader:
    def __init__(self, value):
        self.value = value

    async def read(self, size):
        result, self.value = self.value[:size], self.value[size:]
        return result


@pytest.mark.anyio
async def test_utf8_markdown_and_deterministic_offsets():
    upload = await read_text_upload(Reader(("ä" * 2200).encode()), "../report.md", "text/markdown")
    assert upload.filename == "report.md"
    assert upload.media_type == "text/markdown"
    parts = chunks(upload.text)
    assert [(p.start, p.end) for p in parts] == [(0, 1200), (1000, 2200)]
    assert parts[0].text[-200:] == parts[1].text[:200]
    assert chunks(upload.text) == parts


@pytest.mark.anyio
@pytest.mark.parametrize("data,name,media,code", [
    (b"%PDF-1.7", "x.pdf", "application/pdf", "unsupported_media"),
    (b"abc\x00xyz", "x.txt", "text/plain", "invalid_text"),
    (b"\xff", "x.txt", "text/plain", "invalid_text"),
    (b"abc\x01", "x.md", "text/markdown", "invalid_text"),
    (b"\t\n", "x.txt", "text/plain", "empty_text"),
    (b"hello", "x.exe", "text/plain", "unsupported_media"),
    (b"hello", "x.txt", "application/octet-stream", "unsupported_media"),
])
async def test_rejects_unsafe_or_unsupported_text(data, name, media, code):
    with pytest.raises(TextRejected) as error:
        await read_text_upload(Reader(data), name, media)
    assert error.value.code == code


@pytest.mark.anyio
async def test_actual_bytes_and_aggregate_characters_are_bounded():
    with pytest.raises(TextRejected, match="upload_too_large"):
        await read_text_upload(Reader(b"x" * (25 * 1024 * 1024 + 1)), "x.txt", "text/plain")
    with pytest.raises(TextRejected, match="text_too_large"):
        await read_text_upload(Reader(b"x" * 2_000_001), "x.txt", "text/plain")


@pytest.mark.anyio
async def test_display_name_removes_path_and_format_controls():
    upload = await read_text_upload(Reader(b"safe"), "C:\\fake\\a\u202etxt.md", "text/markdown")
    assert upload.filename == "atxt.md"
