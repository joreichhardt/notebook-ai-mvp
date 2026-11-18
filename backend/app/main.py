from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.auth.administration import router as administration_router
from app.auth.invitations import router as invitations_router
from app.auth.router import router as auth_router
from app.auth.settings import get_settings
from app.capabilities import router as capabilities_router
from app.connections.router import router as connections_router
from app.documents.chat_router import router as chat_router
from app.documents.router import router as documents_router
from app.documents.runtime import local_upload_runtime, upload_settings
from app.documents.upload_router import router as document_upload_router
from app.health import ProbeUnavailable, Readiness
from app.health import router as health_router
from app.notebooks.router import router as notebooks_router
from app.readiness import (
    auth_probe,
    database_probe,
    local_storage_probe,
    local_worker_probe,
    secret_files_probe,
)
from app.workspaces.router import router as workspaces_router


class ReadinessSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOTEBOOK_")
    local_readiness_enabled: bool = False


def runtime_checks() -> Readiness:
    settings = get_settings()
    upload = local_upload_runtime()
    if upload is None:
        raise ProbeUnavailable()
    secrets = (
        settings.database_url_file,
        settings.supabase_anon_key_file,
        upload_settings().storage_io_database_url_file,
    )
    return Readiness(
        {
            "database": database_probe(settings.database_url),
            "auth": auth_probe(
                settings.supabase_url,
                settings.supabase_anon_key,
                expected_version="v2.197.0",
                allow_internal_http=True,
            ),
            "secrets": secret_files_probe(tuple(Path(path) for path in secrets)),
            "storage": local_storage_probe(upload),
            "worker": local_worker_probe(),
        }
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.readiness = None
    if ReadinessSettings().local_readiness_enabled:
        try:
            app.state.readiness = runtime_checks()
        except Exception:  # noqa: BLE001 — do not disclose runtime configuration
            raise RuntimeError("readiness_configuration_unavailable") from None
    try:
        yield
    finally:
        app.state.readiness = None


app = FastAPI(
    title="Notebook",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Pydantic errors include submitted values, which may contain passwords or API keys.
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
def live() -> dict[str, str]:
    return {"status": "ok"}


# Readiness stays unavailable until trusted startup installs every real runtime probe.
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(workspaces_router)
app.include_router(notebooks_router)

app.include_router(capabilities_router)

app.include_router(invitations_router)
app.include_router(administration_router)

app.include_router(connections_router)

# Registration does not enable the feature: the document dependency remains
# literal-True capability gated before authentication or service construction.
app.include_router(documents_router)
app.include_router(document_upload_router)
app.include_router(chat_router)
