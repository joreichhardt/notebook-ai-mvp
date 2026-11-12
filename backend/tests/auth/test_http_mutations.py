"""Existing HTTP mutation seam, real restricted PostgreSQL, no external auth calls.

Verified identity is injected only in this isolated test. Database authorization,
CSRF, the public route and transaction/response ownership remain exercised.
"""

import secrets

import psycopg
import pytest
from app.auth.security import CSRF_COOKIE
from app.auth.settings import get_settings
from app.auth.supabase import Identity
from app.db import session as session_layer
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from test_uploads import URL

pytest_plugins = ["test_uploads"]
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL URL not configured")


@pytest.fixture
def http_runtime(scope, monkeypatch):
    # Passwordless URL: libpq uses the protected, per-role synthetic pgpass file.
    url = (
        make_url(URL)
        .set(drivername="postgresql+psycopg")
        ._replace(username="notebook_app", password=None)
        .difference_update_query(["password", "passfile", "user"])
    )
    assert url.password is None
    runtime = create_engine(url, pool_pre_ping=True)
    monkeypatch.setattr(session_layer, "engine", lambda: runtime)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[session_layer.identity] = lambda: Identity(
        scope[0], "http-mutation@example.test"
    )
    origin = get_settings().public_origin
    csrf = secrets.token_urlsafe(32)
    try:
        with TestClient(app, base_url=origin, raise_server_exceptions=False) as client:
            client.headers.update(
                {
                    "origin": origin,
                    "x-csrf-token": csrf,
                    "cookie": f"{CSRF_COOKIE}={csrf}",
                }
            )
            yield client, scope
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        runtime.dispose()


def test_notebook_busy_is_opaque_503_without_mutation(http_runtime):
    client, scope = http_runtime
    path = f"/api/workspaces/{scope[4]}/notebooks"
    before = client.get(path)
    assert before.status_code == 200
    # This synthetic operator holds only its own cluster's fixed transaction gate.
    # The HTTP request still uses the actual direct nonprivileged notebook_app login.
    with psycopg.connect(URL) as holder:
        holder.execute("SELECT public.quota_gate()")
        response = client.post(path, json={"title": "must not commit"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "quota_busy"
    assert "quota_gate_busy" not in response.text
    assert client.get(path).json() == before.json()
