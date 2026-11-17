"""Synthetic ASGI projection/error checks; not database authorization evidence."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.auth.supabase import Identity
from app.documents import router as module
from app.documents.service import DocumentDTO, SourceChunkDTO, SourceTextDTO
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def scene():
    user = Identity(id=uuid4(), email="synthetic@example.test")
    notebook, document = uuid4(), uuid4()
    value = SourceTextDTO(
        document=DocumentDTO(
            id=document,
            notebook_id=notebook,
            version_id=uuid4(),
            filename="note.txt",
            status="ready",
            error_code=None,
        ),
        items=[
            SourceChunkDTO(id=uuid4(), ordinal=0, page=None, text="<b>inert</b>", truncated=False)
        ],
        next_ordinal=None,
    )
    calls = []

    def source_text(**kwargs):
        calls.append(kwargs)
        return value

    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[module.document_identity] = lambda: user
    app.dependency_overrides[module.document_service] = lambda: SimpleNamespace(
        source_text=source_text
    )
    return app, user, notebook, document, value, calls


def get(app, notebook, document, query=""):
    async def run():
        async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
            return await client.get(f"/api/notebooks/{notebook}/documents/{document}/text{query}")

    return asyncio.run(run())


def test_current_scope_projection_and_no_store(scene):
    app, user, notebook, document, value, calls = scene
    response = get(app, notebook, document)
    assert response.status_code == 200
    assert response.json() == value.model_dump(mode="json")
    assert response.headers["cache-control"] == "no-store, private"
    assert calls == [
        {"actor_id": user.id, "notebook_id": notebook, "document_id": document, "after": 0}
    ]


@pytest.mark.parametrize(
    "query", ["?after=-1", "?after=2000001", "?after=nan", "?after=１２", "?after=" + "9" * 100]
)
def test_invalid_cursor_is_opaque_and_never_calls_service(scene, query):
    app, _, notebook, document, _, calls = scene
    response = get(app, notebook, document, query)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert calls == []


def test_service_failure_is_opaque(scene):
    app, _, notebook, document, _, _ = scene

    def source_text(**kwargs):
        raise RuntimeError("private driver diagnostic")

    app.dependency_overrides[module.document_service] = lambda: SimpleNamespace(
        source_text=source_text
    )
    response = get(app, notebook, document)
    assert response.status_code == 503
    assert "private driver" not in response.text


def test_projection_binds_identifiers_instead_of_interpolating_sql():
    from app.db.models import Document
    from app.documents.service import UploadService
    from sqlalchemy.dialects import postgresql

    notebook, document = uuid4(), uuid4()
    statement = UploadService._projection(notebook).where(Document.id == document)
    compiled = statement.compile(dialect=postgresql.dialect())
    assert notebook in compiled.params.values() and document in compiled.params.values()
    assert str(notebook) not in str(compiled) and str(document) not in str(compiled)


def test_closed_capability_hides_text_route_before_identity(monkeypatch):
    monkeypatch.setattr(module, "capabilities", lambda: {"documents": False})

    async def forbidden(request):
        raise AssertionError("identity must not run")

    monkeypatch.setattr(module, "identity", forbidden)
    app = FastAPI()
    app.include_router(module.router)
    assert get(app, uuid4(), uuid4()).status_code == 404
