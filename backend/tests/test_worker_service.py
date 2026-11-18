import asyncio
from types import SimpleNamespace

import pytest
from app.workers import service


@pytest.mark.parametrize(
    "release, healthy, status", [(None, True, 503), ("a" * 40, False, 503), ("a" * 40, True, 200)]
)
def test_internal_worker_readiness_requires_release_and_live_supervisor(
    monkeypatch, release, healthy, status
):
    monkeypatch.setattr(service, "image_release", lambda: release)
    monkeypatch.setattr(
        service.app.state, "worker", SimpleNamespace(is_ready=lambda: healthy), raising=False
    )
    result = asyncio.run(service.ready())
    assert result.status_code == status
    assert result.headers["cache-control"] == "no-store"
