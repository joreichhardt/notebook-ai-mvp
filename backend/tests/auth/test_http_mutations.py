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


@pytest.mark.parametrize(
    "operation,method,target,payload",
    [
        ("create", "POST", "/api/workspaces/{workspace}/notebooks", {"title": "must not commit"}),
        ("rename", "PATCH", "/api/notebooks/{notebook}", {"title": "must not commit"}),
        ("delete", "DELETE", "/api/notebooks/{notebook}", None),
        (
            "invite",
            "POST",
            "/api/invitations",
            {"email": "next@example.test", "role": "viewer"},
        ),
        ("accept", "POST", "/api/invitations/accept", None),
        ("change", "PATCH", "/api/workspaces/{workspace}/members/{user}", {"role": "viewer"}),
        ("remove", "DELETE", "/api/workspaces/{workspace}/members/{user}", None),
    ],
)
def test_mutation_busy_is_opaque_503_without_mutation(
    http_runtime, operation, method, target, payload
):
    from uuid import uuid4

    client, scope = http_runtime
    path = f"/api/workspaces/{scope[4]}/notebooks"
    members_path = f"/api/workspaces/{scope[4]}/members"
    before = client.get(path)
    before_members = client.get(members_path)
    assert before.status_code == before_members.status_code == 200
    if operation == "invite":
        payload = {**payload, "workspace_id": str(scope[4])}
    elif operation == "accept":
        # A verified but NOT admitted identity must reach acceptance, not be
        # rejected by the ordinary mutation admission requirement.
        app.dependency_overrides[session_layer.identity] = lambda: Identity(
            uuid4(), "http-mutation@example.test"
        )
        payload = {"token": secrets.token_urlsafe(32)}
    target = target.format(workspace=scope[4], notebook=scope[5], user=scope[1])
    # This synthetic operator holds only its own cluster's fixed transaction gate.
    # The HTTP request still uses the actual direct nonprivileged notebook_app login.
    with psycopg.connect(URL) as holder:
        holder.execute("SELECT public.quota_gate()")
        response = client.request(method, target, json=payload)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "quota_busy"
    assert "quota_gate_busy" not in response.text
    app.dependency_overrides[session_layer.identity] = lambda: Identity(
        scope[0], "http-mutation@example.test"
    )
    assert client.get(path).json() == before.json()
    assert client.get(members_path).json() == before_members.json()
