"""Real locked multipart parser; no Storage/DB/identity fallback or HTTP registration."""

import asyncio

import pytest
from app.documents.body_limit import UploadBodyError
from app.documents.multipart import parse_text_form
from starlette.requests import Request


def body(data=b"hello", *, name="file", filename="sample.txt", media="text/plain", end=True):
    value = (
        (
            f'--b3-boundary\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {media}\r\n\r\n"
        ).encode()
        + data
        + b"\r\n"
    )
    return value + (b"--b3-boundary--\r\n" if end else b"")


def request_for(payload, *, declared=b"1", receive=None):
    async def complete():
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [
                (b"content-type", b"multipart/form-data; boundary=b3-boundary"),
                (b"content-length", declared),
            ],
        },
        receive=receive or complete,
    )


def spools(monkeypatch):
    from tempfile import SpooledTemporaryFile

    import starlette.formparsers as parser

    real = SpooledTemporaryFile
    opened = []

    def tracked(*args, **kwargs):
        result = real(*args, **kwargs)
        opened.append(result)
        return result

    monkeypatch.setattr(parser, "SpooledTemporaryFile", tracked)
    return opened


def test_single_utf8_file_and_spools_closed_on_success(monkeypatch):
    opened = spools(monkeypatch)
    result = asyncio.run(parse_text_form(request_for(body("Grüße".encode()))))
    assert result.text == "Grüße" and result.filename == "sample.txt"
    assert len(opened) == 1 and all(f.closed for f in opened)


@pytest.mark.parametrize(
    "payload,code",
    [
        (body(name="foreign"), "invalid_upload"),
        (body(end=False) + body(), "invalid_upload"),
        (body(end=False), "invalid_upload"),
        (body(media="application/pdf", filename="x.pdf"), "unsupported_media"),
        (body(b"abc\x00"), "invalid_text"),
        (body(filename="x" * 1025), "invalid_upload"),
    ],
)
def test_malformed_unexpected_or_unsafe_parts_close_every_spool(monkeypatch, payload, code):
    opened = spools(monkeypatch)
    with pytest.raises(UploadBodyError, match=code):
        asyncio.run(parse_text_form(request_for(payload)))
    assert all(f.closed for f in opened)


def test_actual_file_limit_not_declared_length_or_starlette_field_limit(monkeypatch):
    opened = spools(monkeypatch)
    with pytest.raises(UploadBodyError) as error:
        asyncio.run(parse_text_form(request_for(body(b"x" * (25 * 1024 * 1024 + 1)))))
    assert (error.value.code, error.value.status_code) == ("upload_too_large", 413)
    assert all(f.closed for f in opened)


@pytest.mark.parametrize("mode", ["cancel", "cancel_disk", "disconnect", "raw_limit", "timeout"])
def test_incomplete_stream_or_cancellation_closes_spool(monkeypatch, mode):
    opened = spools(monkeypatch)
    if mode == "timeout":
        from app.documents.body_limit import RawBodyBudget

        monkeypatch.setattr(
            "app.documents.multipart.RawBodyBudget",
            lambda receive: RawBodyBudget(receive, timeout_seconds=0.03),
        )

    async def run():
        entered = asyncio.Event()
        calls = 0

        async def receive():
            nonlocal calls
            calls += 1
            if calls == 1:
                data = b"x" * 1_100_000 if mode == "cancel_disk" else b"hello"
                return {"type": "http.request", "body": body(data, end=False), "more_body": True}
            if mode == "cancel_disk":
                assert opened[0].name is not None  # Real rolled-to-disk spool, not memory only.
            entered.set()
            if mode == "disconnect":
                return {"type": "http.disconnect"}
            if mode == "raw_limit":
                return {
                    "type": "http.request",
                    "body": b"x" * (26 * 1024 * 1024),
                    "more_body": False,
                }
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        task = asyncio.create_task(parse_text_form(request_for(b"", receive=receive)))
        await entered.wait()
        if mode in ("cancel", "cancel_disk"):
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(UploadBodyError) as error:
                await task
            expected = {"disconnect": 400, "raw_limit": 413, "timeout": 408}
            assert error.value.status_code == expected[mode]
        assert opened and all(f.closed for f in opened)

    asyncio.run(run())


def test_chunked_unknown_length_handles_split_utf8_and_multipart_headers(monkeypatch):
    opened = spools(monkeypatch)

    async def run():
        payload = body("Grüße".encode())
        pieces = iter(payload[i : i + 3] for i in range(0, len(payload), 3))

        async def receive():
            chunk = next(pieces, b"")
            return {"type": "http.request", "body": chunk, "more_body": bool(chunk)}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "headers": [
                    (b"content-type", b"multipart/form-data; boundary=b3-boundary"),
                ],
            },
            receive=receive,
        )
        result = await parse_text_form(request)
        assert result.text == "Grüße"

    asyncio.run(run())
    assert opened and all(f.closed for f in opened)
