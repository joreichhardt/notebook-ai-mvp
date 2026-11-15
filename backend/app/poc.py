"""Explicit login/workspace PoC, not the full application's readiness profile.

No document, provider, worker or registration routes are mounted here. The three
real probes are constructed only by this module's lifespan, never from a registry.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth.invitations import router as invitations_router
from app.auth.router import router as auth_router
from app.auth.settings import Settings, get_settings
from app.health import ProbeUnavailable, image_release
from app.notebooks.router import router as notebooks_router
from app.readiness import auth_probe, database_probe, secret_files_probe
from app.workspaces.router import router as workspaces_router

PROFILE = "login-workspace-v1"
SECRET_FILES = (
    Path("/run/secrets/notebook_database_url"),
    Path("/run/secrets/notebook_supabase_anon_key"),
)


class _Runtime:
    def __init__(self, settings: Settings):
        if (
            settings.public_origin != "https://note.yokoma.de"
            or settings.supabase_url != "http://frontend:8081"
            or settings.database_url_file != str(SECRET_FILES[0])
            or settings.supabase_anon_key_file != str(SECRET_FILES[1])
        ):
            raise ProbeUnavailable()
        self._database = database_probe(settings.database_url)
        self._auth = auth_probe(
            settings.supabase_url,
            settings.supabase_anon_key,
            expected_version="v2.197.0",
            allow_internal_http=True,
        )
        self._secrets = secret_files_probe(SECRET_FILES)

    async def check(self) -> bool:
        async def bounded(probe):
            try:
                return await asyncio.wait_for(probe(), timeout=2) is True
            except (TimeoutError, ProbeUnavailable):
                return False

        try:
            async with asyncio.TaskGroup() as group:
                database = group.create_task(bounded(self._database))
                auth = group.create_task(bounded(self._auth))
                files = group.create_task(bounded(self._secrets))
        except ExceptionGroup:
            # TaskGroup joins siblings; upstream diagnostics never reach HTTP/logs.
            return False
        return all(
            not task.cancelled() and task.result() is True for task in (database, auth, files)
        )


_runtime: _Runtime | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _runtime
    _runtime = None
    try:
        _runtime = _Runtime(get_settings())
    except (HTTPException, ProbeUnavailable):
        # The login page can render while dependencies are unavailable.
        _runtime = None
    try:
        yield
    finally:
        _runtime = None


app = FastAPI(
    title="Notebook login PoC", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": {"code": "invalid_request", "message": "Bitte Eingaben prüfen."}},
    )


@app.middleware("http")
async def response_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/api/health/live")
def live():
    return {"status": "ok"}


@app.get("/api/health/ready")
def production_ready():
    # Deliberately not a substitute for the unchanged five-probe production profile.
    return JSONResponse({"status": "not_ready"}, status_code=503)


@app.get("/api/poc/ready")
async def poc_ready():
    release = image_release()
    runtime = _runtime
    # Recheck immutable provenance after awaiting external dependencies.
    if (
        release is not None
        and runtime is not None
        and await runtime.check()
        and image_release() == release
        and _runtime is runtime
    ):
        return JSONResponse(
            {
                "status": "ready",
                "profile": PROFILE,
                "release": release,
                "documents": False,
                "chat": False,
            }
        )
    return JSONResponse({"status": "not_ready", "profile": PROFILE}, status_code=503)


@app.get("/api/capabilities")
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


app.include_router(auth_router)
app.include_router(invitations_router)
app.include_router(workspaces_router)
app.include_router(notebooks_router)
