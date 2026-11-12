"""Cookie/CSRF and invitation primitives; tokens never enter application logs."""

import hashlib
import secrets
from typing import NoReturn

from fastapi import HTTPException, Request

SESSION_COOKIE = "__Host-notebook_session"
CSRF_COOKIE = "__Host-notebook_csrf"


def fail(status: int, code: str, message: str) -> NoReturn:
    raise HTTPException(status, detail={"code": code, "message": message})


def check_origin(request: Request, origin: str):
    if request.headers.get("origin") != origin:
        fail(403, "origin_denied", "Anfrageursprung nicht erlaubt.")


def check_csrf(request: Request, origin: str):
    check_origin(request, origin)
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("x-csrf-token", "")
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        fail(403, "csrf_denied", "Sicherheitsprüfung fehlgeschlagen.")


def invitation_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, invitation_hash(token)


def invitation_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
