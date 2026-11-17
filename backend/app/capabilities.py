"""Public UI capabilities, without configuration values or credential metadata."""

from fastapi import APIRouter, HTTPException

from app.auth.settings import get_settings
from app.documents.runtime import local_upload_runtime
from app.documents.service import DocumentError
from app.documents.storage import StorageError

router = APIRouter(prefix="/api", tags=["capabilities"])


@router.get("/capabilities")
def capabilities():
    try:
        settings = get_settings()
        password_login = bool(
            settings.supabase_url and settings.supabase_anon_key.get_secret_value()
        )
    except HTTPException:
        password_login = False
    try:
        documents = local_upload_runtime() is not None
    except (HTTPException, DocumentError, StorageError):
        documents = False
    return {
        "auth": {
            "password_login": password_login,
            "registration": False,
            "google": False,
            "recovery": False,
        },
        "invitations": True,
        "members": True,
        "documents": documents,
        "chat": False,
    }
