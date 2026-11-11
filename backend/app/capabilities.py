"""Public UI capabilities, without configuration values or credential metadata."""

from fastapi import APIRouter, HTTPException

from app.auth.settings import get_settings

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
    return {
        "auth": {
            "password_login": password_login,
            "registration": False,
            "google": False,
            "recovery": False,
        },
        "invitations": True,
        "members": True,
        "documents": False,
        "chat": False,
    }
