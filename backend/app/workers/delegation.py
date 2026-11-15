"""Uninstalled exact-job consumer. No Identity issuance, daemon or runtime provisioning.

Factories are trusted injection: finite pool/connect budgets and startup statement/
lock timeouts (3s/1s) must actually be configured, including before role checks.
Grants are opaque capabilities until freshly validated by PostgreSQL.
"""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
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

from app.workers.text import TextProgress, TextWorker


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


SchedulerPhase = Literal["claim", "validate", "renew", "acknowledge"]


@dataclass(frozen=True)
class SchedulerProgress:
    phase: SchedulerPhase
    started_at: float
    committed_at: float | None


@dataclass(frozen=True)
class ConsumerPolicy:
    delivery_seconds: int = 60
    renewal_seconds: float = 15
    scheduler_timeout: float = 10
    delivery_timeout: float = 120
    terminal_settlement_timeout: float = 10

    def __post_init__(self):
        if type(self.delivery_seconds) is not int or not 1 <= self.delivery_seconds <= 300:
            raise ValueError("invalid_consumer_policy")
        for value, maximum in (
            (self.renewal_seconds, self.delivery_seconds / 2),
            (self.scheduler_timeout, 10),
            (self.delivery_timeout, 120),
            (self.terminal_settlement_timeout, 10),
        ):
            if type(value) not in (int, float) or not isfinite(value) or not 0 < value <= maximum:
                raise ValueError("invalid_consumer_policy")
        if (
            self.renewal_seconds >= self.delivery_seconds / 2
            or self.scheduler_timeout >= self.delivery_seconds
        ):
            raise ValueError("invalid_consumer_policy")


_CONSUMER_POLICY = ConsumerPolicy()


class DelegationRepository:
    def __init__(self, factory: SchedulerFactory, runner: BoundedThreadRunner):
        if not isinstance(factory, SchedulerFactory) or not isinstance(runner, BoundedThreadRunner):
            raise TypeError("bounded_repository_required")
        self.factory, self.runner = factory, runner
        self._progress_lock = Lock()
        self._last_progress: SchedulerProgress | None = None

    @property
    def last_progress(self) -> SchedulerProgress | None:
        with self._progress_lock:
            return self._last_progress

    def _transaction(self, query, params, *, phase: SchedulerPhase, row=False):
        started_at = monotonic()
        with self._progress_lock:
            self._last_progress = SchedulerProgress(phase, started_at, None)
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
            value = result.mappings().one_or_none() if row else result.scalar_one()
        with self._progress_lock:
            self._last_progress = SchedulerProgress(phase, started_at, monotonic())
        return value

    async def _call(self, query, params, *, phase: SchedulerPhase, row=False) -> Any:
        try:
            return await self.runner(
                partial(self._transaction, query, params, phase=phase, row=row)
            )
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
            phase="claim",
        )
        return JobExecutionGrant(**row) if row else None

    async def validate(self, grant: JobExecutionGrant) -> str | None:
        return await self._call(
            text(
                "SELECT public.validate_text_job(:job_id,:actor_user_id,:workspace_id,:notebook_id,"
                ":document_version_id,:purpose,:delivery_token)"
            ),
            self._parameters(grant),
            phase="validate",
        )

    async def renew(self, grant: JobExecutionGrant, *, seconds: int = 60) -> bool:
        params = self._parameters(grant) | {"seconds": self._duration(seconds)}
        return await self._call(
            text(
                "SELECT public.renew_text_job(:job_id,:actor_user_id,:workspace_id,:notebook_id,"
                ":document_version_id,:purpose,:delivery_token,:seconds)"
            ),
            params,
            phase="renew",
        )

    async def acknowledge(self, grant: JobExecutionGrant) -> bool:
        return await self._call(
            text(
                "SELECT public.ack_text_job(:job_id,:actor_user_id,:workspace_id,:notebook_id,"
                ":document_version_id,:purpose,:delivery_token)"
            ),
            self._parameters(grant),
            phase="acknowledge",
        )


ConsumerPhase = Literal[
    "idle",
    "claiming",
    "validating",
    "executing",
    "reconciling",
    "stopping_renewal",
    "acknowledging",
    "settled",
    "uncertain",
]
Acknowledgment = Literal["not_attempted", "confirmed", "unconfirmed"]


