"""Internal HTTP readiness for a separate supervised TXT worker process."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.health import image_release
from app.workers.delegation import BoundedThreadRunner
from app.workers.local_runtime import build_consumer, configured_runtime
from app.workers.supervisor import TextWorkerSupervisor


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = configured_runtime()
    if runtime is None:
        raise RuntimeError("worker_disabled")
    runner = BoundedThreadRunner()
    supervisor = None
    try:
        supervisor = TextWorkerSupervisor(build_consumer(runtime, runner))
        task = asyncio.create_task(supervisor.run())
        app.state.worker = supervisor
        try:
            yield
        finally:
            app.state.worker = None
            await supervisor.close()
            if task.done():
                await asyncio.gather(task, return_exceptions=True)
    finally:
        if supervisor is None:
            await runner.close(timeout=3)
        if not runner.snapshot().pending:
            runtime.dispose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/ready")
async def ready():
    release = image_release()
    worker = getattr(app.state, "worker", None)
    headers = {"Cache-Control": "no-store"}
    if release and worker is not None and worker.is_ready():
        return JSONResponse({"status": "ready", "release": release}, headers=headers)
    return JSONResponse({"status": "not_ready"}, status_code=503, headers=headers)
