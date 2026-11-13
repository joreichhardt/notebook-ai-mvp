"""Unregistered, default-CLOSED TXT/MD upload adapter; no runtime storage wiring.

Server dependencies only: preserve the verified Identity in every fresh actor
transaction. StorageIo must use a separate restricted login; no implicit engine,
credentials, privileged fallback or request-scoped transaction is supplied.
"""

import re
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.security import check_csrf
from app.auth.settings import get_settings
from app.auth.supabase import Identity
from app.capabilities import capabilities
from app.db.session import authenticated_session, identity
from app.documents.body_limit import UploadBodyError
from app.documents.multipart import parse_text_form
from app.documents.service import DocumentError, IoTx, UploadReceipt, UploadService
from app.documents.storage import StorageConfig

router = APIRouter(prefix="/api/notebooks", tags=["documents"])
AuthenticatedSessionFactory = Callable[[Identity], AbstractContextManager[Session]]


@dataclass(frozen=True)
class StorageIo:
    """Trusted server-only wiring, never client input; service checks restricted role."""

    io_tx: IoTx
    storage: StorageConfig


def storage_io() -> StorageIo | None:
    return None  # Main owns separate reviewed provisioning/wiring, NOT this adapter.


def authenticated_session_factory() -> AuthenticatedSessionFactory:
    return contextmanager(authenticated_session)


def upload_service(user: Identity) -> UploadService:
    factory = authenticated_session_factory()

    @contextmanager
    def actor_tx(actor_id: UUID):
        if type(actor_id) is not UUID or actor_id != user.id:
            raise DocumentError("authentication_required", 401)
        with factory(user) as db:
            yield db

    io = storage_io()
    return UploadService(
        actor_tx=actor_tx, io_tx=io.io_tx if io else None, storage=io.storage if io else None
    )


_ERRORS = {
    "authentication_required": (401, "Anmeldung erforderlich."),
    "invalid_credentials": (401, "Anmeldung oder Sitzung ungültig."),
    "invalid_identity": (401, "Sitzung ungültig."),
    "email_unverified": (403, "E-Mail-Adresse ist nicht bestätigt."),
    "origin_denied": (403, "Anfrageursprung nicht erlaubt."),
    "csrf_denied": (403, "Sicherheitsprüfung fehlgeschlagen."),
    "admission_required": (403, "Eine angenommene Einladung ist erforderlich."),
    "write_required": (403, "Schreibberechtigung erforderlich."),
    "notebook_not_found": (404, "Notebook nicht gefunden."),
    "invalid_notebook": (422, "Ungültiges Notebook."),
    "invalid_idempotency_key": (422, "Ungültiger Idempotenzschlüssel."),
    "quota_capacity": (429, "Kapazität vorübergehend ausgeschöpft."),
    "upload_idempotency_conflict": (409, "Idempotenzschlüssel bereits anders verwendet."),
    "unsupported_media": (415, "Nur TXT- und Markdown-Dateien sind erlaubt."),
    "invalid_upload": (400, "Ungültiger Upload."),
    "invalid_text": (422, "Die Datei muss gültigen UTF-8-Text enthalten."),
    "empty_text": (422, "Die Datei enthält keinen Text."),
    "upload_too_large": (413, "Die Datei ist zu groß."),
    "text_too_large": (413, "Die Textmenge ist zu groß."),
}

_BODY_ERRORS = {
    **_ERRORS,
    "upload_timeout": (408, "Zeitlimit beim Lesen des Uploads überschritten."),
    "upload_interrupted": (400, "Upload unterbrochen."),
}


def _failure(exc: Exception) -> HTTPException:
    # Do not read attributes of untrusted subclasses (properties/__getattribute__).
    code = None
    if type(exc) is DocumentError or type(exc) is UploadBodyError:
        code = cast(DocumentError | UploadBodyError, exc).code
    elif type(exc) is HTTPException:
        detail = exc.detail
        if type(detail) is dict:
            # Even builtin dict lookup can call a hostile stored key's __eq__.
            for key, value in dict.items(detail):
                if type(key) is str and key == "code":
                    code = value
                    break
    errors = _BODY_ERRORS if type(exc) is UploadBodyError else _ERRORS
    if type(code) is str and code in errors:
        status, message = errors[code]
        return HTTPException(status, detail={"code": code, "message": message})
    return HTTPException(
        503, detail={"code": "upload_unavailable", "message": "Upload vorübergehend nicht möglich."}
    )


@router.post("/{notebook_id}/documents", status_code=202, response_model=UploadReceipt)
async def upload_document(notebook_id: str, request: Request):
    if capabilities().get("documents") is not True:
        raise HTTPException(404, detail={"code": "not_found", "message": "Nicht gefunden."})
    try:
        user = await identity(request)
        check_csrf(request, get_settings().public_origin)
        try:
            notebook = UUID(notebook_id)
        except ValueError:
            raise DocumentError("invalid_notebook", 422) from None
        service = upload_service(user)
        service.authorize_upload(actor_id=user.id, notebook_id=notebook)
        keys = [
            value for name, value in request.scope["headers"] if name.lower() == b"idempotency-key"
        ]
        if len(keys) != 1 or re.fullmatch(rb"[A-Za-z0-9._:-]{1,128}", keys[0]) is None:
            raise DocumentError("invalid_idempotency_key", 422)
        key = keys[0].decode("ascii")
        upload = await parse_text_form(request)
        receipt = await service.upload(
            actor_id=user.id,
            notebook_id=notebook,
            idempotency_key=key,
            upload=upload,
        )
        if type(receipt) is not UploadReceipt:
            raise DocumentError("upload_unavailable")
        return receipt
    except Exception as exc:  # noqa: BLE001 -- opaque HTTP boundary; cancellation is BaseException.
        raise _failure(exc) from None
