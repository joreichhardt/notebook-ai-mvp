"""Explicit trusted actor/job invocation, NOT an unattended dispatcher or HTTP API.

The caller owns identity issuance. IDs must come from verified durable server
state; never let a browser choose an actor. Ordinary SQL uses notebook_app only.
"""
import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID, uuid5

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.db.leases import claim_job, finish_job, heartbeat_job, lock_job_for_publish
from app.db.models import Admission, Chunk, DocumentVersion, IngestionJob
from app.documents.storage import (
    Authorize,
    ObjectScope,
    StorageConfig,
    StorageError,
    SupabaseStorage,
)
from app.ingestion.text import TextChunk, TextRejected, chunks, decode_text


class LeaseLost(RuntimeError):
    pass


class OriginalStore(Protocol):
    async def read(self, actor_id: UUID, scope: ObjectScope) -> bytes: ...


@dataclass(frozen=True)
class Source:
    scope: ObjectScope
    sha256: str
    byte_count: int
    media_type: str
    embedding: bool


class TextWorker:
    def __init__(self, engine: Engine, *, storage: StorageConfig | None = None,
                 store_factory: Callable[[Authorize], OriginalStore] | None = None,
                 lease_seconds: int = 30, heartbeat_seconds: float = 5):
        if not 1 <= lease_seconds <= 300 or not 0 < heartbeat_seconds < lease_seconds / 2:
            raise ValueError("invalid bounded heartbeat/lease")
        self.engine = engine
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        # No synthetic store is constructed by default. Unconfigured means fail closed.
        self.store_factory = store_factory or (lambda auth: SupabaseStorage(storage, authorize=auth))

    @contextmanager
    def _session(self, actor: UUID) -> Iterator[Session]:
        with Session(self.engine) as session, session.begin():
            safe = session.execute(text(
                "SELECT current_user='notebook_app' AND NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )).scalar_one()
            if safe is not True:
                raise RuntimeError("unsafe_database_role")
            session.execute(text("SET LOCAL statement_timeout='3s'"))
            session.execute(text("SET LOCAL lock_timeout='1s'"))
            session.execute(text("SELECT set_config('request.jwt.claim.sub',:actor,true)"),
                            {"actor": str(actor)})
            if session.get(Admission, actor) is None:
                raise LeaseLost("admission_revoked")
            yield session

    def _claim(self, actor: UUID, job_id: UUID) -> tuple[UUID, Source] | None:
        with self._session(actor) as db:
            # Cooperating callers serialize per actor, across notebooks/processes.
            # Actor-private SELECT cannot establish notebook-wide cross-actor quotas.
            db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:actor,0))"),
                       {"actor": str(actor)})
            running = db.execute(text(
                "SELECT count(*) FROM public.ingestion_jobs WHERE actor_user_id=:actor "
                "AND status='running' AND lease_expires_at>clock_timestamp()"
            ), {"actor": actor}).scalar_one()
            if running >= 2:
                return None
            token = claim_job(db, job_id, lease_seconds=self.lease_seconds)
            if token is None:
                return None
            row = db.execute(select(IngestionJob, DocumentVersion).join(
                DocumentVersion, IngestionJob.document_version_id == DocumentVersion.id,
            ).where(IngestionJob.id == job_id, IngestionJob.actor_user_id == actor)).one_or_none()
            if row is None:
                raise LeaseLost()
            job, version = row
            scope = ObjectScope(job.workspace_id, job.notebook_id, version.document_id, version.id)
            if version.storage_key != scope.key:
                raise LeaseLost()
            source = Source(scope, version.sha256, version.byte_count, version.media_type,
                            job.connection_id is not None or job.index_profile_id is not None)
            return token, source  # context exits and commits BEFORE any I/O

    def _pulse(self, actor: UUID, job: UUID, token: UUID) -> None:
        with self._session(actor) as db:
            if not heartbeat_job(db, job, token, lease_seconds=self.lease_seconds):
                raise LeaseLost()

    def _finish(self, actor: UUID, job: UUID, token: UUID,
                status: Literal["failed", "cancelled"], code: str) -> bool:
        with self._session(actor) as db:
            if not lock_job_for_publish(db, job, token):
                raise LeaseLost()
            if not finish_job(db, job, token, status=status, error_code=code):
                raise LeaseLost()
            return True

    def _publish(self, actor: UUID, job: UUID, token: UUID,
                 source: Source, parts: tuple[TextChunk, ...]) -> None:
        with self._session(actor) as db:
            if not lock_job_for_publish(db, job, token):
                raise LeaseLost()
            # Retry output IDs and order are deterministic. Never commit partial chunks.
            for ordinal, part in enumerate(parts):
                db.add(Chunk(
                    id=uuid5(source.scope.version_id, str(ordinal)),
                    document_version_id=source.scope.version_id,
                    document_id=source.scope.document_id, notebook_id=source.scope.notebook_id,
                    workspace_id=source.scope.workspace_id, ordinal=ordinal, text=part.text,
                    sha256=part.sha256, page=None, start_offset=part.start, end_offset=part.end,
                ))
            db.flush()
            if not finish_job(db, job, token, status="succeeded"):
                # Exception leaves context -> rollback ALL chunk writes.
                raise LeaseLost()

    async def run_one(self, *, actor_id: UUID, job_id: UUID) -> str:
        """Trusted actor-bound seam; never registers a route or discovers other actors."""
        try:
            claimed = await asyncio.to_thread(self._claim, actor_id, job_id)
        except LeaseLost:
            return "not_claimed"
        if claimed is None:
            return "not_claimed"
        token, source = claimed

        async def authorize(actor: UUID, scope: ObjectScope, action: Literal["read", "write"]):
            if actor != actor_id or scope != source.scope or action != "read":
                raise StorageError("storage_forbidden")
            await asyncio.to_thread(self._pulse, actor_id, job_id, token)

        async def execute():
            if source.embedding:
                raise TextRejected("unsupported_embedding")
            if source.media_type not in ("text/plain", "text/markdown"):
                raise TextRejected("unsupported_media")
            await authorize(actor_id, source.scope, "read")
            data = await self.store_factory(authorize).read(actor_id, source.scope)
            await authorize(actor_id, source.scope, "read")
            if len(data) != source.byte_count or sha256(data).hexdigest() != source.sha256:
                raise TextRejected("original_integrity_failed")
            value = await asyncio.to_thread(decode_text, data)
            parts = await asyncio.to_thread(chunks, value)
            await authorize(actor_id, source.scope, "read")
            # Synchronous short transaction is the commit/cancellation linearization boundary.
            self._publish(actor_id, job_id, token, source, parts)

        async def watch():
            while True:
                await asyncio.sleep(self.heartbeat_seconds)
                await asyncio.to_thread(self._pulse, actor_id, job_id, token)

        work, watcher = asyncio.create_task(execute()), asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait((work, watcher), return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                watcher.result()
            await work
            return "succeeded"
        except LeaseLost:
            return "lease_lost"
        except (TextRejected, StorageError) as exc:
            try:
                self._finish(actor_id, job_id, token, "failed", exc.code)
                return "failed"
            except LeaseLost:
                return "lease_lost"
        except asyncio.CancelledError:
            with suppress(LeaseLost):
                self._finish(actor_id, job_id, token, "cancelled", "execution_cancelled")
            raise
        finally:
            for task in (work, watcher):
                task.cancel()
            await asyncio.gather(work, watcher, return_exceptions=True)
