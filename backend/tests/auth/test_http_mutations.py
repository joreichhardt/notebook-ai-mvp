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
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from test_uploads import URL

pytest_plugins = ["test_uploads"]
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL URL not configured")


@pytest.fixture
def http_runtime(scope, monkeypatch, tmp_path):
    from app.auth import invitations

    password_file = tmp_path / "smtp-fixture"
    password_file.write_text("synthetic-password")
    settings = invitations.get_settings().model_copy(update={
        "invitation_smtp_host": "smtp.example.test",
        "invitation_smtp_sender": "notebook@example.test",
        "invitation_smtp_password_file": str(password_file),
    })
    monkeypatch.setattr(invitations, "get_settings", lambda: settings)
    monkeypatch.setattr(invitations.InvitationMailer, "send", lambda *_: None)
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


def test_superuser_original_login_cannot_hide_behind_set_role(http_runtime, monkeypatch):
    client, scope = http_runtime
    path = f"/api/workspaces/{scope[4]}/notebooks"
    before = client.get(path).json()
    privileged = create_engine(make_url(URL).set(drivername="postgresql+psycopg"))

    @event.listens_for(privileged, "begin")
    def switch_role(connection):
        connection.exec_driver_sql("SET ROLE notebook_app")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(session_layer, "engine", lambda: privileged)
            response = client.post(path, json={"title": "unsafe original login"})
    finally:
        privileged.dispose()
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "unsafe_database_role"
    assert client.get(path).json() == before


