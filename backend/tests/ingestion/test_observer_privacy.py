"""Observer failures must not retain private exception context or depend on logging."""

import asyncio
import logging
import traceback
from unittest.mock import Mock

import pytest
from app.workers.text import ObservationUnavailable, TextPhase, TextProgress, TextWorker
from sqlalchemy import Engine

CANARY = "observer-private-canary"


def observer_for(fault):
    def fail(progress):
        if fault == "exception":
            raise RuntimeError(CANARY)
        if fault == "cancel":
            raise asyncio.CancelledError(CANARY)
        if fault == "return":
            return CANARY

        async def unexpected():
            try:
                await asyncio.sleep(0)
            finally:
                raise RuntimeError(CANARY)

        coroutine = unexpected()
        coroutine.send(None)
        return coroutine

    return Mock(side_effect=fail)


@pytest.mark.parametrize("fault", ["exception", "cancel", "return", "coroutine_close"])
@pytest.mark.parametrize("phase", ["ingestion_claim", "pulse", "publish", "finish"])
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("broken_logger", [False, True])
def test_observer_boundary_clears_context_and_preserves_truth(
    monkeypatch, capsys, caplog, fault, phase: TextPhase, committed, broken_logger
):
    async def runner(operation, *args):
        return operation(*args)

    worker = TextWorker(
        Mock(spec=Engine), operation_runner=runner, progress_observer=observer_for(fault)
    )

    def logger_failed(*args, **kwargs):
        raise RuntimeError("logger-private-canary")

    with monkeypatch.context() as patches:
        if broken_logger:
            patches.setattr(logging.Logger, "handle", logger_failed)
        if committed and phase in ("publish", "finish"):
            worker._commit_progress(phase, 1.0)
        else:
            with pytest.raises(ObservationUnavailable) as caught:
                if committed:
                    worker._commit_progress(phase, 1.0)
                else:
                    worker._observe(TextProgress(phase, 1.0, None), committed=False)
            error = caught.value
            assert error.__context__ is None
            assert error.__cause__ is None
            assert str(error) == "observation_unavailable"
            assert CANARY not in "".join(traceback.format_exception(error))

    assert worker.observation_failed
    assert worker.terminal_committed is (committed and phase in ("publish", "finish"))
    output = capsys.readouterr()
    assert output.out == output.err == ""
    assert caplog.records == []
