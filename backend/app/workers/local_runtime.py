"""Opt-in single-delivery worker process; no HTTP execution or role provisioning.

Run only under a process manager with an external hard deadline. A held result
must stop automatic restarts: cancellation alone cannot terminate a DB thread.
Only database-validated grants select actors/jobs. No CLI identity or BWS token.
"""

import asyncio
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.documents.local_storage import LocalStorage, LocalStorageConfig
from app.documents.runtime import _secret


class WorkerUnavailable(RuntimeError):
    pass


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOTEBOOK_")
    local_text_worker_enabled: bool = False
    local_storage_root: str = ""
    database_url_file: str = ""
    text_scheduler_database_url_file: str = ""


@dataclass(frozen=True)
class LocalTextRuntime:
    storage: LocalStorageConfig
    app_engine: Engine = field(repr=False)
    scheduler_engine: Engine = field(repr=False)

    @contextmanager
    def scheduler_tx(self):
        with Session(self.scheduler_engine) as db, db.begin():
            yield db

    def dispose(self):
        try:
            self.app_engine.dispose()
        finally:
            self.scheduler_engine.dispose()


def _engine(path: str, expected: str) -> Engine:
    if not path or not Path(path).is_absolute():
        raise WorkerUnavailable("configuration_unavailable")
    parsed = make_url(_secret(path))
    if (
        parsed.drivername not in ("postgresql", "postgresql+psycopg")
        or parsed.username != expected
        or "user" in parsed.query
    ):
        raise WorkerUnavailable("configuration_unavailable")
    options = parsed.query.get("options", "")
    if not isinstance(options, str):
        raise WorkerUnavailable("configuration_unavailable")
    return create_engine(
        parsed.set(drivername="postgresql+psycopg"),
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        connect_args={
            "connect_timeout": 3,
            "options": f"{options} -c statement_timeout=3000 -c lock_timeout=1000".strip(),
        },
    )


def configured_runtime() -> LocalTextRuntime | None:
    app = None
    try:
        settings = WorkerSettings()
        if not settings.local_text_worker_enabled:
            return None
        config = LocalStorageConfig(Path(settings.local_storage_root))
        fd = os.open(config.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            LocalStorage._private(fd, directory=True)
        finally:
            os.close(fd)
        app = _engine(settings.database_url_file, "notebook_app")
        scheduler = _engine(settings.text_scheduler_database_url_file, "notebook_text_scheduler")
        return LocalTextRuntime(config, app, scheduler)
    except Exception:  # noqa: BLE001 — no DSN, file contents or driver details in errors
        if app is not None:
            try:
                app.dispose()
            except Exception:  # noqa: BLE001 — cleanup errors also remain opaque
                raise WorkerUnavailable("configuration_unavailable") from None
        raise WorkerUnavailable("configuration_unavailable") from None


async def _finish_one(consumer, runner) -> str:
    """No second delivery; require actual executor settlement, not task cancellation."""
    result = None
    try:
        result = await consumer.run_one()
    except Exception:  # noqa: BLE001 — unknown outcome remains held, never retried here
        result = "held"
    finally:
        consumer.request_stop()
        closed = await runner.close(timeout=3)
    snapshot = consumer.snapshot()
    if (
        closed is not True
        or runner.snapshot().pending
        or snapshot.outcome_unknown
        or snapshot.lease_failed
        or snapshot.cancelled
        or snapshot.renewal_active
    ):
        return "held"
    if result == "idle" and snapshot.phase == "idle":
        return "idle"
    if (
        type(result) is str
        and result in ("succeeded", "failed", "cancelled")
        and snapshot.phase == "settled"
        and snapshot.terminal_observed
        and snapshot.acknowledgment == "confirmed"
    ):
        return result
    return "held"


def build_consumer(runtime: LocalTextRuntime, runner, *, supervised: bool = False):
    from app.workers.delegation import DelegationRepository, ExactTextConsumer, SchedulerFactory
    from app.workers.text import TextWorker

    repository = DelegationRepository(
        SchedulerFactory(runtime.scheduler_tx, pool_timeout=1, connect_timeout=3), runner
    )
    worker = TextWorker(
        runtime.app_engine,
        store_factory=lambda authorize: LocalStorage(runtime.storage, authorize=authorize),
        operation_runner=runner,
        work_observation=supervised,
        progress_observer=lambda progress: None,
    )
    return ExactTextConsumer(repository, worker, pool_timeout=1, connect_timeout=3)


async def execute(runtime: LocalTextRuntime) -> str:
    from app.workers.delegation import BoundedThreadRunner

    runner = None
    assembled = False
    try:
        runner = BoundedThreadRunner()
        consumer = build_consumer(runtime, runner)
        assembled = True
        return await _finish_one(consumer, runner)
    finally:
        if runner is None:
            runtime.dispose()
        else:
            if not assembled:
                await runner.close(timeout=3)
            # Never dispose a pool while its real DB callback remains outstanding.
            if not runner.snapshot().pending:
                runtime.dispose()


def main() -> int:
    if len(sys.argv) != 1:
        print(json.dumps({"status": "invalid_arguments"}))
        return 64
    try:
        runtime = configured_runtime()
        if runtime is None:
            print(json.dumps({"status": "disabled"}))
            return 78
        status = asyncio.run(execute(runtime))
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 — opaque CLI boundary
        print(json.dumps({"status": "held"}))
        return 75
    if status not in ("idle", "succeeded", "failed", "cancelled"):
        status = "held"
    print(json.dumps({"status": status}))
    return 75 if status == "held" else 0


if __name__ == "__main__":
    raise SystemExit(main())
