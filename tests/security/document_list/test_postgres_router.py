"""Optional future serial kube1 acceptance; NEVER run locally or on A5's stack.

New isolated migrated PostgreSQL16 DB + direct-role private passfiles required.
All cases UNRUN in this delivery; external auth response alone is synthetic.
"""

import asyncio
import logging
import os
import re
import stat
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from app.auth.security import SESSION_COOKIE
from app.auth.supabase import SupabaseAuth
from app.db import session as session_layer
from app.documents import router as adapter
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ENABLED = os.getenv("B4_RUN_POSTGRES") == "isolated-kube1-approved"
pytestmark = pytest.mark.skipif(not ENABLED, reason="B4 explicit isolated kube1 opt-in absent")


def require(condition, label):
    if not condition:
        pytest.fail(label, pytrace=False)


@contextmanager
def opaque(label):
    try:
        yield
    except Exception:  # noqa: BLE001 — discard original driver error, never print raw text
        pytest.fail(label, pytrace=False)


def configuration():
    database = os.getenv("B4_ASSIGNED_DATABASE", "")
    require(bool(re.fullmatch(r"notebook_b4_[0-9a-f]{12}", database)), "B4 assignment required")
    result = []
    for role, user in (("APP", "notebook_app"), ("ADMIN", "postgres")):
        with opaque("B4 endpoint configuration rejected"):
            url = make_url(os.getenv(f"B4_{role}_URL", ""))
            path = Path(os.getenv(f"B4_{role}_PASSFILE", ""))
            mode = path.lstat().st_mode
            require(path.is_absolute() and not path.is_symlink(), "B4 private passfile required")
            require(
                stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o600, "B4 private passfile required"
            )
            require(
                url.drivername in ("postgresql", "postgresql+psycopg")
                and url.host == "db"
                and url.port == 5432
                and url.database == database
                and url.username == user
                and url.password is None
                and not url.query,
                "B4 assigned passwordless direct role endpoint required",
            )
            result.append((url.set(drivername="postgresql+psycopg"), str(path)))
    return result


@pytest.fixture
def database(monkeypatch):
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    engines = []
    ids = {
        name: uuid4()
        for name in (
            "viewer",
            "foreign",
            "unadmitted",
            "workspace",
            "notebook",
            "document",
            "version",
        )
    }
    setup = False
    admin: Callable[..., list] | None = None
    try:
        with opaque("B4 database setup failed"):
            for url, passfile in configuration():
                engines.append(
                    create_engine(
                        url,
                        hide_parameters=True,
                        pool_size=2,
                        max_overflow=0,
                        pool_timeout=1,
                        connect_args={
                            "passfile": passfile,
                            "connect_timeout": 3,
                            "options": "-c statement_timeout=3000 -c lock_timeout=1000",
                        },
                    )
                )
            app_engine, admin_engine = engines

            def run_admin(sql, params=None):
                with (
                    opaque("B4 isolated administrative operation failed"),
                    admin_engine.begin() as db,
                ):
                    result = db.execute(text(sql), params or ids)
                    return list(result.fetchall()) if result.returns_rows else []

            admin = run_admin
            require(
                admin(
                    "SELECT current_user,current_setting('server_version_num')::int/10000,"
                    "(SELECT count(*) FROM app_admissions),(SELECT count(*) FROM workspaces)"
                )
                == [("postgres", 16, 0, 0)],
                "B4 fresh migrated PostgreSQL16 required",
            )
            setup = True
            admin("INSERT INTO app_admissions(user_id) VALUES (:viewer),(:foreign)")
            admin("INSERT INTO workspaces(id,name) VALUES (:workspace,'B4 synthetic')")
            admin("INSERT INTO workspace_members VALUES (:workspace,:viewer,'viewer')")
            admin("INSERT INTO notebooks(id,workspace_id,title) VALUES (:notebook,:workspace,'B4')")
            admin(
                "INSERT INTO documents(id,workspace_id,notebook_id,created_by,filename,current_version_id) "
                "VALUES (:document,:workspace,:notebook,:viewer,'synthetic.md',NULL)"
            )
            admin(
                "INSERT INTO document_versions(id,document_id,notebook_id,workspace_id,version,media_type,"
                "byte_count,sha256,storage_key) VALUES (:version,:document,:notebook,:workspace,1,"
                "'text/markdown',1,repeat('a',64),:workspace||'/'||:notebook||'/'||:document||'/'||:version||'/original')"
            )
            admin("UPDATE documents SET current_version_id=:version WHERE id=:document")
            monkeypatch.setattr(session_layer, "engine", lambda: app_engine)

            async def auth(self, method, path, token="", data=None):
                require(method == "GET" and path == "user", "B4 unexpected authentication endpoint")
                actor = ids.get(token)
                if token not in ("viewer", "foreign", "unadmitted"):
                    raise HTTPException(401, {"code": "invalid_credentials"})
                return {
                    "id": str(actor),
                    "email": "b4@example.test",
                    "email_confirmed_at": "synthetic",
                }

            monkeypatch.setattr(SupabaseAuth, "request", auth)
            app = FastAPI()
            app.include_router(adapter.router)
            app.dependency_overrides[adapter.documents_enabled] = lambda: None
        yield app, ids, admin
    finally:
        try:
            if setup and admin is not None:
                admin("DELETE FROM workspaces WHERE id=:workspace")
                admin("DELETE FROM app_admissions WHERE user_id IN (:viewer,:foreign)")
        finally:
            for engine in engines:
                engine.dispose()
            logging.disable(previous)


@pytest.mark.parametrize(
    "scenario,status",
    [
        ("viewer", 200),
        ("foreign", 404),
        ("unadmitted", 403),
        ("membership-revoked", 404),
        ("admission-revoked", 403),
        ("deleted", 200),
        ("empty", 200),
    ],
)
def test_real_restricted_reader(database, scenario, status):
    app, ids, admin = database
    actor = scenario if scenario in ("foreign", "unadmitted") else "viewer"
    if scenario == "membership-revoked":
        admin("DELETE FROM workspace_members WHERE workspace_id=:workspace AND user_id=:viewer")
    elif scenario == "admission-revoked":
        admin("DELETE FROM app_admissions WHERE user_id=:viewer")
    elif scenario == "deleted":
        admin("UPDATE documents SET deleted_at=now() WHERE id=:document")
    elif scenario == "empty":
        admin("UPDATE documents SET current_version_id=NULL WHERE id=:document")

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="https://b4.example.test",
        ) as client:
            return await client.get(
                f"/api/notebooks/{ids['notebook']}/documents",
                headers={"cookie": f"{SESSION_COOKIE}={actor}"},
            )

    with opaque("B4 HTTP request failed"):
        response = asyncio.run(send())
    require(response.status_code == status, "B4 unexpected HTTP status")
    require(
        not any(
            field in response.text
            for field in ("storage_key", "actor_user_id", "job_id", "lease_token")
        ),
        "B4 private fields escaped member DTO",
    )
    if status == 200:
        expected = (
            []
            if scenario in ("deleted", "empty")
            else [
                {
                    "id": str(ids["document"]),
                    "notebook_id": str(ids["notebook"]),
                    "filename": "synthetic.md",
                    "version_id": str(ids["version"]),
                    "status": "failed",
                    "error_code": "not_processed",
                }
            ]
        )
        require(response.json() == {"items": expected}, "B4 member DTO differs from contract")
