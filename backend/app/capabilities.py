"""Public UI capabilities, without configuration values or credential metadata."""

import os

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from pydantic_settings import SettingsError

from app.auth.settings import get_settings
from app.documents.runtime import local_upload_runtime
from app.documents.service import DocumentError
from app.documents.storage import StorageError
from app.providers.config import settings as provider_settings

router = APIRouter(prefix="/api", tags=["capabilities"])


def private_chat_configured() -> bool:
    if os.environ.get("NOTEBOOK_PRIVATE_CHAT_ENABLED") != "true":
        return False
    try:
        config = provider_settings()
    except (SettingsError, ValidationError):
        return False
    return config.configured and bool(config.endpoints)


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
        "chat": documents and private_chat_configured(),
    }
