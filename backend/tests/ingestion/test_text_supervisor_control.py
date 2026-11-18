"""S2b1 local supervision, not installed HTTP readiness or native I/O evidence."""

import asyncio
import threading
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest
from app.workers import text as text_module
from app.workers.delegation import BoundedThreadRunner
from app.workers.text import TextWorker
from sqlalchemy import Engine
from test_text_consumer_renewal import Clock
from test_text_supervisor_lifecycle import (
    SyntheticTransactions,
    assert_readonly,
    join_runner,
    until,
)


def observed_worker(harness, runner, observer=None):
    return TextWorker(
        cast(Engine, harness.engine),
        operation_runner=runner,
        store_factory=lambda auth: harness,
        progress_observer=observer,
        work_observation=True,
    )


@pytest.mark.parametrize("phase", ["read", "decode", "chunks"])
def test_work_progress_actual_entry_completion_never_db_commit(monkeypatch, phase):
    async def run():
        clock = Clock(monkeypatch)
        harness = SyntheticTransactions(monkeypatch)
        runner = BoundedThreadRunner()
        entered, release = threading.Event(), threading.Event()
        read_release = asyncio.Event()
        events = []
        task = None

        async def read(actor, scope):
            entered.set()
            await read_release.wait()
            return b"hello"

        original = getattr(text_module, "decode_text" if phase == "decode" else "chunks")

        def held(value):
            entered.set()
            assert release.wait(3)
            return original(value)

        try:
            worker = observed_worker(harness, runner, events.append)
            if phase == "read":
                monkeypatch.setattr(harness, "read", read)
            else:
                monkeypatch.setattr(
                    text_module, "decode_text" if phase == "decode" else "chunks", held
                )
            task = asyncio.create_task(worker.run_one(actor_id=harness.actor, job_id=harness.job))
            await until(entered.is_set)
            snapshot = worker.work_snapshot()
            progress = snapshot.progress
            assert progress is not None and progress.phase == phase
            assert progress.admitted_at == progress.started_at == 100
            assert progress.completed_at is None
            assert not hasattr(progress, "committed_at")
            ingestion = snapshot.ingestion_progress
            assert ingestion is not None and ingestion.committed_at == 100
            assert not snapshot.children_joined
            assert_readonly(progress, "phase", "read", FrozenInstanceError)
            clock.advance(1)
            release.set()
            read_release.set()
            assert await task == "succeeded"
            final = worker.work_snapshot()
            assert final.children_joined
            assert final.progress is not None and final.progress.phase == "publish"
            assert final.progress.completed_at == 101
            assert final.progress.sequence > progress.sequence
            completed = [entry for entry in final.completed if entry.sequence == progress.sequence]
            assert len(completed) == 1
            assert completed[0].admitted_at == 100 and completed[0].completed_at == 101
            assert len(final.completed) == 8
            assert all(
                event.phase in ("ingestion_claim", "pulse", "publish", "finish") for event in events
            )
            assert worker.terminal_committed
        finally:
            release.set()
            read_release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await join_runner(runner)
            assert not clock.waiters

    asyncio.run(run())


def test_work_observation_is_explicit_and_identity_cannot_opt_in(monkeypatch):
    harness = SyntheticTransactions(monkeypatch)
    with pytest.raises(ValueError):
        TextWorker(cast(Engine, harness.engine), work_observation=True)
    with pytest.raises(ValueError):
        TextWorker(cast(Engine, harness.engine), work_observation=cast(Any, 1))
    worker = harness.worker(None, None)
    assert worker.work_snapshot().progress is None


def test_read_authorization_pulse_does_not_replace_main_phase(monkeypatch):
    async def run():
        clock = Clock(monkeypatch)
        harness = SyntheticTransactions(monkeypatch)
        runner = BoundedThreadRunner()
        worker = observed_worker(harness, runner)
        reading, pulse, pulsed, release = (asyncio.Event() for _ in range(4))

        class Store:
            def __init__(self, authorize):
                self.authorize = authorize

            async def read(self, actor_id, scope) -> bytes:
                reading.set()
                await pulse.wait()
                await self.authorize(actor_id, scope, "read")
                pulsed.set()
                await release.wait()
                return b"hello"

        worker.store_factory = Store
        task = asyncio.create_task(worker.run_one(actor_id=harness.actor, job_id=harness.job))
        try:
            await asyncio.wait_for(reading.wait(), 2)
            before = worker.work_snapshot().progress
            assert before is not None and before.phase == "read"
            clock.advance(4)
            pulse.set()
            await asyncio.wait_for(pulsed.wait(), 2)
            after = worker.work_snapshot()
            assert after.ingestion_progress is not None
            assert after.ingestion_progress.committed_at == 104
            assert after.progress is before
            release.set()
            assert await task == "succeeded"
        finally:
            pulse.set()
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await join_runner(runner)
            assert not clock.waiters

    asyncio.run(run())


