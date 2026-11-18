"""Uninstalled, loop-local supervision of one exact consumer and its existing runner.

This predicate is not the HTTP final-response recheck or runtime assembly. No
resource disposal, database authority, replacement executor or process kill lives here.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from math import isfinite
from random import uniform
from time import monotonic
from typing import Literal

from app.workers.delegation import ConsumerSnapshot, DelegationUnavailable, ExactTextConsumer
from app.workers.text import TextWorkSnapshot, WorkProgress


@dataclass(frozen=True)
class SupervisorPolicy:
    control_interval: float = 0.5
    control_max_age: float = 2
    scheduler_max_age: float = 20
    ingestion_max_age: float = 15
    read_timeout: float = 30
    decode_timeout: float = 5
    chunks_timeout: float = 5
    shutdown_grace: float = 10
    shutdown_timeout: float = 3

    def __post_init__(self):
        for value, maximum in (
            (self.control_interval, 0.5),
            (self.control_max_age, 2),
            (self.scheduler_max_age, 20),
            (self.ingestion_max_age, 15),
            (self.read_timeout, 30),
            (self.decode_timeout, 5),
            (self.chunks_timeout, 5),
            (self.shutdown_grace, 10),
            (self.shutdown_timeout, 3),
        ):
            if type(value) not in (int, float) or not isfinite(value) or not 0 < value <= maximum:
                raise ValueError("invalid_supervisor_policy")
        if self.control_interval >= self.control_max_age:
            raise ValueError("invalid_supervisor_policy")


_POLICY = SupervisorPolicy()


@dataclass(frozen=True)
class ShutdownReport:
    run_joined: bool
    control_joined: bool
    consumer_joined: bool
    worker_children_joined: bool
    renewal_joined: bool
    runner_closed: bool
    runner_settled: bool
    runner_close_joined: bool
    waiters_joined: bool
    outcome_unknown: bool
    quiescent: bool


State = Literal["starting", "serving", "degraded", "draining", "faulted", "stopped"]


def _jitter() -> float:
    return uniform(0.9, 1.1)


def _finite(value: object) -> bool:
    if not isinstance(value, (int, float)):
        return False
    return type(value) in (int, float) and isfinite(value)


def _done(task: asyncio.Task | None) -> bool:
    return task is None or task.done()


class TextWorkerSupervisor:
    """run never awaits close; the retained close coordinator can join run honestly.

    run owns polling/control and retains its sole consumer task. close independently
    stops admission, bounds grace and teardown, and never loses unresolved handles.
    Calling request_stop alone disables admission; the lifespan owner must call close.
    """

    def __init__(
        self,
        consumer: ExactTextConsumer,
        *,
        policy: SupervisorPolicy = _POLICY,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = _jitter,
    ):
        if (
            type(consumer) is not ExactTextConsumer
            or type(policy) is not SupervisorPolicy
            or not callable(clock)
            or not callable(sleep)
            or not callable(jitter)
            or consumer.worker.work_observation is not True
            or type(consumer.worker.lease_seconds) is not int
            or consumer.worker.lease_seconds != 30
            or not _finite(consumer.worker.heartbeat_seconds)
            or consumer.worker.heartbeat_seconds != 5
        ):
            raise ValueError("invalid_supervisor_profile")
        self.consumer, self.policy = consumer, policy
        self._runner = consumer.repository.runner
        self._generation = self._runner.snapshot().generation
        self._clock, self._sleep, self._jitter = clock, sleep, jitter
        self._state: State = "starting"
        self._stop = asyncio.Event()
        self._control_stop = asyncio.Event()
        self._started = self._faulted = False
        self._last_now: float | None = None
        self._control_at: float | None = None
        self._scheduler_at: float | None = None
        self._scheduler_marker = consumer.repository.last_progress
        self._cycle = consumer.snapshot().cycle
        self._worker_cycle = consumer.worker.work_snapshot().cycle
        self._successful_cycle = False
        self._outcome_unknown = False
        self._run_task: asyncio.Task | None = None
        self._control_task: asyncio.Task | None = None
        self._consumer_task: asyncio.Task | None = None
        self._close_task: asyncio.Task[ShutdownReport] | None = None
        self._runner_close: asyncio.Task[bool] | None = None
        self._waiters: set[asyncio.Task] = set()

    def request_stop(self) -> None:
        self._stop.set()
        self.consumer.request_stop()
        if not self._faulted and self._state != "stopped":
            self._state = "draining"

    def _fault(self) -> None:
        self._faulted = True
        self.request_stop()
        self._state = "faulted"
        if self._consumer_task is not None and not self._consumer_task.done():
            self._consumer_task.cancel()

    def _now(self) -> float:
        try:
            now = self._clock()
        except Exception:  # noqa: BLE001 — arbitrary clock failure must fault the loop
            self._fault()
            raise DelegationUnavailable("supervisor_unavailable") from None
        if not _finite(now) or (self._last_now is not None and now < self._last_now):
            self._fault()
            raise DelegationUnavailable("supervisor_unavailable")
        self._last_now = now
        return now

    def _waiter(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._waiters.add(task)
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return task

    async def _join(self, *tasks: asyncio.Task | None) -> None:
        owned = [task for task in tasks if task is not None]
        for task in owned:
            if not task.done():
                task.cancel()
        if not owned:
            return
        joined = asyncio.gather(*owned, return_exceptions=True)
        cancelled = False
        while not joined.done():
            try:
                await asyncio.shield(joined)
            except asyncio.CancelledError:
                cancelled = True
        joined.result()
        self._waiters.difference_update(owned)
        if cancelled:
            raise asyncio.CancelledError

    async def _pause(self, delay: float, stop: asyncio.Event) -> None:
        target = self._now() + delay
        while not stop.is_set():
            remaining = target - self._now()
            if remaining <= 0:
                return
            timer = self._waiter(self._sleep(remaining))
            stopped = self._waiter(stop.wait())
            try:
                await asyncio.wait((timer, stopped), return_when=asyncio.FIRST_COMPLETED)
                if timer.done():
                    timer.result()
            finally:
                await self._join(timer, stopped)

    def _work_ok(self, work: TextWorkSnapshot, now: float) -> bool:
        if work.cycle == self._worker_cycle:
            return True  # no TextWorker invocation in this consumer cycle yet
        if work.cycle != self._worker_cycle + 1:
            return False
        records = work.completed + ((work.progress,) if work.progress is not None else ())
        for progress in records:
            limit = self._phase_limit(progress)
            end = progress.completed_at if progress.completed_at is not None else now
            times = (progress.admitted_at, progress.started_at, progress.completed_at)
            if (
                any(not _finite(t) or t > now for t in times if t is not None)
                or progress.admitted_at > end
                or (progress.started_at is not None and progress.started_at < progress.admitted_at)
                or (progress.started_at is not None and end < progress.started_at)
                or end >= progress.admitted_at + limit
            ):
                return False
        return True

    def _phase_limit(self, progress: WorkProgress) -> float:
        if progress.phase == "read":
            return self.policy.read_timeout
        if progress.phase == "decode":
            return self.policy.decode_timeout
        if progress.phase == "chunks":
            return self.policy.chunks_timeout
        return self.consumer.policy.scheduler_timeout

    def _bounds_ok(self, snapshot: ConsumerSnapshot, now: float) -> bool:
        if (
            snapshot.generation != self._generation
            or snapshot.runner.closed
            or snapshot.draining
            and not self._stop.is_set()
            or self.consumer.worker.observation_failed
        ):
            return False
        active = self._consumer_task is not None and not self._consumer_task.done()
        if snapshot.cycle != self._cycle:
            # A newly created task may not have executed its first instruction yet.
            return active and snapshot.cycle == self._cycle - 1
        work = self.consumer.worker.work_snapshot()
        if not self._work_ok(work, now):
            return False
        if snapshot.delivery_deadline is not None and active and now >= snapshot.delivery_deadline:
            return False
        terminal = snapshot.terminal_progress
        if terminal is not None:
            end = terminal.committed_at if terminal.committed_at is not None else now
            if end >= terminal.started_at + self.consumer.policy.terminal_settlement_timeout:
                return False
            if (
                active
                and now >= terminal.started_at + self.consumer.policy.terminal_settlement_timeout
            ):
                return False
        if not active:
            return (
                not snapshot.runner.pending and work.children_joined and not snapshot.renewal_active
            )
        if snapshot.lease_failed or snapshot.cancelled:
            return False
        if snapshot.phase not in ("claiming", "idle") and (
            snapshot.delivery_valid_until is None or now >= snapshot.delivery_valid_until
        ):
            return False
        if snapshot.phase in ("claiming", "validating", "reconciling", "acknowledging") and (
            snapshot.phase_started_at is None
            or now >= (snapshot.phase_started_at + self.consumer.policy.scheduler_timeout)
        ):
            return False
        if work.cycle == self._worker_cycle + 1 and terminal is None:
            ingestion = work.ingestion_progress
            if ingestion is not None and (
                ingestion.committed_at is None
                or ingestion.committed_at > now
                or now - ingestion.committed_at > self.policy.ingestion_max_age
                or now >= ingestion.started_at + 30
            ):
                return False
        return True

    def is_ready(self) -> bool:
        """Current read-only facts; this method cannot heal or acknowledge a control loop."""
        if (
            self._state != "serving"
            or self._stop.is_set()
            or self._faulted
            or not self._successful_cycle
            or self._run_task is None
            or self._run_task.done()
            or self._control_task is None
            or self._control_task.done()
        ):
            return False
        try:
            now = self._clock()
        except Exception:  # noqa: BLE001 — readiness must fail closed on any clock failure
            return False
        if (
            not _finite(now)
            or self._last_now is None
            or now < self._last_now
            or self._control_at is None
            or not 0 <= now - self._control_at <= self.policy.control_max_age
            or self._scheduler_at is None
            or not 0 <= now - self._scheduler_at <= self.policy.scheduler_max_age
        ):
            return False
        return self._bounds_ok(self.consumer.snapshot(), now)

    def _observe(self, *, control: bool = False) -> None:
        now = self._now()
        if self._control_at is not None and now - self._control_at > self.policy.control_max_age:
            self._fault()
            return
        snapshot = self.consumer.snapshot()
        self._outcome_unknown |= snapshot.outcome_unknown
        progress = snapshot.scheduler_progress
        if progress is not None and progress is not self._scheduler_marker:
            if (
                not _finite(progress.started_at)
                or progress.started_at > now
                or progress.committed_at is not None
                and (
                    not _finite(progress.committed_at)
                    or not progress.started_at <= progress.committed_at <= now
                )
            ):
                self._fault()
                return
            if progress.committed_at is not None:
                self._scheduler_at = progress.committed_at
                self._scheduler_marker = progress
        if not self._bounds_ok(snapshot, now):
            self._fault()
            return
        if control:
            self._control_at = now

    async def _control(self) -> None:
        try:
            while not self._control_stop.is_set():
                self._observe(control=True)
                if self._faulted:
                    return
                await self._pause(self.policy.control_interval, self._control_stop)
        finally:
            if not self._control_stop.is_set():
                self._fault()

    async def run(self) -> None:
        if self._started or self._stop.is_set() or self._runner.snapshot().closed:
            raise RuntimeError("supervisor_lifetime_closed")
        self._started = True
        self._run_task = asyncio.current_task()
        self._control_task = asyncio.create_task(self._control())
        idle = nonprogress = errors = 0
        try:
            while not self._stop.is_set():
                self._observe()
                if self._faulted or self._control_task.done():
                    self._fault()
                    break
                if (
                    self._runner.snapshot().pending
                    or not self.consumer.worker.work_snapshot().children_joined
                ):
                    self._fault()
                    break
                self._worker_cycle = self.consumer.worker.work_snapshot().cycle
                self._cycle = self.consumer.snapshot().cycle + 1
                previous = self.consumer.repository.last_progress
                self._consumer_task = asyncio.create_task(self.consumer.run_one())
                try:
                    result = await asyncio.shield(self._consumer_task)
                except DelegationUnavailable:
                    self._state = "degraded"
                    result = "dependency_error"
                self._observe()
                snapshot = self.consumer.snapshot()
                if self._faulted or self._stop.is_set():
                    break
                if result == "busy":
                    self._fault()
                    break
                progress = snapshot.scheduler_progress
                committed = (
                    progress is not None
                    and progress is not previous
                    and progress.committed_at is not None
                    and snapshot.delivery_started_at is not None
                    and progress.started_at >= snapshot.delivery_started_at
                    and not snapshot.outcome_unknown
                    and not snapshot.lease_failed
                )
                success = committed and (
                    result == "idle"
                    and progress is not None
                    and progress.phase == "claim"
                    or result in ("succeeded", "failed", "cancelled")
                    and snapshot.acknowledgment == "confirmed"
                    and progress is not None
                    and progress.phase == "acknowledge"
                )
                if success:
                    self._successful_cycle = True
                    self._state = "serving"
                    errors = nonprogress = 0
                    if result == "idle":
                        delay = (1, 2, 4, 5)[min(idle, 3)]
                        idle += 1
                    else:
                        idle = 0
                        delay = 0.1
                elif result in ("not_claimed", "yielded", "lease_lost"):
                    self._state = "degraded"
                    idle = 0
                    delay = (1, 2, 4, 5)[min(nonprogress, 3)]
                    nonprogress += 1
                elif result == "dependency_error":
                    self._state = "degraded"
                    factor = self._jitter()
                    if not _finite(factor) or not 0.9 <= factor <= 1.1:
                        self._fault()
                        break
                    delay = min(10, max(1, (1, 2, 4, 8, 10)[min(errors, 4)] * factor))
                    errors += 1
                else:
                    self._fault()
                    break
                await self._pause(delay, self._stop)
        finally:
            self.request_stop()
            self._control_stop.set()
            try:
                await self._join(self._consumer_task)
            finally:
                await self._join(self._control_task)

    def _shutdown_now(self) -> float | None:
        try:
            return self._now()
        except DelegationUnavailable:
            return None

    def _remaining(self, deadline: float | None, real_deadline: float) -> float:
        remaining = real_deadline - asyncio.get_running_loop().time()
        now = self._shutdown_now()
        if deadline is not None and now is not None:
            remaining = min(remaining, deadline - now)
        return max(0, remaining)

    async def _wait_until(
        self, tasks: tuple[asyncio.Task, ...], deadline: float | None, real_deadline: float
    ) -> None:
        pending = tuple(task for task in tasks if not task.done())
        remaining = self._remaining(deadline, real_deadline)
        if not pending or remaining <= 0:
            return
        timer = self._waiter(self._sleep(remaining))
        try:
            while pending and not timer.done():
                remaining = self._remaining(deadline, real_deadline)
                if remaining <= 0:
                    break
                await asyncio.wait(
                    (*pending, timer), timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
                pending = tuple(task for task in pending if not task.done())
        finally:
            timer.cancel()
            # Cancellation-resistant sleep is retained, not joined beyond the deadline.
            remaining = self._remaining(deadline, real_deadline)
            if remaining > 0 and not timer.done():
                await asyncio.wait((timer,), timeout=remaining)
            if timer.done():
                if not timer.cancelled():
                    timer.exception()
                self._waiters.discard(timer)

    def _report(self) -> ShutdownReport:
        snapshot = self.consumer.snapshot()
        run_joined = _done(self._run_task)
        control_joined = _done(self._control_task)
        waiters_joined = all(t.done() for t in self._waiters)
        runner_close_joined = _done(self._runner_close)
        consumer_joined = _done(self._consumer_task)
        worker_joined = self.consumer.worker.work_snapshot().children_joined
        renewal_joined = not snapshot.renewal_active
        closed, settled = snapshot.runner.closed, not snapshot.runner.pending
        self._outcome_unknown |= snapshot.outcome_unknown or not settled or not consumer_joined
        quiescent = all(
            (
                run_joined,
                control_joined,
                consumer_joined,
                worker_joined,
                renewal_joined,
                closed,
                settled,
                runner_close_joined,
                waiters_joined,
            )
        )
        self._state = "stopped" if quiescent and not self._faulted else "faulted"
        return ShutdownReport(
            run_joined,
            control_joined,
            consumer_joined,
            worker_joined,
            renewal_joined,
            closed,
            settled,
            runner_close_joined,
            waiters_joined,
            self._outcome_unknown,
            quiescent,
        )

    async def _close(self) -> ShutdownReport:
        loop = asyncio.get_running_loop()
        grace = self.policy.shutdown_grace
        grace_deadline = loop.time() + grace
        now = self._shutdown_now()
        if (
            self._consumer_task is not None
            and not self._consumer_task.done()
            and not self._faulted
            and now is not None
        ):
            await self._wait_until((self._consumer_task,), now + grace, grace_deadline)
        # One fixed teardown budget includes coroutine joins AND runner.close.
        real_deadline = loop.time() + self.policy.shutdown_timeout
        now = self._shutdown_now()
        deadline = None if now is None else now + self.policy.shutdown_timeout
        self._control_stop.set()
        owned = tuple(
            t for t in (self._run_task, self._control_task, self._consumer_task) if t is not None
        )
        for task in owned:
            if not task.done():
                task.cancel()
        remaining = self._remaining(deadline, real_deadline)
        if remaining > 0:
            self._runner_close = asyncio.create_task(self._runner.close(timeout=remaining))
            await self._wait_until((*owned, self._runner_close), deadline, real_deadline)
        if self._runner_close is not None and not self._runner_close.done():
            self._runner_close.cancel()
            # Cancellation of this waiter is not cancellation of the actual Future.
        return self._report()

    async def close(self) -> ShutdownReport:
        self.request_stop()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError
        # Repeated calls observe current owned facts without opening another budget.
        return self._report()


def worker_probe(supervisor: TextWorkerSupervisor) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        return supervisor.is_ready()

    return check
