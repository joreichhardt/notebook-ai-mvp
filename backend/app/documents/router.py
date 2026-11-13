"""Unregistered, capability-closed read-only document adapter.

Only the server-verified Identity may bind a fresh short actor transaction.
No Storage/provider/IO transaction factory and no mutation routes are supplied.
"""

from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.auth.supabase import Identity
from app.capabilities import capabilities
from app.db.session import authenticated_session, identity
from app.documents.service import DocumentDTO, DocumentError, UploadService

router = APIRouter(prefix="/api/notebooks", tags=["documents"])


class DocumentList(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: list[DocumentDTO]


# Trusted literal codes/statuses only; never echo a service/driver/auth message.
_ERRORS = {
    "authentication_required": (401, "Bitte anmelden."),
    "invalid_credentials": (401, "Sitzung ungültig."),
    "invalid_identity": (401, "Sitzung ungültig."),
    "email_unverified": (403, "E-Mail-Adresse ist nicht bestätigt."),
    "admission_required": (403, "Eine angenommene Einladung ist erforderlich."),
    "notebook_not_found": (404, "Notebook nicht gefunden."),
    "invalid_request": (422, "Bitte Eingaben prüfen."),
    "documents_unavailable": (503, "Dokumente sind derzeit nicht verfügbar."),
}


def fixed_error(code="documents_unavailable"):
    status, message = _ERRORS[code]
    return HTTPException(status, {"code": code, "message": message})


def sanitized(error):
    """Exact builtin codes only, protected getters; never return untrusted objects."""
    try:
        if type(error) is DocumentError:
            code = error.code
        elif isinstance(error, HTTPException):
            detail = error.detail
            code = dict.get(detail, "code") if type(detail) is dict else None
        else:
            code = None
        if type(code) is str:
            for trusted in _ERRORS:
                if code == trusted:
                    return fixed_error(trusted)
    except Exception:  # noqa: BLE001 — hostile getter boundary, never log private errors
        return fixed_error()
    return fixed_error()


def documents_enabled():
    try:
        public = capabilities()
        enabled = type(public) is dict and dict.get(public, "documents") is True
    except Exception:  # noqa: BLE001 — unavailable/malformed capability is closed
        enabled = False
    if not enabled:
        raise HTTPException(404, {"code": "not_found", "message": "Nicht gefunden."})


async def document_identity(
    request: Request, gate: Annotated[None, Depends(documents_enabled)]
) -> Identity:
    try:
        user = await identity(request)
        if type(user) is not Identity or type(user.id) is not UUID:
            raise fixed_error("invalid_identity")
        return user
    except Exception as error:  # noqa: BLE001 — fixed public authentication boundary
        raise sanitized(error) from None


def document_service(user: Annotated[Identity, Depends(document_identity)]) -> UploadService:
    # Dependency injection is trusted server code; never accept a browser UUID as auth.
    if type(user) is not Identity or type(user.id) is not UUID:
        raise fixed_error("invalid_identity")

    @contextmanager
    def actor_tx(actor: UUID):
        if type(actor) is not UUID or actor != user.id:
            raise DocumentError("authentication_required", 401)
        # Each service attempt calls the existing factory afresh; contextmanager
        # drives its commit/rollback/close before the service returns DTO values.
        with contextmanager(authenticated_session)(user) as db:
            yield db

    try:
        return UploadService(actor_tx=actor_tx)
    except Exception as error:  # noqa: BLE001 — constructor errors must remain opaque
        raise sanitized(error) from None


@router.get("/{notebook_id}/documents", response_model=DocumentList)
def list_documents(
    notebook_id: str,
    service: Annotated[UploadService, Depends(document_service)],
    user: Annotated[Identity, Depends(document_identity)],
    limit: str = "100",
) -> DocumentList:
    try:
        # Manual bounded validation avoids reflecting path/query inputs even in
        # an isolated app without main.py's RequestValidationError handler.
        if len(notebook_id) != 36:
            raise fixed_error("invalid_request")
        try:
            notebook = UUID(notebook_id)
        except ValueError:
            raise fixed_error("invalid_request") from None
        if len(limit) > 3 or not limit.isascii() or not limit.isdecimal():
            raise fixed_error("invalid_request")
        bound = int(limit)
        if not 1 <= bound <= 100:
            raise fixed_error("invalid_request")
        rows = service.list_documents(actor_id=user.id, notebook_id=notebook, limit=bound)
        # Revalidate/project the exact DTO shape inside the safe error boundary,
        # not FastAPI's post-return response-validation exception path.
        return DocumentList(items=[DocumentDTO.model_validate(row) for row in rows])
    except Exception as error:  # noqa: BLE001 — service/DTO/commit failures stay opaque
        raise sanitized(error) from None
