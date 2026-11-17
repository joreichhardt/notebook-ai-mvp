"""HTTP/metadata boundaries; PostgreSQL role enforcement is tested separately."""
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.auth.administration import router as admin_router
from app.auth.invitations import router as invitation_router
from app.auth.security import CSRF_COOKIE
from app.db.models import Base, PlatformAdmin
from app.db.session import identity, user_session
from app.mutations import actor_mutations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def runtime():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    uid, other = uuid4(), uuid4()
    with Session(engine) as db:
        db.add(PlatformAdmin(user_id=other, role="superadmin"))
        db.commit()
    app = FastAPI()
    app.include_router(invitation_router)
    app.include_router(admin_router)
    app.dependency_overrides[identity] = lambda: SimpleNamespace(id=uid, email="fixture@example.test")

    def session():
        with Session(engine) as db, db.begin():
            yield db

    app.dependency_overrides[user_session] = session
    client = TestClient(app, base_url="https://note.yokoma.de")
    client.cookies.set(CSRF_COOKIE, "fixture-csrf")
    yield client, app, engine, uid
    client.close()
    engine.dispose()


def test_admission_does_not_inherit_another_users_admin_role(runtime):
    client, _, _, _ = runtime
    assert client.get("/api/auth/admission").json() == {"admitted": True, "platform_role": None}


@pytest.mark.parametrize("role", ["admin", "superadmin"])
def test_admission_reports_only_the_actors_role(runtime, role):
    client, _, engine, uid = runtime
    with Session(engine) as db:
        db.add(PlatformAdmin(user_id=uid, role=role))
        db.commit()
    assert client.get("/api/auth/admission").json()["platform_role"] == role


def test_grant_rejects_browser_supplied_superadmin_role(runtime):
    client, app, _, _ = runtime
    mutations = Mock()
    app.dependency_overrides[actor_mutations] = lambda: mutations
    response = client.post("/api/admin/administrators", json={"user_id": str(uuid4()), "role": "superadmin"})
    assert response.status_code == 422
    mutations.run.assert_not_called()


def test_grant_requires_origin_and_csrf_before_mutation(runtime):
    client, app, _, _ = runtime
    mutations = Mock()
    app.dependency_overrides[actor_mutations] = lambda: mutations
    response = client.post("/api/admin/administrators", json={"user_id": str(uuid4())}, headers={"Origin": "https://foreign.invalid", "X-CSRF-Token": "fixture-csrf"})
    assert response.status_code == 403
    mutations.run.assert_not_called()


def test_database_denial_is_sanitized_and_not_treated_as_success(runtime):
    client, app, _, _ = runtime

    class PermissionDenied(Exception):
        sqlstate = "42501"

    db = Mock()
    db.execute.side_effect = DBAPIError("private-query-canary", {}, PermissionDenied("private-diagnostic-canary"))
    app.dependency_overrides[actor_mutations] = lambda: SimpleNamespace(run=lambda fn: fn(db))
    response = client.post("/api/admin/administrators", json={"user_id": str(uuid4())}, headers={"Origin": "https://note.yokoma.de", "X-CSRF-Token": "fixture-csrf"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "superadmin_required"
    assert "canary" not in response.text
