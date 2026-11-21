from uuid import uuid4

from app.auth.settings import Settings
from app.auth.supabase import Identity
from app.documents import chat_router as chat
from app.documents.router import document_identity, document_service
from app.documents.service import DocumentError
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_private_chat_denies_disabled_csrf_and_unreadable_source(monkeypatch):
    actor, notebook, document = uuid4(), uuid4(), uuid4()
    reads = []

    class Reader:
        def source_text(self, *, actor_id, notebook_id, document_id, after):
            reads.append((actor_id, notebook_id, document_id, after))
            raise DocumentError("notebook_not_found", 404)

    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[document_identity] = lambda: Identity(
        id=actor, email="test@example.test"
    )
    app.dependency_overrides[document_service] = Reader
    monkeypatch.setattr(chat, "get_settings", lambda: Settings(public_origin="https://test"))
    monkeypatch.setattr(chat, "private_chat_configured", lambda: False)
    client = TestClient(app, base_url="https://test")
    url = f"/api/notebooks/{notebook}/chat"
    body = {
        "document_id": str(document),
        "expected_version_id": str(uuid4()),
        "connection_id": str(uuid4()),
        "model": "test",
        "question": "Was?",
    }
    assert client.post(url, json=body).status_code == 404
    assert client.post(url, json=body).json()["detail"]["code"] == "not_found"
    monkeypatch.setattr(chat, "private_chat_configured", lambda: True)
    assert client.post(url, json=body).status_code == 403
    assert not reads
    client.cookies.set("__Host-notebook_csrf", "synthetic")
    response = client.post(
        url, json=body, headers={"Origin": "https://test", "X-CSRF-Token": "synthetic"}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "notebook_not_found"
    assert reads == [(actor, notebook, document, 0)]