class Runtime:
    def __init__(self, monkeypatch, **kwargs):
        from app.workers.supervisor import TextWorkerSupervisor
        from test_text_consumer_renewal import Scheduler

        self.clock = Clock(monkeypatch)
        self.harness = SyntheticTransactions(monkeypatch)
        self.runner = BoundedThreadRunner()
        self.worker = observed_worker(self.harness, self.runner)
        self.scheduler = Scheduler(self.runner, self.harness)
        self.scheduler.values["claim"] = None
        self.consumer = self.scheduler.consumer(self.worker, self.clock)
        self.supervisor = TextWorkerSupervisor(
            self.consumer, clock=self.clock, sleep=self.clock.sleep, jitter=lambda: 1, **kwargs
        )
        self.task = None
        self.tasks = []

    def start(self):
        self.task = asyncio.create_task(self.supervisor.run())
        self.tasks.append(self.task)
        return self.task

    async def cleanup(self):
        self.scheduler.release.set()
        self.harness.release.set()
        self.supervisor.request_stop()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.supervisor.close()
        await join_runner(self.runner)
        assert not self.clock.waiters

    async def healthy(self):
        self.start()
        await until(self.supervisor.is_ready)


def test_supervisor_starts_only_after_actual_committed_cycle_and_stops_sticky(monkeypatch):
    async def run():
        runtime = Runtime(monkeypatch)
        s = runtime.supervisor
        runtime.scheduler.hold = "claim"
        runtime.scheduler.release.clear()
        try:
            assert not s.is_ready()
            runtime.start()
            await until(runtime.scheduler.entered.is_set)
            assert not s.is_ready()
            runtime.scheduler.release.set()
            await until(s.is_ready)
            before = runtime.consumer.snapshot()
            for _ in range(5):
                assert s.is_ready()
            assert runtime.consumer.snapshot() == before
            s.request_stop()
            assert not s.is_ready()
            report = await s.close()
            assert report.quiescent and report.runner_closed
            assert report.run_joined and report.control_joined and report.consumer_joined
            assert report.worker_children_joined and report.renewal_joined
            assert report.runner_settled and not report.outcome_unknown
            assert not s.is_ready()
            with pytest.raises(RuntimeError):
                await s.run()
        finally:
            await runtime.cleanup()

    asyncio.run(run())


def test_supervisor_probe_reads_current_control_age_without_refresh(monkeypatch):
    async def run():
        from app.workers.supervisor import worker_probe

        runtime = Runtime(monkeypatch)
        try:
            await runtime.healthy()
            probe = worker_probe(runtime.supervisor)
            assert await probe() is True
            runtime.clock.now += 2.01
            assert await probe() is False
            assert await probe() is False
        finally:
            await runtime.cleanup()

    asyncio.run(run())


def test_supervisor_idle_backoff_and_immediate_stop(monkeypatch):
    async def run():
        runtime = Runtime(monkeypatch)
        try:
            await runtime.healthy()
            for delay in (1, 2, 4, 5):
                before = len(runtime.scheduler.calls)
                target = runtime.clock.now + delay
                await until(
                    lambda target=target: any(t == target for t, _ in runtime.clock.waiters)
                )
                # Step control separately; no oversleep waiver for a live poll timer.
                for _ in range(delay * 2):
                    runtime.clock.advance(0.5)
                    await asyncio.sleep(0.002)
                await until(lambda before=before: len(runtime.scheduler.calls) == before + 1)
            before = len(runtime.scheduler.calls)
            runtime.supervisor.request_stop()
            report = await runtime.supervisor.close()
            assert report.quiescent
            assert len(runtime.scheduler.calls) == before
        finally:
            await runtime.cleanup()

    asyncio.run(run())


