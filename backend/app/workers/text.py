"""Explicit trusted actor/job invocation, NOT an unattended dispatcher or HTTP API.

The caller owns identity issuance. IDs must come from verified durable server
state; never let a browser choose an actor. Ordinary SQL uses notebook_app only.
"""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from hashlib import sha256
from inspect import isasyncgenfunction, iscoroutine, iscoroutinefunction
from math import isfinite
from threading import Lock
from time import monotonic
from typing import Any, Literal, Protocol
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
from app.documents.transactions import QuotaUnavailable, retry_transaction
from app.ingestion.text import TextChunk, TextRejected, chunks, decode_text


class LeaseLost(RuntimeError):
    pass


class ObservationUnavailable(RuntimeError):
    """Fixed observer fault; never a retryable database or lease failure."""


class _ObserverFault(RuntimeError):
    """Private control signal, caught before publishing an observer failure."""


TextPhase = Literal["ingestion_claim", "pulse", "publish", "finish"]


@dataclass(frozen=True)
class TextProgress:
    phase: TextPhase
    started_at: float
    committed_at: float | None


def _notify_observer(observer: Callable[[TextProgress], None], progress: TextProgress) -> None:
    try:
        result = observer(progress)
        if result is not None:
            if iscoroutine(result):
                result.close()
            raise _ObserverFault()
    except (Exception, asyncio.CancelledError) as error:
        raise _ObserverFault() from error


class OriginalStore(Protocol):
    async def read(self, actor_id: UUID, scope: ObjectScope) -> bytes: ...


@dataclass(frozen=True)
class Source:
    scope: ObjectScope
    sha256: str
    byte_count: int
    media_type: str
    embedding: bool


class OperationRunner(Protocol):
    async def __call__(self, operation: Callable, *args) -> Any: ...


