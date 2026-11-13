"""Isolated ASGI/verifier/service/factory tests; no backend auth autouse fixtures.

Session/query doubles are synthetic evidence, never PostgreSQL/RLS acceptance.
"""

import asyncio
import importlib.util
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from app.auth.security import SESSION_COOKIE
from app.auth.supabase import Identity, SupabaseAuth
from app.documents.service import DocumentError, UploadService
from fastapi import FastAPI, HTTPException


@pytest.fixture(autouse=True)
def no_connections(monkeypatch):
    import socket
    import sqlite3

    import psycopg
    import sqlalchemy

    def forbidden(*args, **kwargs):
        pytest.fail("local database/network connection forbidden", pytrace=False)

    for target, name in (
        (psycopg, "connect"),
        (psycopg.Connection, "connect"),
        (sqlite3, "connect"),
        (sqlalchemy, "create_engine"),
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket, "create_connection"),
    ):
        monkeypatch.setattr(target, name, forbidden)


def request(app, path, *, cookie="synthetic", method="GET"):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            if cookie:
                client.cookies.set(SESSION_COOKIE, cookie)
            return await client.request(method, path)

    return asyncio.run(send())


def test_gated_document_reader_has_an_explicit_route(monkeypatch):
    """Baseline was HTTP404, not import failure; enabled/unconfigured is fixed503."""
    app = FastAPI()
    if importlib.util.find_spec("app.documents.router") is not None:
        from app.documents.router import documents_enabled, router

        app.include_router(router)
        app.dependency_overrides[documents_enabled] = lambda: None

    async def auth(self, method, path, token="", data=None):
        return {"id": str(UUID(int=1)), "email": "reader@example.test", "email_confirmed_at": "yes"}

    monkeypatch.setattr(SupabaseAuth, "request", auth)
    adapter = (
        importlib.import_module("app.documents.router")
        if importlib.util.find_spec("app.documents.router")
        else None
    )

    def unavailable():
        raise RuntimeError("SYNTHETIC_PRIVATE_DRIVER")

    if adapter is not None:
        monkeypatch.setattr(adapter, "authenticated_session", unavailable)
    response = request(app, f"/api/notebooks/{UUID(int=2)}/documents")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "documents_unavailable"


@pytest.fixture
def scene(monkeypatch):
    from app.db import session as session_module
    from app.documents import router as adapter

    actor, notebook, workspace, doc, version = [uuid4() for _ in range(5)]
    state = SimpleNamespace(
        actor=actor,
        notebook=notebook,
        workspace=workspace,
        doc=doc,
        version=version,
        admitted=True,
        role="viewer",
        confirmed=True,
        safe=True,
        exists=True,
        mode="ok",
        events=[],
        claims={},
        calls=0,
        rows=[],
        auth_calls=0,
        sessions=[],
    )

    async def auth(self, method, path, token="", data=None):
        state.auth_calls += 1
        assert (method, path) == ("GET", "user")
        if token != "synthetic":
            raise HTTPException(401, {"code": "invalid_credentials"})
        return {
            "id": str(actor),
            "email": "viewer@example.test",
            "email_confirmed_at": "yes" if state.confirmed else None,
        }

    monkeypatch.setattr(SupabaseAuth, "request", auth)

    class Result:
        def __init__(self, value=None):
            self.value = value

        def scalar(self):
            return self.value

        scalar_one = scalar

        def one(self):
            return self.value

        def mappings(self):
            return iter(state.rows)

    class FakeSession:
        def __init__(self, engine):
            self.claims = {}
            state.sessions.append(self)

        def __enter__(self):
            state.events.append("open")
            return self

        def __exit__(self, *error):
            state.events.append("close")

        @contextmanager
        def begin(self):
            state.events.append("begin")
            try:
                yield
                if state.mode == "commit":
                    raise RuntimeError("SYNTHETIC_PRIVATE_COMMIT")
                state.events.append("commit")
            except BaseException:
                state.events.append("rollback")
                raise

        def execute(self, query, params=None):
            sql = str(query)
            if "NOT rolsuper AND NOT rolbypassrls" in sql:
                return Result(state.safe)
            if "set_config" in sql:
                self.claims.update(params or {})
                return Result()
            if "SELECT current_user,session_user" in sql:
                return Result(
                    getattr(
                        state,
                        "role_row",
                        ("notebook_app", "notebook_app", False, False, False, False),
                    )
                )
            if "SET LOCAL" in sql:
                return Result()
            if "current_setting" in sql:
                return Result(getattr(state, "subject", self.claims.get("uid")))
            if "document_versions" in sql:
                state.limit = query.compile().params.get("param_1")
                state.calls += 1
                state.events.append("listing")
                assert "current_version_id" in sql and "deleted_at IS NULL" in sql
                assert "LIMIT" in sql and "ingestion_jobs" not in sql
                if state.mode == "query":
                    raise RuntimeError("SYNTHETIC_PRIVATE_QUERY")
                return Result()
            raise AssertionError("unexpected synthetic SQL operation")

        def scalar(self, query):
            sql = str(query)
            if "app_admissions" in sql:
                return actor if state.admitted else None
            if "workspace_members" in sql:
                return state.role
            if "notebooks" in sql:
                return workspace if state.exists else None
            raise AssertionError("unexpected synthetic scalar")

    monkeypatch.setattr(session_module, "engine", lambda: object())
    monkeypatch.setattr(session_module, "Session", FakeSession)
    app = FastAPI()
    app.include_router(adapter.router)
    app.dependency_overrides[adapter.documents_enabled] = lambda: None
    state.app = app
    state.adapter = adapter
    state.path = f"/api/notebooks/{notebook}/documents"
    return state


