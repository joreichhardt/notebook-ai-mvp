import json
import shutil
import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from app.auth.security import CSRF_COOKIE
from app.auth.supabase import Identity
from app.connections.execution import DiscoveryRepository
from app.connections.router import discovery_repository, router
from app.db.models import Base, ProviderConnection
from app.db.session import identity, user_session
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def api(request, monkeypatch):
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
    if getattr(request, "param", None) == "full":
        from app.main import app

        monkeypatch.setattr(app, "dependency_overrides", dict(app.dependency_overrides))
    else:
        app = FastAPI()
        app.include_router(router)
    app.dependency_overrides[identity] = lambda: SimpleNamespace(
        id=owner, email="owner@example.test"
    )

    def db_session():
        with Session(engine) as db, db.begin():
            yield db

    session_factory = contextmanager(lambda _: db_session())
    app.dependency_overrides[discovery_repository] = lambda: DiscoveryRepository(
        Identity(id=owner, email="owner@example.test"), session_factory=session_factory
    )
    app.dependency_overrides[user_session] = db_session
    client = TestClient(app, base_url="https://note.yokoma.de")
    client.cookies.set(CSRF_COOKIE, "csrf")
    return client, ids, engine


@pytest.mark.parametrize("api", ["full"], indirect=True)
def test_cloud_profile_offers_three_separate_personal_connections(api, monkeypatch):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is required to render the cloud test profile")
    root = Path(__file__).resolve().parents[3]
    from os import environ

    env = dict(
        environ,
        NOTEBOOK_POC_API_IMAGE="fixture:local",
        NOTEBOOK_POC_FRONTEND_IMAGE="fixture:local",
        NOTEBOOK_POC_SMTP_HOST="unused.invalid",
        NOTEBOOK_POC_SECRETS="/nonexistent/fixture",
        NOTEBOOK_UPLOAD_TEST_ORIGINALS="/nonexistent/fixture",
        NOTEBOOK_UPLOAD_TEST_IO_FILE="/nonexistent/fixture",
        NOTEBOOK_UPLOAD_TEST_SCHEDULER_FILE="/nonexistent/fixture",
    )
    config = json.loads(
        subprocess.check_output(
            [
                "docker",
                "compose",
                "-f",
                "compose.poc.yaml",
                "-f",
                "compose.upload-test.yaml",
                "-f",
                "compose.upload-cloud-keys.yaml",
                "config",
                "--format",
                "json",
            ],
            cwd=root,
            env=env,
            timeout=15,
        )
    )
    for name, value in config["services"]["api"]["environment"].items():
        if name.startswith("NOTEBOOK_PROVIDER_"):
            monkeypatch.setenv(name, value)
    client, _, _ = api
    catalog = client.get("/api/providers")
    assert catalog.status_code == 200
    assert {
        (item["id"], item["label"], item["kind"], item["requires_api_key"])
        for item in catalog.json()["items"]
    } == {
        ("openai", "OpenAI", "openai", True),
        ("claude", "Claude (Anthropic)", "anthropic", True),
        ("groq", "Groq", "openai", True),
    }
    with patch("app.connections.router.SupabaseStore") as vault:
        vault.return_value.create.side_effect = lambda *_: uuid4()
        for name in ("openai", "claude", "groq"):
            response = client.post(
                "/api/connections",
                json={
                    "provider_id": name,
                    "label": name,
                    "api_key": f"synthetic-only-{name}",
                },
                headers={"origin": "https://note.yokoma.de", "x-csrf-token": "csrf"},
            )
            assert response.status_code == 201
            assert response.json()["provider_id"] == name
            assert "secret_id" not in response.json() and "synthetic-only-" not in response.text
        assert vault.return_value.create.call_count == 3
    assert {item["provider_id"] for item in client.get("/api/connections").json()["items"]} >= {
        "openai",
        "claude",
        "groq",
    }


def test_lists_only_owner_without_secret_reference(api):
    client, ids, _ = api
    response = client.get("/api/connections")
    assert response.status_code == 200
    assert [x["id"] for x in response.json()["items"]] == [str(ids[0])]
    assert "secret_id" not in response.text


def test_cannot_revoke_other_owner_secret(api):
    client, ids, _ = api
    with patch("app.connections.router.SupabaseStore") as store:
        response = client.delete(
            f"/api/connections/{ids[1]}",
            headers={"origin": "https://note.yokoma.de", "x-csrf-token": "csrf"},
        )
        assert response.status_code == 404
        store.assert_not_called()


def test_delete_requires_csrf(api):
    client, ids, _ = api
    assert client.delete(f"/api/connections/{ids[0]}").status_code == 403


def test_disconnect_commits_before_vault_cleanup_and_owner_can_retry(api):
    client, ids, engine = api
    with patch("app.connections.router.SupabaseStore") as store:

        def unavailable(*_):
            with Session(engine) as db:
                row = db.get(ProviderConnection, ids[0])
                assert row is not None and row.status == "disconnected"
            raise HTTPException(503, "Vault unavailable")

        store.return_value.delete.side_effect = unavailable
        response = client.delete(
            f"/api/connections/{ids[0]}",
            headers={"origin": "https://note.yokoma.de", "x-csrf-token": "csrf"},
        )
    assert response.status_code == 202
    assert response.json() == {"status": "disconnected", "cleanup_pending": True}
    with Session(engine) as db:
        row = db.get(ProviderConnection, ids[0])
        assert row is not None
        assert row.status == "disconnected"
    with patch("app.connections.router.SupabaseStore"):
        response = client.delete(
            f"/api/connections/{ids[0]}",
            headers={"origin": "https://note.yokoma.de", "x-csrf-token": "csrf"},
        )
    assert response.status_code == 204
    with Session(engine) as db:
        assert db.get(ProviderConnection, ids[0]) is None


def test_replace_key_returns_only_metadata_and_updates_reference(api):
    client, ids, engine = api
    new_reference = uuid4()
    with (
        patch("app.connections.router.SupabaseStore") as store,
        patch("app.connections.router.provider_config"),
    ):
        store.return_value.create.return_value = new_reference
        response = client.put(
            f"/api/connections/{ids[0]}/key",
            json={"api_key": "synthetic-replacement"},
            headers={"origin": "https://note.yokoma.de", "x-csrf-token": "csrf"},
        )
    assert response.status_code == 200
    assert "synthetic-replacement" not in response.text
    assert "secret_id" not in response.json()
    with Session(engine) as db:
        row = db.get(ProviderConnection, ids[0])
        assert row is not None
        assert row.secret_id == new_reference


def test_unknown_endpoint_never_calls_secret_store(api):
    client, _, _ = api
    with patch("app.connections.router.SupabaseStore") as store:
        response = client.post(
            "/api/connections",
            json={"provider_id": "evil", "label": "x", "api_key": "PRIVATE"},
            headers={"origin": "https://note.yokoma.de", "x-csrf-token": "csrf"},
        )
    assert response.status_code == 400
    assert "PRIVATE" not in response.text
    store.assert_not_called()