@dataclass(frozen=True)
class ConsumerSnapshot:
    generation: int
    cycle: int
    phase: ConsumerPhase
    phase_started_at: float | None
    delivery_started_at: float | None
    delivery_deadline: float | None
    delivery_valid_until: float | None
    draining: bool
    cancelled: bool
    lease_failed: bool
    outcome_unknown: bool
    terminal_observed: bool
    acknowledgment: Acknowledgment
    renewal_active: bool
    runner: RunnerSnapshot
    scheduler_progress: SchedulerProgress | None
    text_progress: TextProgress | None
    terminal_progress: TextProgress | None


class _DeliveryDeadline(RuntimeError):
    """Local detection only, never rollback or physical thread termination."""


class ExactTextConsumer:
    """One private grant and renewal loop, one shared actual executor slot.

    Not a poller or supervisor. Cancellation joins coroutine children, not an
    unresolved driver thread. That thread remains fenced by runner admission.
    """

    def __init__(
        self,
        repository: DelegationRepository,
        worker: TextWorker,
        *,
        pool_timeout: float,
        connect_timeout: float,
        policy: ConsumerPolicy = _CONSUMER_POLICY,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        SchedulerFactory(repository.factory.open, pool_timeout, connect_timeout)
        if worker.operation_runner is not repository.runner:
            raise ValueError("shared_bounded_runner_required")
        if type(policy) is not ConsumerPolicy or not callable(clock) or not callable(sleep):
            raise ValueError("invalid_consumer_policy")
        self.repository, self.worker = repository, worker
        self.policy, self._clock, self._sleep = policy, clock, sleep
        self._busy = self._draining = False
        self._cycle = 0
        self._phase: ConsumerPhase = "idle"
        self._phase_started_at: float | None = None
        self._delivery_started_at: float | None = None
        self._delivery_deadline: float | None = None
        self._valid_until: float | None = None
        self._last_now: float | None = None
        self._cancelled = self._lease_failed = self._outcome_unknown = False
        self._terminal_observed = False
        self._acknowledgment: Acknowledgment = "not_attempted"
        self._renewal: asyncio.Task | None = None
        self._worker_started = False
        self._terminal_marker: TextProgress | None = None
        self._text_marker: TextProgress | None = None

    def request_stop(self) -> None:
        self._draining = True

    def _terminal(self) -> TextProgress | None:
        progress = self.worker.terminal_progress
        if not self._worker_started or progress is self._terminal_marker:
            return None
        return progress

    def snapshot(self) -> ConsumerSnapshot:
        terminal = self._terminal()
        progress = self.worker.last_progress
        runner = self.repository.runner.snapshot()
        return ConsumerSnapshot(
            runner.generation,
            self._cycle,
            self._phase,
            self._phase_started_at,
            self._delivery_started_at,
            self._delivery_deadline,
            self._valid_until,
            self._draining,
            self._cancelled,
            self._lease_failed,
            self._outcome_unknown,
            self._terminal_observed or (terminal is not None and self.worker.terminal_committed),
            self._acknowledgment,
            self._renewal is not None and not self._renewal.done(),
            runner,
            self.repository.last_progress,
            progress if self._worker_started and progress is not self._text_marker else None,
            terminal,
        )

    def _now(self) -> float:
        now = self._clock()
        if (
            type(now) not in (int, float)
            or not isfinite(now)
            or (self._last_now is not None and now < self._last_now)
        ):
            self._lease_failed = True
            raise DelegationUnavailable("delegation_unavailable")
        self._last_now = now
        return now

    def _phase_entry(self, phase: ConsumerPhase) -> None:
        self._phase, self._phase_started_at = phase, self._now()

    def _check_progress(self, progress: SchedulerProgress | TextProgress | None) -> None:
        if progress is None:
            return
        now = self._now()
        times = (progress.started_at, progress.committed_at)
        if (
            any(
                type(t) not in (int, float) or not isfinite(t) or t > now
                for t in times
                if t is not None
            )
            or (progress.committed_at is not None and progress.committed_at < progress.started_at)
            or (
                self._delivery_started_at is not None
                and progress.started_at < self._delivery_started_at
            )
        ):
            self._lease_failed = True
            raise DelegationUnavailable("delegation_unavailable")

    def _deadline(self, limit: float) -> float:
        deadlines = [limit]
        if self._delivery_deadline is not None:
            deadlines.append(self._delivery_deadline)
        if self._valid_until is not None:
            deadlines.append(self._valid_until)
        return min(deadlines)

    async def _sleep_until(self, deadline: float) -> None:
        while (remaining := deadline - self._now()) > 0:
            await self._sleep(remaining)

    @staticmethod
    async def _join(*tasks: asyncio.Task | None) -> None:
        owned = [task for task in tasks if task is not None]
        for task in owned:
            if not task.done():
                task.cancel()
        if owned:
            joined = asyncio.gather(*owned, return_exceptions=True)
            # Repeated cancellation cannot drop the last child ownership handle,
            # but a cancellation arriving during cleanup must still propagate.
            cancelled = False
            while not joined.done():
                try:
                    await asyncio.shield(joined)
                except asyncio.CancelledError:
                    cancelled = True
            joined.result()
            if cancelled:
                raise asyncio.CancelledError

    async def _bounded(self, task: asyncio.Task, deadline: float):
        timer = asyncio.create_task(self._sleep_until(deadline))
        try:
            await asyncio.wait((task, timer), return_when=asyncio.FIRST_COMPLETED)
            if timer.done():
                timer.result()
            if task.done():
                result = task.result()
                if self._now() < deadline:
                    return result
            self._lease_failed = True
            raise _DeliveryDeadline()
        finally:
            await self._join(timer, task)

    async def _scheduler(self, operation: Callable[[], Coroutine[Any, Any, Any]]):
        started_at = self._now()
        if started_at >= self._deadline(started_at + self.policy.scheduler_timeout):
            self._lease_failed = True
            raise _DeliveryDeadline()
        previous = self.repository.last_progress
        task = asyncio.create_task(operation())
        try:
            result = await self._bounded(
                task, self._deadline(started_at + self.policy.scheduler_timeout)
            )
            progress = self.repository.last_progress
            if progress is not previous:
                self._check_progress(progress)
            return result, started_at
        finally:
            if task.cancelled() or (task.done() and task.exception() is not None):
                self._outcome_unknown = True

    def _accept_lease(self, started_at: float) -> None:
        valid_until = started_at + self.policy.delivery_seconds
        if self._now() >= self._deadline(valid_until):
            self._lease_failed = True
            raise _DeliveryDeadline()
        self._valid_until = valid_until

    async def _renew(self, grant: JobExecutionGrant, stop: asyncio.Event) -> None:
        while not stop.is_set():
            wake = asyncio.create_task(
                self._sleep_until(self._deadline(self._now() + self.policy.renewal_seconds))
            )
            stopped = asyncio.create_task(stop.wait())
            try:
                await asyncio.wait((wake, stopped), return_when=asyncio.FIRST_COMPLETED)
                if stop.is_set():
                    return
                wake.result()
            finally:
                await self._join(wake, stopped)
            renewed, started_at = await self._scheduler(
                lambda: self.repository.renew(grant, seconds=self.policy.delivery_seconds)
            )
            if renewed is not True:
                self._lease_failed = True
                return
            self._accept_lease(started_at)

    async def _stop_renewal(self, stop: asyncio.Event) -> None:
        self._phase_entry("stopping_renewal")
        stop.set()
        if self._renewal is not None:
            # Do not cancel an in-flight renewal result waiter on normal completion.
            # Its original bounded scheduler wait, not a new grace period, settles it.
            await asyncio.shield(self._renewal)

    async def _execute(self, grant: JobExecutionGrant) -> str:
        renewal, delivery_deadline = self._renewal, self._delivery_deadline
        if renewal is None or delivery_deadline is None:
            raise DelegationUnavailable("delegation_unavailable")
        self._phase_entry("executing")
        self._terminal_marker = self.worker.terminal_progress
        self._text_marker = self.worker.last_progress
        self._worker_started = True
        work = asyncio.create_task(
            self.worker.run_one(actor_id=grant.actor_user_id, job_id=grant.job_id)
        )
        whole = asyncio.create_task(self._sleep_until(delivery_deadline))
        try:
            await asyncio.wait((work, renewal, whole), return_when=asyncio.FIRST_COMPLETED)
            if self._now() >= delivery_deadline:
                self._lease_failed = True
            if whole.done():
                whole.result()
                self._lease_failed = True
            if renewal.done():
                self._lease_failed = True
                try:
                    renewal.result()
                except (DelegationUnavailable, _DeliveryDeadline):
                    # Failure is already sanitized and scheduler uncertainty retained.
                    pass
            if self._lease_failed:
                terminal = self._terminal()
                self._check_progress(terminal)
                if terminal is not None:
                    deadline = min(
                        terminal.started_at + self.policy.terminal_settlement_timeout,
                        delivery_deadline,
                    )
                    if self._now() < deadline:
                        try:
                            await self._bounded(work, deadline)
                        except _DeliveryDeadline:
                            pass
                return "lease_lost"
            result = work.result()
            terminal = self._terminal()
            self._check_progress(terminal)
            if result in ("succeeded", "failed"):
                self._terminal_observed = True
                if (
                    terminal is not None
                    and self._now() >= terminal.started_at + self.policy.terminal_settlement_timeout
                ):
                    self._lease_failed = True
            return result
        finally:
            await self._join(work, whole)
            # A normal worker result can leave the independently owned renewal
            # in the runner. Its pending state is NOT an unknown worker outcome.
            if (work.cancelled() and self.repository.runner.snapshot().pending) or (
                not work.cancelled() and work.done() and work.exception() is not None
            ):
                self._outcome_unknown = True

    async def run_one(self) -> str:
        if self._busy or self.repository.runner.snapshot().pending:
            return "busy"
        self._busy = True
        stop = asyncio.Event()
        try:
            if self.worker.observation_failed or self._draining:
                raise DelegationUnavailable("delegation_unavailable")
            self._cycle += 1
            self._cancelled = self._lease_failed = self._outcome_unknown = False
            self._terminal_observed = self._worker_started = False
            self._acknowledgment = "not_attempted"
            self._valid_until = None
            self._renewal = None
            self._delivery_started_at = self._now()
            self._delivery_deadline = self._delivery_started_at + self.policy.delivery_timeout
            self._phase_entry("claiming")
            grant, started_at = await self._scheduler(
                lambda: self.repository.claim(seconds=self.policy.delivery_seconds)
            )
            if grant is None:
                self._phase_entry("idle")
                return "idle"
            self._accept_lease(started_at)
            self._renewal = asyncio.create_task(self._renew(grant, stop))
            self._phase_entry("validating")
            state, _ = await self._scheduler(lambda: self.repository.validate(grant))
            if state in ("succeeded", "failed", "cancelled"):
                self._phase_entry("reconciling")
                self._terminal_observed = True
                result = state
            elif state == "executable":
                if self._lease_failed or self._renewal.done():
                    return "lease_lost"
                result = await self._execute(grant)
            else:
                result = "not_claimed"
            await self._stop_renewal(stop)
            if self.repository.runner.snapshot().pending:
                self._lease_failed = self._outcome_unknown = True
            if self.worker.observation_failed:
                self._lease_failed = True
                raise DelegationUnavailable("delegation_unavailable")
            if self._lease_failed or self._outcome_unknown:
                return "lease_lost"
            if result in ("succeeded", "failed", "cancelled"):
                self._phase_entry("acknowledging")
                self._acknowledgment = "unconfirmed"
                acknowledged, _ = await self._scheduler(lambda: self.repository.acknowledge(grant))
                if acknowledged is True:
                    self._acknowledgment = "confirmed"
                    return result
                self._lease_failed = True
                return "lease_lost"
            if result not in ("not_claimed", "yielded", "lease_lost"):
                raise DelegationUnavailable("invalid_worker_result")
            return result
        except _DeliveryDeadline:
            self._lease_failed = True
            return "lease_lost"
        except asyncio.CancelledError:
            self._cancelled = True
            raise
        except Exception:  # noqa: BLE001 - sanitize arbitrary injected driver/Storage failures
            raise DelegationUnavailable("delegation_unavailable") from None
        finally:
            stop.set()
            try:
                await self._join(self._renewal)
            except asyncio.CancelledError:
                self._cancelled = True
                raise
            finally:
                if self.repository.runner.snapshot().pending:
                    self._outcome_unknown = True
                if self.worker.observation_failed:
                    self._lease_failed = True
                self._busy = False
                try:
                    self._phase_started_at = self._now()
                except DelegationUnavailable:
                    # The failed clock is already latched. Never invent a phase
                    # timestamp or replace an external cancellation during cleanup.
                    self._phase_started_at = None
                if self._cancelled or self._outcome_unknown or self._lease_failed:
                    self._phase = "uncertain"
                elif self._phase != "idle":
                    self._phase = "settled"