def test_viewer_reads_empty_list_after_commit_and_close(scene):
    response = request(scene.app, scene.path)
    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert scene.auth_calls == 1
    assert scene.limit == 100
    assert scene.events == ["open", "begin", "listing", "commit", "close"]
    assert scene.sessions[0].claims == {"uid": str(scene.actor), "email": "viewer@example.test"}


@pytest.mark.parametrize(
    "status,code",
    [
        ("queued", "not_processed"),
        ("processing", None),
        ("ready", None),
        ("failed", "processing_failed"),
        ("failed", "processing_cancelled"),
    ],
)
def test_member_dto_only_has_contract_fields(scene, status, code):
    scene.rows = [
        {
            "id": scene.doc,
            "notebook_id": scene.notebook,
            "filename": "synthetic.md",
            "version_id": scene.version,
            "status": status,
            "error_code": code,
            "job_id": "SYNTHETIC_PRIVATE_JOB",
            "actor_user_id": "SYNTHETIC_PRIVATE_ACTOR",
            "storage_key": "SYNTHETIC_PRIVATE_REFERENCE",
            "lease_token": "SYNTHETIC_PRIVATE_TOKEN",
        }
    ]
    response = request(scene.app, scene.path)
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": str(scene.doc),
                "notebook_id": str(scene.notebook),
                "filename": "synthetic.md",
                "version_id": str(scene.version),
                "status": status,
                "error_code": code,
            }
        ]
    }
    assert "SYNTHETIC_PRIVATE" not in response.text
    assert not any(
        value in response.text
        for value in ("job_id", "actor", "storage_key", "lease_token", "secret_id")
    )


@pytest.mark.parametrize(
    "gate", [False, None, 1, "true", {}, {"documents": 1}, {"documents": True}]
)
def test_closed_capability_precedes_auth_service_and_db(scene, monkeypatch, gate):
    scene.app.dependency_overrides.clear()
    monkeypatch.setattr(scene.adapter, "capabilities", lambda: {"documents": gate})

    def forbidden(*args, **kwargs):
        pytest.fail("closed capability constructed service")

    monkeypatch.setattr(scene.adapter, "UploadService", forbidden)
    response = request(scene.app, scene.path)
    assert response.status_code == 404
    assert scene.auth_calls == 0 and scene.sessions == []


def test_absent_and_default_gate_are_closed(scene, monkeypatch):
    scene.app.dependency_overrides.clear()
    assert request(scene.app, scene.path).status_code == 404
    monkeypatch.setattr(scene.adapter, "capabilities", dict)
    assert request(scene.app, scene.path).status_code == 404
    assert not scene.sessions and scene.auth_calls == 0