def test_committed_notebook_and_invitation_flow_preserves_accept_without_admission(
    http_runtime, monkeypatch
):
    from uuid import uuid4

    client, scope = http_runtime
    path = f"/api/workspaces/{scope[4]}/notebooks"
    created = client.post(path, json={"title": "committed notebook"})
    assert created.status_code == 201
    notebook = created.json()["id"]
    assert notebook in {item["id"] for item in client.get(path).json()["items"]}
    renamed = client.patch(f"/api/notebooks/{notebook}", json={"title": "committed rename"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "committed rename"
    assert client.delete(f"/api/notebooks/{notebook}").status_code == 204
    assert notebook not in {item["id"] for item in client.get(path).json()["items"]}

    from app.auth import invitations

    delivered = {}
    monkeypatch.setattr(
        invitations.InvitationMailer, "send", lambda _, email, token: delivered.update({email: token})
    )
    recipient = uuid4()
    try:
        invited = client.post(
            "/api/invitations",
            json={"email": "invitee@example.test", "workspace_id": str(scope[4]), "role": "viewer"},
        )
        assert invited.status_code == 201
        app.dependency_overrides[session_layer.identity] = lambda: Identity(
            recipient, "invitee@example.test"
        )
        assert "token" not in invited.json()
        accepted = client.post(
            "/api/invitations/accept", json={"token": delivered["invitee@example.test"]}
        )
        assert accepted.status_code == 200
        assert client.get("/api/auth/admission").json() == {"admitted": True, "platform_role": None}
        assert client.get(path).status_code == 200
        app.dependency_overrides[session_layer.identity] = lambda: Identity(
            scope[0], "http-mutation@example.test"
        )
        member = f"/api/workspaces/{scope[4]}/members/{recipient}"
        assert client.patch(member, json={"role": "editor"}).status_code == 200
        assert client.delete(member).status_code == 204
        app.dependency_overrides[session_layer.identity] = lambda: Identity(
            recipient, "invitee@example.test"
        )
        assert client.get(path).status_code == 403
    finally:
        # Dispose only this fixture's newly accepted user's personal workspace/admission.
        with psycopg.connect(URL) as cleanup:
            cleanup.execute("DELETE FROM workspaces WHERE personal_owner_id=%s", (recipient,))
            cleanup.execute("DELETE FROM app_admissions WHERE user_id=%s", (recipient,))


@pytest.mark.parametrize(
    "message,code,status",
    [("quota_gate_busy", "55P03", 503), ("root_unknown_commit", "P0001", 500)],
)
def test_real_deferred_commit_failure_precedes_asgi_response(http_runtime, message, code, status):
    from psycopg import sql
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import QueuePool

    client, scope = http_runtime
    runtime = session_layer.engine()
    pool = runtime.pool
    assert isinstance(pool, QueuePool)
    path = f"/api/workspaces/{scope[4]}/notebooks"
    before = client.get(path).json()
    sessions, connections, starts = [], [], []

    def begun(session, transaction, connection):
        if connection.engine is runtime:
            sessions.append(session)

    def committing(connection):
        connections.append(connection)

    async def observed_app(asgi_scope, receive, send):
        async def observed_send(event_message):
            if event_message["type"] == "http.response.start":
                starts.append(
                    (
                        event_message["status"],
                        all(connection.closed for connection in connections),
                        all(not session.in_transaction() for session in sessions),
                        pool.checkedout(),
                    )
                )
            await send(event_message)

        await app(asgi_scope, receive, observed_send)

    # Own disposable fixture only: add a failure, never disable existing guards.
    # PostgreSQL raises at REAL deferred COMMIT, after flush/DTO construction.
    with psycopg.connect(URL) as setup:
        setup.execute(
            "CREATE FUNCTION public.root_http_commit_failure() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION USING MESSAGE=TG_ARGV[0], ERRCODE=TG_ARGV[1]; END; $$"
        )
        setup.execute(
            sql.SQL(
                "CREATE CONSTRAINT TRIGGER root_http_commit_failure "
                "AFTER INSERT ON public.notebooks DEFERRABLE INITIALLY DEFERRED "
                "FOR EACH ROW WHEN (NEW.title='commit failure probe') "
                "EXECUTE FUNCTION public.root_http_commit_failure({}, {})"
            ).format(sql.Literal(message), sql.Literal(code))
        )
    event.listen(Session, "after_begin", begun)
    event.listen(runtime, "commit", committing)
    try:
        with TestClient(
            observed_app,
            base_url=str(client.base_url),
            headers=client.headers,
            raise_server_exceptions=False,
        ) as witnessed:
            response = witnessed.post(path, json={"title": "commit failure probe"})
        assert response.status_code == status
        assert starts == [(status, True, True, 0)]
        assert sessions and len(sessions) == len({id(session) for session in sessions})
        assert len(connections) == len(sessions)
        assert all(connection.closed for connection in connections)
        if status == 503:
            assert 1 <= len(sessions) <= 3  # Count AND elapsed admission may stop retries.
            assert response.json()["detail"]["code"] == "quota_busy"
        else:
            assert len(sessions) == 1  # Unclassified commit failure is NEVER replayed.
            assert message not in response.text
    finally:
        event.remove(Session, "after_begin", begun)
        event.remove(runtime, "commit", committing)
        with psycopg.connect(URL) as cleanup:
            cleanup.execute("DROP TRIGGER root_http_commit_failure ON public.notebooks")
            cleanup.execute("DROP FUNCTION public.root_http_commit_failure()")
    assert client.get(path).json() == before


@pytest.mark.parametrize("revocation", ["admission", "membership"])
def test_busy_retry_rechecks_revoked_authorization(http_runtime, monkeypatch, revocation):
    from app.documents import transactions
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import QueuePool

    client, scope = http_runtime
    actor = scope[0] if revocation == "admission" else scope[1]
    app.dependency_overrides[session_layer.identity] = lambda: Identity(
        actor, "http-mutation@example.test"
    )
    path = f"/api/workspaces/{scope[4]}/notebooks"
    # Establish real write authorization before the competing transaction changes it.
    assert client.post(path, json={"title": "authorized control"}).status_code == 201
    before = client.get(path).json()
    runtime = session_layer.engine()
    pool = runtime.pool
    assert isinstance(pool, QueuePool)
    sessions, connections, backoffs = [], [], []

    def begun(session, transaction, connection):
        if connection.engine is runtime:
            sessions.append(session)
            connections.append(connection)

    with psycopg.connect(URL) as holder:
        holder.execute("SELECT public.quota_gate()")

        def revoke_during_backoff(delay):
            assert not backoffs and len(sessions) == 1
            assert connections[0].closed and not sessions[0].in_transaction()
            assert pool.checkedout() == 0
            # Separate fixture transaction owns the gate; real first request
            # has already rolled back/closed before authorization is revoked.
            if revocation == "admission":
                holder.execute("DELETE FROM app_admissions WHERE user_id=%s", (actor,))
            else:
                holder.execute(
                    "UPDATE workspace_members SET role='viewer' WHERE workspace_id=%s AND user_id=%s",
                    (scope[4], actor),
                )
            holder.commit()  # Publish revocation and release the competing gate together.
            backoffs.append(delay)

        # Isolate freshness from scheduling: real PostgreSQL errors/rollback,
        # but an unexpired retry-admission clock, not a latency measurement.
        monkeypatch.setattr(transactions, "monotonic", lambda: 0.0)
        monkeypatch.setattr(transactions, "sleep", revoke_during_backoff)
        event.listen(Session, "after_begin", begun)
        try:
            response = client.post(path, json={"title": "must not survive revocation"})
        finally:
            event.remove(Session, "after_begin", begun)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == (
        "admission_required" if revocation == "admission" else "workspace_access_denied"
    )
    assert backoffs == [0.01]
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert all(connection.closed for connection in connections)
    assert all(not session.in_transaction() for session in sessions)
    assert pool.checkedout() == 0
    # An unaffected, still-admitted member sees exactly the pre-revocation rows.
    reader = scope[1] if revocation == "admission" else scope[0]
    app.dependency_overrides[session_layer.identity] = lambda: Identity(
        reader, "http-mutation@example.test"
    )
    assert client.get(path).json() == before
