from app.main import app
from fastapi.testclient import TestClient


def test_liveness():
    assert TestClient(app).get("/api/health/live").json() == {"status": "ok"}