@pytest.mark.parametrize(
    "condition,status,code",
    [
        ("anonymous", 401, "authentication_required"),
        ("unconfirmed", 403, "email_unverified"),
        ("admission", 403, "admission_required"),
        ("foreign", 404, "notebook_not_found"),
        ("missing", 404, "notebook_not_found"),
        ("unsafe", 503, "documents_unavailable"),
    ],
)
def test_authentication_admission_membership_and_principal_fail_closed(
    scene, condition, status, code
):
    if condition == "unconfirmed":
        scene.confirmed = False
    if condition == "admission":
        scene.admitted = False
    if condition == "foreign":
        scene.role = None
    if condition == "missing":
        scene.exists = False
    if condition == "unsafe":
        scene.safe = False
    response = request(
        scene.app, scene.path, cookie="" if condition == "anonymous" else "synthetic"
    )
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert scene.calls == 0
    if condition in ("anonymous", "unconfirmed"):
        assert not scene.sessions
    else:
        assert scene.events[-2:] == ["rollback", "close"]


@pytest.mark.parametrize(
    "suffix",
    ["?limit=0", "?limit=101", "?limit=-1", "?limit=1.0", "?limit=true", "?limit=%EF%BC%91"],
)
def test_limit_validation_is_fixed_and_never_queries(scene, suffix):
    response = request(scene.app, scene.path + suffix)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert not scene.sessions


def test_invalid_uuid_is_not_reflected(scene):
    response = request(scene.app, "/api/notebooks/SYNTHETIC_PRIVATE/documents")
    assert response.status_code == 422 and "SYNTHETIC_PRIVATE" not in response.text
    assert not scene.sessions


def test_each_request_uses_fresh_committing_transaction(scene):
    assert request(scene.app, scene.path + "?limit=1").status_code == 200
    assert request(scene.app, scene.path).status_code == 200
    assert scene.limit == 100
    assert len(scene.sessions) == 2 and scene.sessions[0] is not scene.sessions[1]
    assert scene.events.count("commit") == 2


@pytest.mark.parametrize("mode", ["commit", "query"])
def test_error_closes_transaction_without_replay_or_private_message(scene, mode):
    scene.mode = mode
    response = request(scene.app, scene.path)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "documents_unavailable"
    assert "SYNTHETIC_PRIVATE" not in response.text
    assert scene.calls == 1
    assert scene.events[-2:] == ["rollback", "close"]


def test_actor_factory_rejects_mismatch_before_opening_session(scene):
    # This is a trusted DI factory contract test, not an authentication shortcut.
    service = scene.adapter.document_service(Identity(scene.actor, "viewer@example.test"))
    with pytest.raises(DocumentError):
        service.list_documents(actor_id=uuid4(), notebook_id=scene.notebook)
    assert not scene.sessions
    with pytest.raises(HTTPException):
        scene.adapter.document_service(scene.actor)


def test_uuid_cookie_is_not_authentication(scene):
    response = request(scene.app, scene.path, cookie=str(scene.actor))
    assert response.status_code == 401
    assert scene.auth_calls == 1 and not scene.sessions


def test_reader_module_has_no_mutation_routes(scene):
    assert request(scene.app, scene.path, method="POST").status_code == 405


def test_main_mounts_reader_but_default_gate_precedes_authentication(scene):
    from app.main import app

    response = request(app, scene.path)
    assert response.status_code == 404
    # Exercise the real HTTP boundary, not FastAPI's version-dependent lazy
    # included-router representation. An unmounted route returns another body.
    assert response.json() == {"detail": {"code": "not_found", "message": "Nicht gefunden."}}
    assert scene.auth_calls == 0 and not scene.sessions


def test_main_explicit_test_gate_uses_verified_reader_and_committed_projection(scene):
    from app.main import app

    previous = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[scene.adapter.documents_enabled] = lambda: None
        response = request(app, scene.path + "?limit=1")
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert scene.auth_calls == 1 and scene.limit == 1
    assert scene.events == ["open", "begin", "listing", "commit", "close"]