def test_control_stall_cannot_be_hidden_by_committed_idle_polls(monkeypatch):
    async def run():
        r = Runtime(monkeypatch)
        sleeping = asyncio.Event()
        release = asyncio.Event()
        normal_sleep = r.clock.sleep

        async def sleep(seconds):
            if seconds == 0.5:
                sleeping.set()
                await release.wait()
            else:
                await normal_sleep(seconds)

        r.supervisor._sleep = sleep
        try:
            await r.healthy()
            await sleeping.wait()
            for _ in range(6):
                r.clock.advance(0.5)
                await asyncio.sleep(0.005)
            assert len(r.scheduler.commits) >= 2
            assert not r.supervisor.is_ready()
        finally:
            release.set()
            await r.cleanup()

    asyncio.run(run())


def test_probe_clock_exception_is_private_and_does_not_mutate(monkeypatch):
    async def run():
        r = Runtime(monkeypatch)
        try:
            await r.healthy()
            before = r.consumer.snapshot()
            normal = r.supervisor._clock

            def broken():
                raise RuntimeError("synthetic-secret-shaped://clock")

            r.supervisor._clock = broken
            try:
                assert r.supervisor.is_ready() is False
                assert r.consumer.snapshot() == before
            finally:
                r.supervisor._clock = normal
        finally:
            await r.cleanup()

    asyncio.run(run())


def test_shutdown_clock_fault_still_closes_owned_runner(monkeypatch):
    async def run():
        r = Runtime(monkeypatch)
        normal = r.supervisor._clock
        try:
            await r.healthy()
            r.supervisor._clock = lambda: float("nan")
            try:
                report = await r.supervisor.close()
                assert report.runner_closed and report.quiescent
                assert not report.outcome_unknown
            finally:
                r.supervisor._clock = normal
        finally:
            # A failed coordinator is deliberately surfaced by the assertion above.
            r.supervisor._clock = normal
            r.supervisor.request_stop()
            await asyncio.gather(*r.tasks, return_exceptions=True)
            await join_runner(r.runner)
            assert not r.clock.waiters

    asyncio.run(run())


def test_teardown_budget_does_not_wait_forever_for_cancel_resistant_timer(monkeypatch):
    async def run():
        from app.workers.supervisor import SupervisorPolicy

        r = Runtime(monkeypatch, policy=SupervisorPolicy(shutdown_timeout=0.03))
        entered, release = asyncio.Event(), asyncio.Event()
        held = []

        async def resistant_sleep(seconds):
            held.append(asyncio.current_task())
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        continue

        close = None
        try:
            await r.healthy()
            # Only shutdown timers are injected; existing poll/control sleeps remain owned.
            r.supervisor._sleep = resistant_sleep
            close = asyncio.create_task(r.supervisor.close())
            await asyncio.wait_for(entered.wait(), 1)
            done, _ = await asyncio.wait((close,), timeout=0.15)
            assert close in done
            report = close.result()
            assert not report.waiters_joined and not report.quiescent
        finally:
            release.set()
            if close is not None:
                await asyncio.gather(close, return_exceptions=True)
            await asyncio.gather(
                *(task for task in held if task is not None), return_exceptions=True
            )
            await r.cleanup()

    asyncio.run(run())


async def advance_control(runtime, seconds):
    end = runtime.clock.now + seconds
    while runtime.clock.now < end:
        runtime.clock.advance(min(0.5, end - runtime.clock.now))
        await asyncio.sleep(0.005)


async def deliver_after_idle(runtime):
    await runtime.healthy()
    runtime.scheduler.values["claim"] = vars(runtime.scheduler.grant)
    await advance_control(runtime, 1)


