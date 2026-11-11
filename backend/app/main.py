from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

app = FastAPI(title="Notebook", version="0.1.0", docs_url=None, redoc_url=None,
              openapi_url=None)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Pydantic errors include submitted values, which may contain passwords or API keys.
    return JSONResponse(status_code=422, content={"detail": {
        "code": "invalid_request", "message": "Bitte Eingaben prüfen."
    }})


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