@pytest.mark.parametrize("fault", ["subject", "session-user", "superuser", "bypassrls"])
def test_service_independently_rechecks_subject_and_both_principals(scene, fault):
    if fault == "subject":
        scene.subject = str(uuid4())
    elif fault == "session-user":
        scene.role_row = ("notebook_app", "postgres", False, False, False, False)
    elif fault == "superuser":
        scene.role_row = ("notebook_app", "notebook_app", True, False, False, False)
    else:
        scene.role_row = ("notebook_app", "notebook_app", False, False, False, True)
    response = request(scene.app, scene.path)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "documents_unavailable"
    assert scene.calls == 0 and scene.events[-2:] == ["rollback", "close"]


def test_explicit_literal_true_capability_allows_reader(scene, monkeypatch):
    scene.app.dependency_overrides.clear()
    monkeypatch.setattr(scene.adapter, "capabilities", lambda: {"documents": True})
    assert request(scene.app, scene.path + "?limit=1").status_code == 200
    assert scene.limit == 1


def test_dto_is_constructed_after_transaction_commit_and_close(scene, monkeypatch):
    dto = scene.adapter.DocumentList

    def checked(*args, **kwargs):
        assert scene.events[-2:] == ["commit", "close"]
        return dto(*args, **kwargs)

    monkeypatch.setattr(scene.adapter, "DocumentList", checked)
    assert request(scene.app, scene.path).status_code == 200


def test_listing_never_constructs_storage(scene, monkeypatch):
    import app.documents.service as service_module

    def forbidden(*args, **kwargs):
        pytest.fail("read-only list attempted Storage construction")

    monkeypatch.setattr(service_module, "SupabaseStorage", forbidden)
    assert request(scene.app, scene.path).status_code == 200


@pytest.mark.parametrize("fault", ["auth-code", "constructor", "malformed-dto"])
def test_other_private_errors_are_fixed_unavailable(scene, monkeypatch, fault):
    if fault == "auth-code":

        async def bad_auth(*args, **kwargs):
            raise HTTPException(500, {"code": HostileCode("admission_required")})

        monkeypatch.setattr(SupabaseAuth, "request", bad_auth)
    elif fault == "constructor":

        def bad_constructor(*args, **kwargs):
            raise RuntimeError("SYNTHETIC_PRIVATE_CONSTRUCTOR")

        monkeypatch.setattr(scene.adapter, "UploadService", bad_constructor)
    else:
        scene.rows = [{"status": "SYNTHETIC_PRIVATE_DTO"}]
    response = request(scene.app, scene.path)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "documents_unavailable"
    assert "SYNTHETIC_PRIVATE" not in response.text


class HostileCode(str):
    def __hash__(self):
        raise RuntimeError("SYNTHETIC_PRIVATE_HASH")

    def __eq__(self, other):
        raise RuntimeError("SYNTHETIC_PRIVATE_EQUALITY")


@pytest.mark.parametrize("fault", ["ordinary", "code-subclass", "detail-getter", "dict-subclass"])
def test_hostile_service_errors_use_fixed_constants(scene, monkeypatch, fault):
    class BadDetail(HTTPException):
        @property
        def detail(self):
            raise RuntimeError("SYNTHETIC_PRIVATE_GETTER")

    class BadDict(dict):
        def get(self, key, default=None):
            raise RuntimeError("SYNTHETIC_PRIVATE_DICT")

    def fail(*args, **kwargs):
        if fault == "ordinary":
            raise RuntimeError("SYNTHETIC_PRIVATE_ORDINARY")
        if fault == "code-subclass":
            raise DocumentError(HostileCode("admission_required"), 403)
        if fault == "dict-subclass":
            raise HTTPException(500, BadDict(code="admission_required"))
        error = BadDetail.__new__(BadDetail)
        raise error

    monkeypatch.setattr(UploadService, "list_documents", fail)
    response = request(scene.app, scene.path)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "documents_unavailable"
    assert "SYNTHETIC_PRIVATE" not in response.text