@pytest.mark.parametrize("phase", ["read", "decode", "chunks"])
def test_late_completion_history_rejects_fast_next_phase_before_control_tick(monkeypatch, phase):
    async def run():
        from app.workers.supervisor import SupervisorPolicy

        r = Runtime(monkeypatch, policy=SupervisorPolicy(**{phase + "_timeout": 0.1}))
        entered, release = threading.Event(), threading.Event()
        next_entered, next_release = threading.Event(), threading.Event()
        read_release = asyncio.Event()

        async def read(actor, scope):
            entered.set()
            await read_release.wait()
            return b"hello"

        original_decode, original_chunks = text_module.decode_text, text_module.chunks

        def first(value):
            entered.set()
            assert release.wait(2)
            return (original_decode if phase == "decode" else original_chunks)(value)

        def second(value):
            next_entered.set()
            assert next_release.wait(2)
            return (original_decode if phase == "read" else original_chunks)(value)

        if phase == "read":
            monkeypatch.setattr(r.harness, "read", read)
            monkeypatch.setattr(text_module, "decode_text", second)
        elif phase == "decode":
            monkeypatch.setattr(text_module, "decode_text", first)
            monkeypatch.setattr(text_module, "chunks", second)
        else:
            monkeypatch.setattr(text_module, "chunks", first)
            r.harness.hold_phase = "publish"
            r.harness.release.clear()
        try:
            await deliver_after_idle(r)
            await until(entered.is_set)
            assert r.supervisor.is_ready()
            original = r.worker.work_snapshot().progress
            assert original is not None and original.phase == phase
            # No fake-clock waiters fire: finish and next entry precede the next control tick.
            r.clock.now += 0.2
            release.set()
            read_release.set()
            await until(r.harness.precommit.is_set if phase == "chunks" else next_entered.is_set)
            work = r.worker.work_snapshot()
            assert work.progress is not None and work.progress.phase != phase
            assert any(
                p.sequence == original.sequence and p.completed_at == r.clock.now
                for p in work.completed
            )
            assert not r.supervisor.is_ready()
            next_release.set()
            r.harness.release.set()
            assert r.task is not None
            await until(r.task.done)
            assert len([call for call in r.scheduler.calls if call[0] == "claim"]) == 2
            assert not r.supervisor.is_ready()
        finally:
            release.set()
            next_release.set()
            read_release.set()
            await r.cleanup()

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["not_claimed", "yielded", "lease_lost"])
def test_nonprogress_backoff_preserves_serial_actual_consumer_cycles(monkeypatch, outcome):
    async def run():
        from app.documents.transactions import QuotaUnavailable

        r = Runtime(monkeypatch)
        r.scheduler.values["claim"] = vars(r.scheduler.grant)
        if outcome == "not_claimed":
            r.harness.claim_none = True
        elif outcome == "yielded":

            def quota(*args, **kwargs):
                raise QuotaUnavailable("quota_gate_busy")

            monkeypatch.setattr(text_module, "heartbeat_job", quota)
        else:
            monkeypatch.setattr(text_module, "heartbeat_job", lambda *args, **kwargs: False)
        try:
            r.start()
            for cycle, delay in enumerate((1, 2, 4, 5), start=1):
                await until(
                    lambda cycle=cycle: (
                        r.consumer.snapshot().cycle == cycle
                        and r.consumer.snapshot().phase == "settled"
                    )
                )
                target = r.clock.now + delay
                await until(lambda target=target: any(t == target for t, _ in r.clock.waiters))
                assert not r.supervisor.is_ready()
                assert r.worker.work_snapshot().children_joined
                assert not r.consumer.snapshot().renewal_active
                assert not r.runner.snapshot().pending
                await advance_control(r, delay)
            await until(lambda: r.consumer.snapshot().cycle == 5)
            assert r.harness.reads == 0
            r.supervisor.request_stop()
            report = await r.supervisor.close()
            assert report.quiescent
        finally:
            await r.cleanup()

    asyncio.run(run())


@pytest.mark.parametrize("factor", [0.9, 1.1])
def test_dependency_backoff_jitter_clipped_and_reset_by_committed_success(monkeypatch, factor):
    async def run():
        r = Runtime(monkeypatch)
        r.supervisor._jitter = lambda: factor
        r.scheduler.fail = "claim"
        try:
            r.start()
            for cycle, base in enumerate((1, 2, 4, 8, 10), start=1):
                delay = min(10, max(1, base * factor))
                await until(
                    lambda cycle=cycle: (
                        r.consumer.snapshot().cycle == cycle
                        and r.consumer.snapshot().phase == "uncertain"
                    )
                )
                target = r.clock.now + delay
                await until(
                    lambda target=target: any(abs(t - target) < 1e-8 for t, _ in r.clock.waiters)
                )
                assert not r.supervisor.is_ready()
                assert not r.runner.snapshot().pending
                assert r.worker.work_snapshot().cycle == 0
                if cycle == 5:
                    r.scheduler.fail = None
                await advance_control(r, delay)
            await until(r.supervisor.is_ready)
            r.scheduler.fail = "claim"
            await advance_control(r, 1)
            target = r.clock.now + max(1, factor)
            await until(lambda: any(abs(t - target) < 1e-8 for t, _ in r.clock.waiters))
            assert not r.supervisor.is_ready()
        finally:
            await r.cleanup()

    asyncio.run(run())


