"""Uninstalled exact-job consumer. No Identity issuance, daemon or runtime provisioning.

Factories are trusted injection: finite pool/connect budgets and startup statement/
lock timeouts (3s/1s) must actually be configured, including before role checks.
Grants are opaque capabilities until freshly validated by PostgreSQL.
"""

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from functools import partial
from itertools import count
from math import isfinite
from threading import Lock
from time import monotonic
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.workers.text import TextWorker


class DelegationUnavailable(RuntimeError):
    """Fixed messages only; never propagate driver, Storage or credential details."""


@dataclass(frozen=True)
class RunnerSnapshot:
    generation: int
    sequence: int
    closed: bool
    pending: bool
    phase: Literal["idle", "submitted", "running", "completed", "closed"]
    submitted_at: float | None
    started_at: float | None
    completed_at: float | None


class BoundedThreadRunner:
    """One actual outstanding operation, including after cancellation of its waiter.

    Loop-local lifetime. close() refuses new work and reports unresolved shutdown;
    neither cancellation nor timeout claims that the DB operation rolled back.
    """

    _generations = count(1)

    def __init__(self):
        self._state_lock = Lock()
        self._generation = next(self._generations)
        self._sequence = 0
        self._phase: Literal["idle", "submitted", "running", "completed"] = "idle"
        self._submitted_at: float | None = None
        self._started_at: float | None = None
        self._completed_at: float | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="text-delegation")
        self._lock = asyncio.Lock()
        self._pending: Future | None = None
        self._closed = False

    def snapshot(self) -> RunnerSnapshot:
        with self._state_lock:
            return RunnerSnapshot(
                self._generation,
                self._sequence,
                self._closed,
                self._pending is not None and not self._pending.done(),
                "closed" if self._closed else self._phase,
                self._submitted_at,
                self._started_at,
                self._completed_at,
            )

    def _invoke(self, operation: Callable, args: tuple):
        with self._state_lock:
            self._started_at = monotonic()
            self._phase = "running"
        try:
            return operation(*args)
        finally:
            # Callback return/raise, not coroutine liveness or transaction success.
            # A closed lifetime remains closed even when this finally runs late.
            with self._state_lock:
                self._completed_at = monotonic()
                self._phase = "completed"

    async def _wait(self):
        if self._pending is not None:
            wrapped = asyncio.wrap_future(self._pending)
            # A cancelled waiter must not cancel the underlying operation or leave
            # an unobserved exception when the operation eventually completes.
            wrapped.add_done_callback(lambda f: None if f.cancelled() else f.exception())
            return await asyncio.shield(wrapped)

    async def __call__(self, operation: Callable, *args):
        async with self._lock:
            if self._closed:
                raise DelegationUnavailable("runner_closed")
            if self._pending is not None:
                try:
                    await self._wait()
                except Exception:  # noqa: BLE001, S110 - previous caller owns the opaque result
                    pass
                with self._state_lock:
                    self._pending = None
            if self._closed:
                raise DelegationUnavailable("runner_closed")
            with self._state_lock:
                submitted_at = monotonic()
                self._pending = self._executor.submit(self._invoke, operation, args)
                self._sequence += 1
                self._submitted_at = submitted_at
                self._started_at = self._completed_at = None
                self._phase = "submitted"
            try:
                return await self._wait()
            finally:
                with self._state_lock:
                    if self._pending.done():
                        self._pending = None

    async def close(self, *, timeout: float = 3) -> bool:
        if type(timeout) not in (int, float) or not isfinite(timeout) or not 0 < timeout <= 3:
            raise ValueError("bounded_shutdown_required")
        with self._state_lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=False)
        try:
            await asyncio.wait_for(self._wait(), timeout)
        except TimeoutError:
            return False
        except Exception:  # noqa: BLE001, S110 - quiescence, not operation success
            pass
        return self._pending is None or self._pending.done()


@dataclass(frozen=True)
class JobExecutionGrant:
    job_id: UUID
    actor_user_id: UUID
    workspace_id: UUID
    notebook_id: UUID
    document_version_id: UUID
    purpose: str
    delivery_token: UUID = field(repr=False)


@dataclass(frozen=True)
class SchedulerFactory:
    """Fresh committing READ COMMITTED sessions with enforced startup budgets.

    Values attest trusted engine configuration, not arbitrary factory behavior.
    Connection setup/DNS/OS scheduling are not a promised total wall-clock bound.
    """

    open: Callable[[], AbstractContextManager[Session]]
    pool_timeout: float
    connect_timeout: float

    def __post_init__(self):
        for value, maximum in ((self.pool_timeout, 1), (self.connect_timeout, 3)):
            if type(value) not in (int, float) or not isfinite(value) or not 0 < value <= maximum:
                raise ValueError("bounded_factory_required")
        if not callable(self.open):
            raise TypeError("bounded_factory_required")


