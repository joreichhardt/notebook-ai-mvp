from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth.invitations import router as invitations_router
from app.auth.router import router as auth_router
from app.capabilities import router as capabilities_router
from app.connections.router import router as connections_router
from app.documents.router import router as documents_router
from app.documents.upload_router import router as document_upload_router
from app.notebooks.router import router as notebooks_router
from app.workspaces.router import router as workspaces_router

app = FastAPI(title="Notebook", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)


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


app.include_router(auth_router)
app.include_router(workspaces_router)
app.include_router(notebooks_router)

app.include_router(capabilities_router)

app.include_router(invitations_router)

app.include_router(connections_router)

# Registration does not enable the feature: the document dependency remains
# literal-True capability gated before authentication or service construction.
app.include_router(documents_router)
app.include_router(document_upload_router)