@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf"), 0.8, 1.2])
def test_invalid_jitter_faults_without_replacement_or_hot_retry(monkeypatch, invalid):
    async def run():
        r = Runtime(monkeypatch)
        r.supervisor._jitter = lambda: invalid
        r.scheduler.fail = "claim"
        try:
            r.start()
            assert r.task is not None
            await until(r.task.done)
            assert len(r.scheduler.calls) == 1
            assert not r.supervisor.is_ready()
            report = await r.supervisor.close()
            assert report.quiescent and report.outcome_unknown
        finally:
            await r.cleanup()

    asyncio.run(run())


def test_terminal_entry_healthy_then_fixed_first_entry_deadline(monkeypatch):
    async def run():
        r = Runtime(monkeypatch)
        r.harness.hold_phase = "publish"
        r.harness.release.clear()
        try:
            await deliver_after_idle(r)
            await until(r.harness.precommit.is_set)
            terminal = r.consumer.snapshot().terminal_progress
            assert terminal is not None and terminal.committed_at is None
            assert r.supervisor.is_ready()
            await advance_control(r, 9.5)
            assert r.supervisor.is_ready()
            # Predicate must reject at the bound, before the watchdog callback runs.
            r.clock.now += 0.5
            assert not r.supervisor.is_ready()
            assert r.consumer.snapshot().terminal_progress is terminal
            r.harness.release.set()
            assert r.task is not None
            await until(r.task.done)
            assert r.worker.terminal_committed
            assert r.consumer.snapshot().acknowledgment != "confirmed"
            assert not r.supervisor.is_ready()
        finally:
            await r.cleanup()

    asyncio.run(run())


def test_shutdown_retains_real_future_and_close_false_is_not_rollback(monkeypatch):
    async def run():
        from app.workers.supervisor import SupervisorPolicy

        r = Runtime(
            monkeypatch, policy=SupervisorPolicy(shutdown_grace=0.02, shutdown_timeout=0.04)
        )
        r.harness.hold_phase = "publish"
        r.harness.release.clear()
        try:
            await deliver_after_idle(r)
            await until(r.harness.precommit.is_set)
            generation = r.runner.snapshot().generation
            start = asyncio.get_running_loop().time()
            report = await r.supervisor.close()
            assert asyncio.get_running_loop().time() - start < 0.2
            assert report.runner_closed and not report.runner_settled
            assert not report.quiescent and report.outcome_unknown
            assert r.runner.snapshot().generation == generation and r.runner.snapshot().pending
            assert r.harness.terminal is None
            assert "finish" not in r.harness.commits
            assert not any(name == "acknowledge" for name, _, _ in r.scheduler.calls)
            r.harness.release.set()
            await until(lambda: not r.runner.snapshot().pending)
            assert r.task is not None
            await until(r.task.done)
            final = await r.supervisor.close()
            assert final.quiescent and final.runner_close_joined and final.waiters_joined
            assert final.outcome_unknown and r.worker.terminal_committed
            assert r.harness.disposals == 0
        finally:
            await r.cleanup()

    asyncio.run(run())


def test_repeated_close_cancellation_has_one_budget_and_retains_child_hierarchy(monkeypatch):
    async def run():
        from app.workers.supervisor import SupervisorPolicy

        r = Runtime(
            monkeypatch, policy=SupervisorPolicy(shutdown_grace=0.02, shutdown_timeout=0.04)
        )
        reading, cancelled, release = (asyncio.Event() for _ in range(3))

        async def read(actor, scope):
            reading.set()
            try:
                await release.wait()
            finally:
                while not release.is_set():
                    try:
                        cancelled.set()
                        await release.wait()
                    except asyncio.CancelledError:
                        continue
            return b"hello"

        monkeypatch.setattr(r.harness, "read", read)
        close = None
        try:
            await deliver_after_idle(r)
            await reading.wait()
            close = asyncio.create_task(r.supervisor.close())
            await cancelled.wait()
            assert not r.supervisor.is_ready()
            for _ in range(3):
                close.cancel()
                await asyncio.sleep(0.002)
            done, _ = await asyncio.wait((close,), timeout=0.2)
            assert close in done and close.cancelled()
            report = await r.supervisor.close()
            assert report.runner_closed and report.runner_settled
            assert not report.worker_children_joined and not report.consumer_joined
            assert not report.run_joined and not report.quiescent
            assert report.outcome_unknown
            release.set()
            assert r.task is not None
            await until(r.task.done)
            final = await r.supervisor.close()
            assert final.quiescent and final.outcome_unknown
        finally:
            release.set()
            if close is not None:
                await asyncio.gather(close, return_exceptions=True)
            await r.cleanup()

    asyncio.run(run())
