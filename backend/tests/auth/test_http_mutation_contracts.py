"""Public DB-only route contracts; synthetic identity DI, real SCRAM runtime SQL."""

from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from app import mutations
from app.auth.supabase import Identity
from app.db import session as session_layer
from app.main import app
from fastapi import HTTPException
from sqlalchemy import event
from test_uploads import URL

pytest_plugins = ["test_http_mutations"]
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL required")

OPERATIONS = [
    ("POST", "/api/workspaces/{workspace}/notebooks", {"title": "denied"}),
    ("PATCH", "/api/notebooks/{notebook}", {"title": "denied"}),
    ("DELETE", "/api/notebooks/{notebook}", None),
    ("POST", "/api/invitations", {"email": "next@example.test", "role": "viewer"}),
    ("POST", "/api/invitations/accept", {"token": "a" * 32}),
    ("PATCH", "/api/workspaces/{workspace}/members/{user}", {"role": "viewer"}),
    ("DELETE", "/api/workspaces/{workspace}/members/{user}", None),
]


def path_for(target, scope):
    return target.format(workspace=scope[4], notebook=scope[5], user=scope[1])


@pytest.mark.parametrize("method,target,payload", OPERATIONS)
@pytest.mark.parametrize("fault", ["missing_origin", "wrong_origin", "missing_csrf", "wrong_csrf"])
def test_csrf_origin_refusal_never_invokes_runner(
    http_runtime, monkeypatch, method, target, payload, fault
):
    client, scope = http_runtime
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("CSRF refusal invoked mutation runner")

    monkeypatch.setattr(mutations.ActorMutations, "run", forbidden)
    if fault == "missing_origin":
        del client.headers["origin"]
    elif fault == "wrong_origin":
        client.headers["origin"] = "https://foreign.invalid"
    elif fault == "missing_csrf":
        del client.headers["x-csrf-token"]
    else:
        client.headers["x-csrf-token"] = "mismatch"
    response = client.request(method, path_for(target, scope), json=payload)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == (
        "origin_denied" if "origin" in fault else "csrf_denied"
    )
    assert calls == []


@pytest.mark.parametrize("method,target,payload", OPERATIONS)
@pytest.mark.parametrize("status,code", [(401, "invalid_identity"), (403, "email_unverified")])
def test_synthetic_identity_refusal_opens_no_actor_transaction(
    http_runtime, monkeypatch, method, target, payload, status, code
):
    client, scope = http_runtime
    calls = []

    def refused_identity():
        raise HTTPException(status, detail={"code": code, "message": "synthetic identity denial"})

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("identity refusal opened actor transaction")

    app.dependency_overrides[session_layer.identity] = refused_identity
    monkeypatch.setattr(mutations, "authenticated_session", forbidden)
    response = client.request(method, path_for(target, scope), json=payload)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert calls == []


@pytest.mark.parametrize(
    "case", ["invite_forbidden", "invite_invalid", "member_forbidden", "member_invalid"]
)
def test_real_rpc_denial_preserves_sqlstate_mapping_without_retry(http_runtime, monkeypatch, case):
    client, scope = http_runtime
    attempts = []
    errors = []
    original = mutations.authenticated_session
    engine = session_layer.engine()

    def counted(user):
        attempts.append(user.id)
        yield from original(user)

    def observe(context):
        error = context.original_exception
        if isinstance(error, psycopg.Error):
            errors.append(error.sqlstate)

    monkeypatch.setattr(mutations, "authenticated_session", counted)
    event.listen(engine, "handle_error", observe)
    try:
        if case.endswith("forbidden"):
            # Admitted editor, not owner: current SQL RPC must produce42501.
            app.dependency_overrides[session_layer.identity] = lambda: Identity(
                scope[1], "editor@example.test"
            )
        if case == "invite_forbidden":
            response = client.post(
                "/api/invitations",
                json={
                    "workspace_id": str(scope[4]),
                    "email": "target@example.test",
                    "role": "viewer",
                },
            )
        elif case == "invite_invalid":
            response = client.post("/api/invitations/accept", json={"token": "z" * 32})
        else:
            target = scope[0] if case == "member_forbidden" else uuid4()
            response = client.patch(
                f"/api/workspaces/{scope[4]}/members/{target}", json={"role": "viewer"}
            )
        forbidden = case.endswith("forbidden")
        assert response.status_code == (403 if forbidden else 409)
        assert response.json()["detail"]["code"] == (
            "permission_denied" if forbidden else "invitation_or_membership_invalid"
        )
        assert errors == ["42501" if forbidden else "22023"]
        assert len(attempts) == 1
        with psycopg.connect(URL) as admin:
            assert admin.execute(
                "SELECT role FROM workspace_members WHERE workspace_id=%s AND user_id=%s",
                (scope[4], scope[0]),
            ).fetchone() == ("owner",)
            assert admin.execute(
                "SELECT count(*) FROM invitations WHERE created_by=ANY(%s)", ([scope[0], scope[1]],)
            ).fetchone() == (0,)
    finally:
        event.remove(engine, "handle_error", observe)


def test_public_actor_mutations_override_is_used_without_hidden_transaction(
    http_runtime, monkeypatch
):
    client, scope = http_runtime
    calls = []

    class Injected:
        def run(self, operation, **kwargs):
            calls.append(operation)
            return {"id": "synthetic-di", "title": "override", "workspace_id": str(scope[4])}

    def forbidden(*args, **kwargs):
        raise AssertionError("actor mutation override was bypassed")

    app.dependency_overrides[mutations.actor_mutations] = Injected
    monkeypatch.setattr(mutations, "authenticated_session", forbidden)
    response = client.post(f"/api/workspaces/{scope[4]}/notebooks", json={"title": "ignored"})
    assert response.status_code == 201
    assert response.json()["id"] == "synthetic-di"
    assert len(calls) == 1


def test_connections_keeps_user_session_override_without_actor_runner(http_runtime, monkeypatch):
    client, _scope = http_runtime
    lifecycle = []

    def injected_session():
        lifecycle.append("enter")
        try:
            yield SimpleNamespace(scalars=lambda statement: [])
        finally:
            lifecycle.append("exit")

    def forbidden(*args, **kwargs):
        raise AssertionError("Connections read unexpectedly entered mutation runner")

    app.dependency_overrides[session_layer.user_session] = injected_session
    monkeypatch.setattr(mutations.ActorMutations, "run", forbidden)
    monkeypatch.setattr(session_layer, "engine", forbidden)
    response = client.get("/api/connections")
    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert lifecycle == ["enter", "exit"]
