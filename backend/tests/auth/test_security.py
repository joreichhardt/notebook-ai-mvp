from uuid import uuid4

import pytest
from app.auth.router import router
from app.auth.security import check_csrf, invitation_hash, invitation_token
from app.auth.settings import Settings
from app.auth.supabase import Identity, SupabaseAuth
from app.db.models import Base, Member, Workspace
from app.workspaces.router import membership
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request


def request(origin="https://note.yokoma.de", cookie="abc", header="abc"):
    return Request(
        {
            "type": "http",
            "headers": [
                (b"origin", origin.encode()),
                (b"cookie", f"__Host-notebook_csrf={cookie}".encode()),
                (b"x-csrf-token", header.encode()),
            ],
        }
    )


@pytest.mark.parametrize(
    "origin,cookie,header",
    [
        ("https://evil.test", "abc", "abc"),
        ("https://note.yokoma.de", "", ""),
        ("https://note.yokoma.de", "abc", "wrong"),
    ],
)
def test_csrf_rejects(origin, cookie, header):
    with pytest.raises(HTTPException) as exc:
        check_csrf(request(origin, cookie, header), "https://note.yokoma.de")
    assert exc.value.status_code == 403


def test_csrf_accepts():
    check_csrf(request(), "https://note.yokoma.de")


def test_invitation_random_hash():
    token, digest = invitation_token()
    assert digest == invitation_hash(token) and token != digest
    assert invitation_token()[0] != token


def test_membership_blocks_foreign_and_viewer_write():
    engine = create_engine("sqlite://")
    # This unit test exercises membership(), not PostgreSQL-specific document DDL.
    # Full schema and RLS are verified against migrated PostgreSQL in test_documents.
    Base.metadata.create_all(
        engine, tables=[Base.metadata.tables[name] for name in ("workspaces", "workspace_members")]
    )
    with Session(engine) as db:
        uid, wid = uuid4(), uuid4()
        db.add(Workspace(id=wid, name="Private"))
        db.add(Member(workspace_id=wid, user_id=uid, role="viewer"))
        db.flush()
        member = membership(db, wid, uid)
        assert member is not None and member.role == "viewer"
        for user, write in [(uuid4(), False), (uid, True)]:
            with pytest.raises(HTTPException):
                membership(db, wid, user, write)


def test_login_origin_checked_before_upstream():
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post(
        "/api/auth/login",
        json={"email": "a@example.test", "password": "private"},
        headers={"origin": "https://evil.test"},
    )
    assert r.status_code == 403 and "private" not in r.text


def test_secure_cookie_flags(monkeypatch):
    async def upstream(self, *args, **kwargs):
        return {"access_token": "fixture-token", "expires_in": 600}

    async def identity(self, token):
        return Identity(uuid4(), "test@example.test")

    monkeypatch.setattr(SupabaseAuth, "request", upstream)
    monkeypatch.setattr(SupabaseAuth, "identity", identity)
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post(
        "/api/auth/login",
        json={"email": "a@example.test", "password": "private"},
        headers={"origin": "https://note.yokoma.de"},
    )
    assert r.status_code == 200
    session = next(
        c for c in r.headers.get_list("set-cookie") if c.startswith("__Host-notebook_session=")
    )
    assert all(flag in session for flag in ["HttpOnly", "Secure", "SameSite=lax", "Path=/"])
    assert "fixture-token" not in r.text


def test_missing_configuration_fails_closed():
    import asyncio

    with pytest.raises(HTTPException) as exc:
        asyncio.run(SupabaseAuth(Settings()).identity("token"))
    assert exc.value.status_code == 503


@pytest.mark.parametrize("status", [401, 403, 503])
def test_session_invalid_vs_unavailable(monkeypatch, status):
    async def rejected(self, token):
        raise HTTPException(status, detail={"code": "test_error", "message": "Unavailable"})

    monkeypatch.setattr(SupabaseAuth, "identity", rejected)
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get(
        "/api/auth/session",
        headers={
            "cookie": "__Host-notebook_session=stale; __Host-notebook_csrf=csrf",
        },
    )
    if status == 401:
        assert response.status_code == 200
        assert response.json() == {"user": None}
        assert len(response.headers.get_list("set-cookie")) == 2
        assert all("Max-Age=0" in c for c in response.headers.get_list("set-cookie"))
    else:
        assert response.status_code == status
        assert not response.headers.get_list("set-cookie")


@pytest.mark.parametrize("upstream_status", [None, 401, 503])
def test_logout_always_clears_valid_local_session(monkeypatch, upstream_status):
    async def upstream(self, *args, **kwargs):
        if upstream_status:
            raise HTTPException(upstream_status, detail={"code": "upstream", "message": "No"})
        return {}

    monkeypatch.setattr(SupabaseAuth, "request", upstream)
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        "/api/auth/logout",
        headers={
            "origin": "https://note.yokoma.de",
            "x-csrf-token": "csrf",
            "cookie": "__Host-notebook_session=stale; __Host-notebook_csrf=csrf",
        },
    )
    assert response.status_code == (200 if upstream_status == 503 else 204)
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2 and all("Max-Age=0" in c for c in cookies)
    if upstream_status == 503:
        assert response.json()["local_logout"] is True
        assert response.json()["remote_revoked"] is False
        assert response.json()["detail"]["code"] == "revocation_unconfirmed"
