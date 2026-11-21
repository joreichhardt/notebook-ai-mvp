"""Opt-in private grounded answer endpoint, never mounted by the login profile."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.auth.security import check_csrf
from app.auth.settings import get_settings
from app.auth.supabase import Identity
from app.capabilities import private_chat_configured
from app.connections.execution import ExecutionService
from app.documents.grounded_answer import GroundedAnswer, grounded_answer
from app.documents.router import document_identity, document_service, sanitized
from app.documents.service import DocumentError, UploadService

router = APIRouter(prefix="/api/notebooks")


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    expected_version_id: UUID
    connection_id: UUID
    model: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)


@router.post("/{notebook_id}/chat", response_model=GroundedAnswer)
async def ask(
    notebook_id: UUID,
    body: Question,
    request: Request,
    user: Annotated[Identity, Depends(document_identity)],
    service: Annotated[UploadService, Depends(document_service)],
):
    if not private_chat_configured():
        raise HTTPException(404, {"code": "not_found"})
    check_csrf(request, get_settings().public_origin)

    async def read_page(actor, notebook, document):
        return await run_in_threadpool(
            service.source_text, actor_id=actor, notebook_id=notebook, document_id=document, after=0
        )

    try:
        return await grounded_answer(
            actor_id=user.id,
            notebook_id=notebook_id,
            document_id=body.document_id,
            expected_version_id=body.expected_version_id,
            connection_id=body.connection_id,
            model=body.model,
            question=body.question,
            read_page=read_page,
            execution=ExecutionService(),
        )
    except DocumentError as error:
        raise sanitized(error) from None
    except (HTTPException, ValueError, TimeoutError):
        # Never return provider text, validation inputs or secret-store details.
        raise HTTPException(
            409, {"code": "answer_unavailable", "message": "Keine belegte Antwort verfügbar."}
        ) from None
