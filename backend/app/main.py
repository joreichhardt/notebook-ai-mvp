from fastapi import FastAPI

app = FastAPI(title="Notebook", version="0.1.0", docs_url=None, redoc_url=None)


@app.get("/api/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}