class DelegationRepository:
    def __init__(self, factory: SchedulerFactory, runner: BoundedThreadRunner):
        if not isinstance(factory, SchedulerFactory) or not isinstance(runner, BoundedThreadRunner):
            raise TypeError("bounded_repository_required")
        self.factory, self.runner = factory, runner

    def _transaction(self, query, params, *, row=False):
        with self.factory.open() as db:
            safe = db.execute(
                text(
                    "SELECT current_user=session_user AND current_user='notebook_text_scheduler' "
                    "AND NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
            ).scalar_one()
            if safe is not True:
                raise DelegationUnavailable("unsafe_scheduler_role")
            db.execute(text("SET LOCAL statement_timeout='3s'"))
            db.execute(text("SET LOCAL lock_timeout='1s'"))
            if db.execute(text("SHOW transaction_isolation")).scalar_one() != "read committed":
                raise DelegationUnavailable("unsafe_isolation")
            result = db.execute(query, params)
            return result.mappings().one_or_none() if row else result.scalar_one()

    async def _call(self, query, params, *, row=False) -> Any:
        try:
            return await self.runner(partial(self._transaction, query, params, row=row))
        except DelegationUnavailable:
            raise
        except Exception:  # noqa: BLE001 - sanitize arbitrary injected driver/Storage failures
            raise DelegationUnavailable("delegation_unavailable") from None

    @staticmethod
    def _parameters(grant: JobExecutionGrant) -> dict[str, Any]:
        if type(grant) is not JobExecutionGrant or grant.purpose != "text_extract_v1":
            raise DelegationUnavailable("invalid_grant")
        fields = asdict(grant)
        if any(not isinstance(v, UUID) for k, v in fields.items() if k != "purpose"):
            raise DelegationUnavailable("invalid_grant")
        return fields

    @staticmethod
    def _duration(seconds):
        if type(seconds) is not int or not 1 <= seconds <= 300:
            raise ValueError("bounded_lease_required")
        return seconds

    async def claim(self, *, seconds: int = 60) -> JobExecutionGrant | None:
        row = await self._call(
            text("SELECT * FROM public.claim_text_job(:seconds)"),
            {"seconds": self._duration(seconds)},
            row=True,
        )
        return JobExecutionGrant(**row) if row else None

    async def validate(self, grant: JobExecutionGrant) -> str | None:
        return await self._call(
            text(
                "SELECT public.validate_text_job(:job_id,:actor_user_id,:workspace_id,:notebook_id,"
                ":document_version_id,:purpose,:delivery_token)"
            ),
            self._parameters(grant),
        )

    async def renew(self, grant: JobExecutionGrant, *, seconds: int = 60) -> bool:
        params = self._parameters(grant) | {"seconds": self._duration(seconds)}
        return await self._call(
            text(
                "SELECT public.renew_text_job(:job_id,:actor_user_id,:workspace_id,:notebook_id,"
                ":document_version_id,:purpose,:delivery_token,:seconds)"
            ),
            params,
        )

    async def acknowledge(self, grant: JobExecutionGrant) -> bool:
        return await self._call(
            text(
                "SELECT public.ack_text_job(:job_id,:actor_user_id,:workspace_id,:notebook_id,"
                ":document_version_id,:purpose,:delivery_token)"
            ),
            self._parameters(grant),
        )


class ExactTextConsumer:
    """At most one delivery per instance. No autonomous poll/retry/heartbeat loop.

    Execution engine bounds are a trusted injector contract just like the scheduler
    factory. TextWorker independently enforces direct notebook_app and admission.
    Delivery expiry never permits recovery before the ingestion lease expires.
    """

    def __init__(
        self,
        repository: DelegationRepository,
        worker: TextWorker,
        *,
        pool_timeout: float,
        connect_timeout: float,
    ):
        # Reuse the same bounded contract without constructing a connection.
        SchedulerFactory(repository.factory.open, pool_timeout, connect_timeout)
        if worker.operation_runner is not repository.runner:
            raise ValueError("shared_bounded_runner_required")
        self.repository, self.worker = repository, worker
        self._busy = False

    async def run_one(self) -> str:
        if self._busy:
            return "busy"
        # A cancelled consumer may have left a real operation behind. Do not
        # enqueue a claim that could outlive its late observer-fault decision.
        if self.repository.runner.snapshot().pending:
            return "busy"
        self._busy = True
        try:
            if self.worker.observation_failed:
                raise DelegationUnavailable("delegation_unavailable")
            grant = await self.repository.claim()
            if grant is None:
                return "idle"
            state = await self.repository.validate(grant)
            if state in ("succeeded", "failed", "cancelled"):
                # Publication may have committed before the previous process died.
                return state if await self.repository.acknowledge(grant) else "lease_lost"
            if state != "executable":
                return "not_claimed"
            result = await self.worker.run_one(actor_id=grant.actor_user_id, job_id=grant.job_id)
            if self.worker.observation_failed:
                raise DelegationUnavailable("delegation_unavailable")
            if result in ("succeeded", "failed"):
                return result if await self.repository.acknowledge(grant) else "lease_lost"
            if result not in ("not_claimed", "yielded", "lease_lost"):
                raise DelegationUnavailable("invalid_worker_result")
            return result
        except asyncio.CancelledError:
            # No ack, finish, retry or assertion that a pending commit rolled back.
            raise
        except Exception:  # noqa: BLE001 - sanitize arbitrary injected driver/Storage failures
            raise DelegationUnavailable("delegation_unavailable") from None
        finally:
            self._busy = False
