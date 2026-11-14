"""Synthetic readiness contracts; not live dependency or release acceptance."""

import asyncio

import pytest
from app import health
from app.main import app
from fastapi.testclient import TestClient
from starlette.datastructures import State


def test_unassembled_runtime_is_not_ready():
    response = TestClient(app).get("/api/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert response.headers["cache-control"] == "no-store"


def test_liveness_stays_independent():
    assert TestClient(app).get("/api/health/live").json() == {"status": "ok"}


@pytest.fixture
def assembled(monkeypatch, tmp_path):
    stamp = tmp_path / "_release_sha"
    stamp.write_text("a" * 40)
    monkeypatch.setattr(health, "RELEASE_FILE", stamp)
    calls = []

    async def available():
        calls.append(True)
        return True

    probes = dict.fromkeys(health.REQUIRED_PROBES, available)
    monkeypatch.setattr(app, "state", State({"readiness": health.Readiness(probes)}))
    return stamp, probes, calls


def test_only_complete_synthetic_runtime_can_report_its_image_commit(assembled):
    _, _, calls = assembled
    client = TestClient(app)
    for _ in range(2):
        response = client.get("/api/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready", "release": "a" * 40}
        assert response.headers["cache-control"] == "no-store"
    assert len(calls) == 2 * len(health.REQUIRED_PROBES)  # no success cache


@pytest.mark.parametrize("value", ["", "a" * 39, "a" * 41, "A" * 40, "a" * 40 + "\n", "é" * 40])
def test_invalid_image_stamp_never_probes_or_becomes_ready(assembled, value):
    stamp, _, calls = assembled
    stamp.write_text(value)
    response = TestClient(app).get("/api/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert calls == []


@pytest.mark.parametrize("kind", ["absent", "directory", "symlink"])
def test_missing_or_indirect_stamp_fails_closed(assembled, tmp_path, kind):
    stamp, _, calls = assembled
    stamp.unlink()
    if kind == "directory":
        stamp.mkdir()
    elif kind == "symlink":
        target = tmp_path / "another-stamp"
        target.write_text("a" * 40)
        stamp.symlink_to(target)
    assert TestClient(app).get("/api/health/ready").status_code == 503
    assert calls == []


def test_environment_query_and_header_cannot_replace_image_metadata(assembled, monkeypatch):
    stamp, _, calls = assembled
    stamp.unlink()
    monkeypatch.setenv("NOTEBOOK_RELEASE_SHA", "b" * 40)
    response = TestClient(app).get(
        "/api/health/ready?release=" + "b" * 40,
        headers={"X-Release-Sha": "b" * 40},
    )
    assert response.status_code == 503
    assert calls == []


@pytest.mark.parametrize("missing", sorted(health.REQUIRED_PROBES))
def test_each_dependency_is_mandatory(assembled, monkeypatch, missing):
    _, probes, calls = assembled
    del probes[missing]
    app.state.readiness = health.Readiness(probes)
    assert TestClient(app).get("/api/health/ready").status_code == 503
    assert calls == []


@pytest.mark.parametrize("value", [False, None, 1, "ready", {"status": "ready"}])
def test_truthy_or_failed_probe_is_not_success(assembled, monkeypatch, value):
    _, probes, _ = assembled

    async def failed():
        return value

    probes["database"] = failed
    app.state.readiness = health.Readiness(probes)
    assert TestClient(app).get("/api/health/ready").status_code == 503


def test_probe_failure_is_opaque_and_not_logged(assembled, monkeypatch, caplog):
    _, probes, _ = assembled

    async def failed():
        raise health.ProbeUnavailable("synthetic diagnostic must stay private")

    probes["auth"] = failed
    app.state.readiness = health.Readiness(probes)
    response = TestClient(app).get("/api/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert "synthetic diagnostic" not in caplog.text


def test_timeout_cancels_cooperative_probe_and_cannot_report_ready(assembled, monkeypatch):
    _, probes, _ = assembled
    cancelled = []

    async def slow():
        try:
            await asyncio.sleep(60)
            return True
        finally:
            cancelled.append(True)

    probes["worker"] = slow
    monkeypatch.setattr(health, "PROBE_TIMEOUT_SECONDS", 0.01)
    app.state.readiness = health.Readiness(probes)
    assert TestClient(app).get("/api/health/ready").status_code == 503
    assert cancelled == [True]


def test_probe_set_is_a_snapshot_not_a_mutable_callers_dict(assembled):
    _, probes, _ = assembled
    probes.clear()
    assert TestClient(app).get("/api/health/ready").status_code == 200


def test_arbitrary_state_cannot_claim_readiness(assembled):
    app.state.readiness = {"status": "ready"}
    assert TestClient(app).get("/api/health/ready").status_code == 503


def test_cloudflare_consumer_rejects_default_runtime_even_with_release_stamp(assembled):
    from deploy.cloudflare.probes import app_check
    from deploy.cloudflare.routing import RoutingError

    del app.state.readiness
    client = TestClient(app)

    def fetch(origin, path, *, address=None):
        response = client.get(path)
        return response.status_code, response.headers, response.content

    with pytest.raises(RoutingError, match="readiness_failed"):
        app_check("http://127.0.0.1:18194", "a" * 40, fetch=fetch)


def test_cloudflare_consumer_rejects_mismatched_image_release(assembled):
    from deploy.cloudflare.probes import app_check
    from deploy.cloudflare.routing import RoutingError

    client = TestClient(app)

    def fetch(origin, path, *, address=None):
        response = client.get(path)
        return response.status_code, response.headers, response.content

    with pytest.raises(RoutingError, match="readiness_failed"):
        app_check("http://127.0.0.1:18194", "b" * 40, fetch=fetch)


def test_unexpected_probe_bug_cancels_siblings_and_never_returns_ready(assembled, caplog):
    _, probes, _ = assembled
    cancelled = []

    async def slow():
        try:
            await asyncio.sleep(60)
            return True
        finally:
            cancelled.append(True)

    async def broken():
        # Programming error, not a sanitized dependency failure.
        await asyncio.sleep(0)
        raise TypeError("synthetic programming error")

    probes["storage"] = slow
    probes["auth"] = broken
    app.state.readiness = health.Readiness(probes)
    response = TestClient(app, raise_server_exceptions=False).get("/api/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert response.headers["cache-control"] == "no-store"
    assert "synthetic programming error" not in caplog.text
    assert cancelled == [True]


def test_cancelled_dependency_cannot_be_ready(assembled):
    _, probes, _ = assembled

    async def cancelled():
        raise asyncio.CancelledError

    probes["worker"] = cancelled
    app.state.readiness = health.Readiness(probes)
    assert TestClient(app).get("/api/health/ready").status_code == 503


def test_extra_probe_name_is_not_a_supported_runtime_profile(assembled):
    _, probes, calls = assembled
    probes["liveness"] = probes["database"]
    app.state.readiness = health.Readiness(probes)
    assert TestClient(app).get("/api/health/ready").status_code == 503
    assert calls == []


def test_caller_cancellation_is_not_swallowed(assembled):
    _, probes, _ = assembled

    async def slow():
        await asyncio.sleep(60)
        return True

    probes["worker"] = slow

    async def exercise():
        task = asyncio.create_task(health.Readiness(probes).check())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
