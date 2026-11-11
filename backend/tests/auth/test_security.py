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
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        uid, wid = uuid4(), uuid4()
        db.add(Workspace(id=wid, name="Private"))
        db.add(Member(workspace_id=wid, user_id=uid, role="viewer"))
        db.flush()
        assert membership(db, wid, uid).role == "viewer"
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
