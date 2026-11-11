from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from app.auth.security import CSRF_COOKIE
from app.connections.router import router
from app.db.models import Base, ProviderConnection
from app.db.session import identity, user_session
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def api():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    owner, other = uuid4(), uuid4()
    ids = [uuid4(), uuid4()]
    with Session(engine) as db:
        for id, user in zip(ids, (owner, other), strict=True):
            db.add(
                ProviderConnection(
                    id=id,
                    user_id=user,
                    provider_id="cloud",
                    label="Account",
                    secret_id=uuid4(),
                    status="active",
                    created_at=datetime.now(UTC),
                )
            )
        db.commit()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[identity] = lambda: SimpleNamespace(
        id=owner, email="owner@example.test"
    )

    def db_session():
        with Session(engine) as db, db.begin():
            yield db

    app.dependency_overrides[user_session] = db_session
    client = TestClient(app, base_url="https://note.yokoma.de")
    client.cookies.set(CSRF_COOKIE, "csrf")
    return client, ids, engine


def test_lists_only_owner_without_secret_reference(api):
    client, ids, _ = api
    response = client.get("/api/connections")
    assert response.status_code == 200
    assert [x["id"] for x in response.json()["items"]] == [str(ids[0])]
    assert "secret_id" not in response.text


def test_cannot_revoke_other_owner_secret(api):
    client, ids, _ = api
    with patch("app.connections.router.BitwardenStore") as store:
        response = client.delete(
            f"/api/connections/{ids[1]}",
            headers={"origin": "https://note.yokoma.de", "x-csrf-token": "csrf"},
        )
        assert response.status_code == 404
        store.assert_not_called()


def test_delete_requires_csrf(api):
    client, ids, _ = api
    assert client.delete(f"/api/connections/{ids[0]}").status_code == 403


def test_failed_revocation_preserves_inactive_retryable_record(api):
    client, ids, engine = api
    with patch("app.connections.router.BitwardenStore") as store:
        store.return_value.delete.side_effect = HTTPException(503, "private SDK details")
        response = client.delete(
            f"/api/connections/{ids[0]}",
            headers={"origin": "https://note.yokoma.de", "x-csrf-token": "csrf"},
        )
    assert response.status_code == 202
    assert "private SDK" not in response.text
    with Session(engine) as db:
        assert db.get(ProviderConnection, ids[0]).status == "disconnected"
    with patch("app.connections.router.BitwardenStore") as store:
        response = client.delete(
            f"/api/connections/{ids[0]}",
            headers={"origin": "https://note.yokoma.de", "x-csrf-token": "csrf"},
        )
    assert response.status_code == 204
    with Session(engine) as db:
        assert db.get(ProviderConnection, ids[0]) is None


def test_unknown_endpoint_never_calls_secret_store(api):
    client, _, _ = api
    with patch("app.connections.router.BitwardenStore") as store:
        response = client.post(
            "/api/connections",
            json={"provider_id": "evil", "label": "x", "api_key": "PRIVATE"},
            headers={"origin": "https://note.yokoma.de", "x-csrf-token": "csrf"},
        )
    assert response.status_code == 400
    assert "PRIVATE" not in response.text
    store.assert_not_called()
