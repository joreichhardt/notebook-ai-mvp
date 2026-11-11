from app.main import app
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel


def test_api_responses_are_not_cacheable():
    response = TestClient(app).get('/api/health/live')
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-content-type-options'] == 'nosniff'


def test_validation_does_not_echo_secret_input():
    class Payload(BaseModel):
        value: int

    router = APIRouter()

    @router.post('/_validation_test')
    def validate(payload: Payload):
        return payload

    previous = list(app.router.routes)
    try:
        app.include_router(router)
        response = TestClient(app).post('/_validation_test', json={'value': 'SECRET_SENTINEL'})
        assert response.status_code == 422
        assert 'SECRET_SENTINEL' not in response.text
    finally:
        app.router.routes[:] = previous
