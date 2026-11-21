import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.workers import service
from app.workers.delegation import BoundedThreadRunner
from app.workers.local_runtime import LocalTextRuntime, build_consumer
from fastapi import FastAPI


def test_service_factory_constructs_real_supervisor_without_changing_one_shot():
    runtime = Mock(spec=LocalTextRuntime)
    runtime.app_engine = Mock()
    runner = BoundedThreadRunner()
    assert build_consumer(runtime, runner).worker.work_observation is False
    assert service.build_supervisor(runtime, runner).consumer.worker.work_observation is True


@pytest.mark.parametrize("quiescent,unknown", [(False, False), (True, True), (True, False)])
def test_stop_report_controls_shutdown_and_disposal(monkeypatch, quiescent, unknown):
    runtime = Mock()
    supervisor = SimpleNamespace(
        run=AsyncMock(),
        close=AsyncMock(return_value=SimpleNamespace(quiescent=quiescent, outcome_unknown=unknown)),
    )
    monkeypatch.setattr(service, "configured_runtime", lambda: runtime)
    monkeypatch.setattr(service, "build_supervisor", lambda *_: supervisor)

    async def exercise():
        async with service.lifespan(FastAPI()):
            await asyncio.sleep(0)

    if quiescent and not unknown:
        asyncio.run(exercise())
    else:
        with pytest.raises(RuntimeError, match="worker_shutdown_unconfirmed"):
            asyncio.run(exercise())
    assert runtime.dispose.called is quiescent
