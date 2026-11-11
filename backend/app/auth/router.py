import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr

from app.auth.security import CSRF_COOKIE, SESSION_COOKIE, check_csrf, check_origin, fail
from app.auth.settings import get_settings
from app.auth.supabase import SupabaseAuth

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr


@router.post("/login")
async def login(body: Login, request: Request, response: Response):
    settings = get_settings()
    check_origin(request, settings.public_origin)
    auth = SupabaseAuth(settings)
    result = await auth.request(
        "POST",
        "token?grant_type=password",
        data={"email": body.email, "password": body.password.get_secret_value()},
    )
    token = result.get("access_token")
    if not isinstance(token, str) or not token:
        fail(503, "auth_unavailable", "Anmeldedienst lieferte keine Sitzung.")
    user = await auth.identity(token)
    ttl = min(int(result.get("expires_in", 3600)), 3600)
    response.set_cookie(
        SESSION_COOKIE, token, max_age=ttl, secure=True, httponly=True, samesite="lax"
    )
    csrf = secrets.token_urlsafe(32)
    response.set_cookie(CSRF_COOKIE, csrf, max_age=ttl, secure=True, httponly=False, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return {"user": {"id": str(user.id), "email": user.email}, "csrf_token": csrf}


@router.get("/session")
async def session(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return {"user": None}
    try:
        user = await SupabaseAuth(get_settings()).identity(token)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        clear_cookies(response)
        return {"user": None}
    return {
        "user": {"id": str(user.id), "email": user.email},
        "csrf_token": request.cookies.get(CSRF_COOKIE),
    }


def clear_cookies(response: Response):
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, secure=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    check_csrf(request, get_settings().public_origin)
    response.status_code = 204
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        try:
            await SupabaseAuth(get_settings()).request("POST", "logout", token)
        except HTTPException as exc:
            if exc.status_code != 401:
                response = JSONResponse(
                    status_code=200,
                    content={
                        "detail": {
                            "code": "revocation_unconfirmed",
                            "message": "Lokal abgemeldet. Serverseitiger Widerruf nicht bestätigt.",
                        },
                        "local_logout": True,
                        "remote_revoked": False,
                    },
                )
    clear_cookies(response)
    return response
