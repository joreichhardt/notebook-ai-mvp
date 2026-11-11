from app.auth.settings import Settings
from app.capabilities import router
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_capabilities_never_return_configuration_values(monkeypatch):
    monkeypatch.setattr('app.capabilities.get_settings', lambda: Settings(
        supabase_url='https://private.invalid', supabase_anon_key='SECRET_SENTINEL'
    ))
    response = client().get('/api/capabilities')
    assert response.json()['auth']['password_login'] is True
    assert 'SECRET_SENTINEL' not in response.text
    assert 'private.invalid' not in response.text
    assert response.json()['auth']['google'] is False


def test_missing_config_hides_login_without_failure_details(monkeypatch):
    def unavailable():
        raise HTTPException(503, 'private file path')
    monkeypatch.setattr('app.capabilities.get_settings', unavailable)
    response = client().get('/api/capabilities')
    assert response.status_code == 200
    assert response.json()['auth']['password_login'] is False
    assert 'private file' not in response.text
