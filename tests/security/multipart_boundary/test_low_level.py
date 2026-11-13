"""Pinned real parser boundary; synthetic bytes only, no DB or network access."""

import asyncio
from contextlib import ExitStack
from importlib import import_module
from tempfile import SpooledTemporaryFile

import httpx
import psycopg
import pytest
from app.documents.body_limit import UploadBodyError
from app.documents.multipart import parse_text_form
from app.documents.storage import SupabaseStorage
from sqlalchemy import Engine
from starlette import formparsers
from starlette.requests import Request

FILE_START = (
    b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="sample.txt"\r\n'
    b"Content-Type: text/plain\r\n\r\nhello\r\n"
)


def request_for(receive):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-type", b"multipart/form-data; boundary=boundary")],
        },
        receive=receive,
    )


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail("unexpected_external_io", pytrace=False)

    monkeypatch.setattr(Engine, "connect", forbidden)
    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(SupabaseStorage, "_perform", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    yield
    assert not calls


@pytest.fixture
def opened(monkeypatch):
    files = []
    with ExitStack() as cleanup:

        def track(*args, **kwargs):
            spool = cleanup.enter_context(SpooledTemporaryFile(*args, **kwargs))
            files.append(spool)
            return spool

        monkeypatch.setattr(formparsers, "SpooledTemporaryFile", track)
        yield files
        # Check parser ownership BEFORE fixture fallback closes anything.
        assert all(spool.closed for spool in files)


@pytest.mark.parametrize(
    "pieces,expected_spools",
    [
        ([b"--boundaryX\r\n"], 0),
        ([b"--boundary\r\nBad Header: value\r\n\r\n"], 0),
        ([FILE_START, b"--boundary\r\nBad Header: value\r\n\r\n"], 1),
    ],
    ids=["invalid_start", "invalid_header", "invalid_header_after_spool"],
)
def test_low_level_syntax_is_opaque_400(pieces, expected_spools, opened):
    stream = iter(pieces)

    async def receive():
        piece = next(stream, b"")
        return {"type": "http.request", "body": piece, "more_body": bool(piece)}

    with pytest.raises(UploadBodyError) as caught:
        asyncio.run(parse_text_form(request_for(receive)))
    assert (caught.value.status_code, caught.value.code) == (400, "invalid_upload")
    assert len(opened) == expected_spools
    assert all(spool.closed for spool in opened)
    # Identity only: verify these inputs exercised the actual low-level failure,
    # not merely our wrapper's missing-terminal-boundary validation.
    translated = caught.value.__context__
    assert type(translated) is formparsers.MultiPartException
    parser_errors = import_module("python_multipart.exceptions")
    assert type(translated.__cause__) is parser_errors.MultipartParseError


def test_cancellation_propagates_and_closes_existing_spool(opened):
    async def run():
        waiting = asyncio.Event()
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": FILE_START, "more_body": True}
            waiting.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(parse_text_form(request_for(receive)))
        try:
            await asyncio.wait_for(waiting.wait(), timeout=2)
            assert len(opened) == 1 and not opened[0].closed
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert opened[0].closed
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
