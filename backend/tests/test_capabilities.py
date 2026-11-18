from app.auth.settings import Settings
from app.capabilities import router
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_capabilities_never_return_configuration_values(monkeypatch):
    monkeypatch.setattr(
        "app.capabilities.get_settings",
        lambda: Settings(
            supabase_url="https://private.invalid", supabase_anon_key=SecretStr("SECRET_SENTINEL")
        ),
    )
    response = client().get("/api/capabilities")
    assert response.json()["auth"]["password_login"] is True
    assert "SECRET_SENTINEL" not in response.text
    assert "private.invalid" not in response.text
    assert response.json()["auth"]["google"] is False


def test_missing_config_hides_login_without_failure_details(monkeypatch):
    def unavailable():
        raise HTTPException(503, "private file path")

    monkeypatch.setattr("app.capabilities.get_settings", unavailable)
    response = client().get("/api/capabilities")
    assert response.status_code == 200
    assert response.json()["auth"]["password_login"] is False
    assert "private file" not in response.text


def test_private_chat_requires_uploads_opt_in_and_provider_configuration(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("app.capabilities.local_upload_runtime", lambda: object())
    monkeypatch.delenv("NOTEBOOK_PRIVATE_CHAT_ENABLED", raising=False)
    monkeypatch.setattr(
        "app.capabilities.provider_settings", lambda: SimpleNamespace(configured=True, endpoints=[])
    )
    with client() as c:
        assert c.get("/api/capabilities").json()["chat"] is False
        monkeypatch.setenv("NOTEBOOK_PRIVATE_CHAT_ENABLED", "true")
        assert c.get("/api/capabilities").json()["chat"] is False
        monkeypatch.setattr(
            "app.capabilities.provider_settings",
            lambda: SimpleNamespace(configured=True, endpoints=[object()]),
        )
        assert c.get("/api/capabilities").json()["chat"] is True
        monkeypatch.setattr("app.capabilities.local_upload_runtime", lambda: None)
        assert c.get("/api/capabilities").json()["chat"] is False


def test_invalid_provider_environment_keeps_capabilities_available(monkeypatch):
    monkeypatch.setattr("app.capabilities.local_upload_runtime", lambda: object())
    monkeypatch.setenv("NOTEBOOK_PRIVATE_CHAT_ENABLED", "true")
    monkeypatch.setenv("NOTEBOOK_PROVIDER_ENDPOINTS", "{malformed config")
    with client() as c:
        response = c.get("/api/capabilities")
    assert response.status_code == 200
    assert response.json()["chat"] is False
    assert "malformed" not in response.text
