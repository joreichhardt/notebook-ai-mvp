"""Route-local raw ASGI budget, before any multipart parser can spool bytes."""

import asyncio

import pytest
from app.documents.body_limit import RawBodyBudget, UploadBodyError
from starlette.requests import Request


def test_counts_actual_unknown_or_lying_length_before_delivering_oversized_chunk():
    async def run():
        messages = iter(
            [
                {"type": "http.request", "body": b"12345678", "more_body": True},
                {"type": "http.request", "body": b"123456789", "more_body": False},
            ]
        )

        async def receive():
            return next(messages)

        budget = RawBodyBudget(receive, max_bytes=16)
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "headers": [
                    (b"content-length", b"1"),
                ],
            },
            receive=budget,
        )
        delivered = []
        with pytest.raises(UploadBodyError, match="upload_too_large") as error:
            async for block in request.stream():
                delivered.append(block)
        assert error.value.status_code == 413
        assert delivered == [b"12345678"]

    asyncio.run(run())


def test_exact_envelope_budget_and_disconnect_are_not_successful_extra_body():
    async def exact():
        async def receive():
            return {"type": "http.request", "body": b"12345678", "more_body": False}

        budget = RawBodyBudget(receive, max_bytes=8)
        assert (await budget())["body"] == b"12345678"
        assert budget.total == 8

    asyncio.run(exact())

    async def disconnected():
        async def receive():
            return {"type": "http.disconnect"}

        with pytest.raises(UploadBodyError, match="upload_interrupted"):
            await RawBodyBudget(receive)()

    asyncio.run(disconnected())


def test_cancelled_or_timed_out_receive_stops_without_more_reads():
    async def run():
        stopped = asyncio.Event()

        async def receive():
            try:
                await asyncio.Event().wait()
                return {"type": "http.disconnect"}
            finally:
                stopped.set()

        with pytest.raises(UploadBodyError, match="upload_timeout"):
            await RawBodyBudget(receive, timeout_seconds=0.01)()
        assert stopped.is_set()
        stopped.clear()
        task = asyncio.create_task(RawBodyBudget(receive)())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()

    asyncio.run(run())