class TextWorker:
    def __init__(
        self,
        engine: Engine,
        *,
        storage: StorageConfig | None = None,
        store_factory: Callable[[Authorize], OriginalStore] | None = None,
        lease_seconds: int = 30,
        heartbeat_seconds: float = 5,
        operation_runner: OperationRunner | None = None,
        progress_observer: Callable[[TextProgress], None] | None = None,
    ):
        if not 1 <= lease_seconds <= 300 or not 0 < heartbeat_seconds < lease_seconds / 2:
            raise ValueError("invalid bounded heartbeat/lease")
        if progress_observer is not None and (
            operation_runner is None
            or not callable(progress_observer)
            or iscoroutinefunction(progress_observer)
            or iscoroutinefunction(progress_observer.__call__)
            or isasyncgenfunction(progress_observer)
            or isasyncgenfunction(progress_observer.__call__)
        ):
            raise ValueError("invalid_progress_observer")
        self._progress_observer = progress_observer
        self._progress_lock = Lock()
        self._observation_failed = False
        self._last_progress: TextProgress | None = None
        self._terminal_progress: TextProgress | None = None
        self._terminal_committed = False
        self._last_observed_at: float | None = None
        self.engine = engine
        # Optional delegation seam: serialize actual outstanding thread work even
        # after waiter cancellation. Existing verified-Identity path is unchanged.
        self.operation_runner = operation_runner
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        # No synthetic store is constructed by default. Unconfigured means fail closed.
        self.store_factory = store_factory or (
            lambda auth: SupabaseStorage(storage, authorize=auth)
        )

    @property
    def observation_failed(self) -> bool:
        with self._progress_lock:
            return self._observation_failed

    @property
    def last_progress(self) -> TextProgress | None:
        with self._progress_lock:
            return self._last_progress

    @property
    def terminal_progress(self) -> TextProgress | None:
        with self._progress_lock:
            return self._terminal_progress

    @property
    def terminal_committed(self) -> bool:
        """Actual terminal context exit, independent of the availability of its clock reading."""
        with self._progress_lock:
            return self._terminal_committed

    def _observe(self, progress: TextProgress, *, committed: bool) -> None:
        with self._progress_lock:
            event_at = progress.committed_at if committed else progress.started_at
            if (
                self._observation_failed
                or type(progress.started_at) not in (int, float)
                or not isfinite(progress.started_at)
                or event_at is None
                or type(event_at) not in (int, float)
                or not isfinite(event_at)
                or event_at < progress.started_at
                or (self._last_observed_at is not None and event_at < self._last_observed_at)
            ):
                self._observation_failed = True
                # Invalid times never enter metadata/callbacks. A terminal exit
                # already happened: its separate truth bit must survive this fault.
                if committed and progress.phase in ("publish", "finish"):
                    return
                raise ObservationUnavailable("observation_unavailable") from None
            self._last_observed_at = event_at
            # A late watchdog still executes its real authorization transaction,
            # but cannot erase terminal truth while publication returns/joins.
            if (
                progress.phase == "pulse"
                and self._last_progress is not None
                and self._last_progress.phase in ("publish", "finish")
                and self._last_progress.committed_at is not None
            ):
                return
            self._last_progress = progress
            if progress.phase == "ingestion_claim" and not committed:
                self._terminal_progress = None
                self._terminal_committed = False
            elif progress.phase in ("publish", "finish"):
                terminal = self._terminal_progress
                if terminal is None:
                    self._terminal_progress = progress
                elif progress.committed_at is not None:
                    # Retry entry cannot slide the cycle's original deadline or
                    # erase a known commit. Caller/last_progress remain per-attempt.
                    self._terminal_progress = (
                        progress
                        if progress.started_at == terminal.started_at
                        else TextProgress(
                            progress.phase, terminal.started_at, progress.committed_at
                        )
                    )
        observer = self._progress_observer
        if observer is None:
            return
        failed = False
        try:
            _notify_observer(observer, progress)
        except _ObserverFault:
            failed = True
        if failed:
            # Leave the handler before raising: even __context__ must not retain
            # the observer's private error. Logging is not part of this boundary.
            with self._progress_lock:
                self._observation_failed = True
            if not committed or progress.phase not in ("publish", "finish"):
                raise ObservationUnavailable("observation_unavailable") from None

    def _start_progress(self, phase: TextPhase) -> float | None:
        if self.operation_runner is None:
            return None
        started_at = monotonic()
        self._observe(TextProgress(phase, started_at, None), committed=False)
        return started_at

    def _commit_progress(self, phase: TextPhase, started_at: float | None) -> None:
        if started_at is not None:
            if phase in ("publish", "finish"):
                with self._progress_lock:
                    self._terminal_committed = True
            self._observe(TextProgress(phase, started_at, monotonic()), committed=True)

    @contextmanager
    def _session(self, actor: UUID) -> Iterator[Session]:
        with Session(self.engine) as session, session.begin():
            safe = session.execute(
                text(
                    "SELECT current_user='notebook_app' AND session_user='notebook_app' "
                    "AND NOT c.rolsuper AND NOT c.rolbypassrls "
                    "AND NOT s.rolsuper AND NOT s.rolbypassrls "
                    "FROM pg_roles c JOIN pg_roles s ON s.rolname=session_user "
                    "WHERE c.rolname=current_user"
                )
            ).scalar_one()
            if safe is not True:
                raise RuntimeError("unsafe_database_role")
            session.execute(text("SET LOCAL statement_timeout='3s'"))
            session.execute(text("SET LOCAL lock_timeout='1s'"))
            session.execute(
                text("SELECT set_config('request.jwt.claim.sub',:actor,true)"),
                {"actor": str(actor)},
            )
            if session.get(Admission, actor) is None:
                raise LeaseLost("admission_revoked")
            yield session

    @retry_transaction
    def _claim(self, actor: UUID, job_id: UUID) -> tuple[UUID, Source] | None:
        started_at = self._start_progress("ingestion_claim")
        with self._session(actor) as db:
            # Cooperating callers serialize per actor, across notebooks/processes.
            # Actor-private SELECT cannot establish notebook-wide cross-actor quotas.
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:actor,0))"),
                {"actor": str(actor)},
            )
            running = db.execute(
                text(
                    "SELECT count(*) FROM public.ingestion_jobs WHERE actor_user_id=:actor "
                    "AND status='running' AND lease_expires_at>clock_timestamp()"
                ),
                {"actor": actor},
            ).scalar_one()
            if running >= 2:
                return None
            token = claim_job(db, job_id, lease_seconds=self.lease_seconds)
            if token is None:
                return None
            row = db.execute(
                select(IngestionJob, DocumentVersion)
                .join(
                    DocumentVersion,
                    IngestionJob.document_version_id == DocumentVersion.id,
                )
                .where(IngestionJob.id == job_id, IngestionJob.actor_user_id == actor)
            ).one_or_none()
            if row is None:
                raise LeaseLost()
            job, version = row
            scope = ObjectScope(job.workspace_id, job.notebook_id, version.document_id, version.id)
            if version.storage_key != scope.key:
                raise LeaseLost()
            source = Source(
                scope,
                version.sha256,
                version.byte_count,
                version.media_type,
                job.connection_id is not None or job.index_profile_id is not None,
            )
        self._commit_progress("ingestion_claim", started_at)
        return token, source  # context exits and commits BEFORE observation or I/O

    @retry_transaction
    def _pulse(self, actor: UUID, job: UUID, token: UUID) -> None:
        started_at = self._start_progress("pulse")
        with self._session(actor) as db:
            if not heartbeat_job(db, job, token, lease_seconds=self.lease_seconds):
                raise LeaseLost()
        self._commit_progress("pulse", started_at)

    @retry_transaction
    def _finish(
        self, actor: UUID, job: UUID, token: UUID, status: Literal["failed", "cancelled"], code: str
    ) -> bool:
        started_at = self._start_progress("finish")
        with self._session(actor) as db:
            if not lock_job_for_publish(db, job, token):
                raise LeaseLost()
            if not finish_job(db, job, token, status=status, error_code=code):
                raise LeaseLost()
        self._commit_progress("finish", started_at)
        return True

    @retry_transaction
    def _publish(
        self, actor: UUID, job: UUID, token: UUID, source: Source, parts: tuple[TextChunk, ...]
    ) -> None:
        started_at = self._start_progress("publish")
        with self._session(actor) as db:
            if not lock_job_for_publish(db, job, token):
                raise LeaseLost()
            # Retry output IDs and order are deterministic. Never commit partial chunks.
            for ordinal, part in enumerate(parts):
                db.add(
                    Chunk(
                        id=uuid5(source.scope.version_id, str(ordinal)),
                        document_version_id=source.scope.version_id,
                        document_id=source.scope.document_id,
                        notebook_id=source.scope.notebook_id,
                        workspace_id=source.scope.workspace_id,
                        ordinal=ordinal,
                        text=part.text,
                        sha256=part.sha256,
                        page=None,
                        start_offset=part.start,
                        end_offset=part.end,
                    )
                )
            db.flush()
            if not finish_job(db, job, token, status="succeeded"):
                # Exception leaves context -> rollback ALL chunk writes.
                raise LeaseLost()
        self._commit_progress("publish", started_at)

    async def _run_operation(self, operation: Callable, *args):
        if self.operation_runner is not None:
            return await self.operation_runner(operation, *args)
        return await asyncio.to_thread(operation, *args)

    async def run_one(self, *, actor_id: UUID, job_id: UUID) -> str:
        """Trusted actor-bound seam; never registers a route or discovers other actors."""
        if self.observation_failed:
            raise ObservationUnavailable("observation_unavailable")
        try:
            claimed = await self._run_operation(self._claim, actor_id, job_id)
        except (LeaseLost, QuotaUnavailable):
            return "not_claimed"
        if claimed is None:
            return "not_claimed"
        token, source = claimed
        publishing = False

        async def authorize(actor: UUID, scope: ObjectScope, action: Literal["read", "write"]):
            if actor != actor_id or scope != source.scope or action != "read":
                raise StorageError("storage_forbidden")
            await self._run_operation(self._pulse, actor_id, job_id, token)

        async def execute():
            nonlocal publishing
            if source.embedding:
                raise TextRejected("unsupported_embedding")
            if source.media_type not in ("text/plain", "text/markdown"):
                raise TextRejected("unsupported_media")
            await authorize(actor_id, source.scope, "read")
            data = await self.store_factory(authorize).read(actor_id, source.scope)
            await authorize(actor_id, source.scope, "read")
            if len(data) != source.byte_count or sha256(data).hexdigest() != source.sha256:
                raise TextRejected("original_integrity_failed")
            value = await self._run_operation(decode_text, data)
            parts = await self._run_operation(chunks, value)
            await authorize(actor_id, source.scope, "read")
            publishing = True
            if self.operation_runner is None:
                # Preserve the original verified-Identity linearization boundary.
                self._publish(actor_id, job_id, token, source, parts)
            else:
                # Cancellation leaves a possibly committed outcome for durable
                # reconciliation; it must not launch a conflicting finish/ack.
                await self._run_operation(self._publish, actor_id, job_id, token, source, parts)

        async def watch():
            while True:
                await asyncio.sleep(self.heartbeat_seconds)
                await self._run_operation(self._pulse, actor_id, job_id, token)

        work, watcher = asyncio.create_task(execute()), asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait((work, watcher), return_when=asyncio.FIRST_COMPLETED)
            # A heartbeat racing AFTER the successful terminal commit sees no
            # running lease. Completed work wins that reporting race; failures
            # still propagate from work, and a lost watcher cancels unfinished I/O.
            if work in done or (self.operation_runner is not None and publishing):
                await work
                return "succeeded"
            watcher.result()
            raise LeaseLost()
        except QuotaUnavailable:
            # A claim already committed. Keep its token/attempt/lease until a
            # future fenced transition or real expiry; do not repeat source I/O.
            return "yielded"
        except LeaseLost:
            return "lease_lost"
        except (TextRejected, StorageError) as exc:
            try:
                if self.operation_runner is None:
                    self._finish(actor_id, job_id, token, "failed", exc.code)
                else:
                    await self._run_operation(
                        self._finish, actor_id, job_id, token, "failed", exc.code
                    )
                return "failed"
            except QuotaUnavailable:
                return "yielded"
            except LeaseLost:
                return "lease_lost"
        except asyncio.CancelledError:
            if self.operation_runner is None:
                with suppress(LeaseLost, QuotaUnavailable):
                    self._finish(actor_id, job_id, token, "cancelled", "execution_cancelled")
            # Delegation cancellation is not rollback evidence. Leave the ingestion
            # lease/terminal outcome intact; no competing cancellation transition.
            raise
        finally:
            for task in (work, watcher):
                task.cancel()
            await asyncio.gather(work, watcher, return_exceptions=True)
