"""S2a local consumer mechanics; synthetic context exits are not wire-COMMIT proof."""

import asyncio
import threading
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict
from types import SimpleNamespace
from typing import Any, cast

import pytest
from app.workers import delegation
from app.workers import text as text_module
from app.workers.delegation import (
    BoundedThreadRunner,
    DelegationRepository,
    ExactTextConsumer,
    JobExecutionGrant,
    SchedulerFactory,
)
from sqlalchemy.orm import Session
from test_text_supervisor_lifecycle import (
    SyntheticTransactions,
    assert_readonly,
    join_runner,
    until,
)


class Clock:
    def __init__(self, monkeypatch):
        self.now = 100.0
        self.waiters = []
        monkeypatch.setattr(delegation, "monotonic", self)
        monkeypatch.setattr(text_module, "monotonic", self)

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        target = self.now + seconds
        event = asyncio.Event()
        self.waiters.append((target, event))
        try:
            await event.wait()
        finally:
            self.waiters.remove((target, event))

    def advance(self, seconds):
        self.now += seconds
        for target, event in tuple(self.waiters):
            if target <= self.now:
                event.set()


class Scheduler:
    """Real repository methods, fixed SQL queries and committing factory context."""

    def __init__(self, runner, harness):
        self.grant = JobExecutionGrant(
            harness.job,
            harness.actor,
            harness.scope.workspace_id,
            harness.scope.notebook_id,
            harness.scope.version_id,
            "text_extract_v1",
            harness.token,
        )
        self.values = {
            "claim": asdict(self.grant),
            "validate": "executable",
            "renew": True,
            "acknowledge": True,
        }
        self.calls = []
        self.commits = []
        self.hold: str | None = None
        self.fail: str | None = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.repo = DelegationRepository(SchedulerFactory(self.transaction, 1, 3), runner)

    @contextmanager
    def transaction(self):
        phase = None

        def execute(query, params=None):
            nonlocal phase
            sql = str(query)
            value = True
            if "transaction_isolation" in sql:
                value = "read committed"
            for name, function in (
                ("claim", "claim_text_job"),
                ("validate", "validate_text_job"),
                ("renew", "renew_text_job"),
                ("acknowledge", "ack_text_job"),
            ):
                if function in sql:
                    phase = name
                    self.calls.append((name, params, threading.get_ident()))
                    value = self.values[name]
            return SimpleNamespace(
                scalar_one=lambda: value,
                mappings=lambda: SimpleNamespace(one_or_none=lambda: value),
            )

        yield cast(Session, SimpleNamespace(execute=execute))
        if phase == self.hold:
            self.entered.set()
            assert self.release.wait(3)
        if phase == self.fail:
            raise RuntimeError("synthetic-secret-shaped://ambiguous-commit")
        self.commits.append(phase)

    def consumer(self, worker, clock, **kwargs):
        return ExactTextConsumer(
            self.repo,
            worker,
            pool_timeout=1,
            connect_timeout=3,
            clock=clock,
            sleep=clock.sleep,
            **kwargs,
        )


def test_consumer_policy_has_independent_bounded_whole_and_lease():
    policy = delegation.ConsumerPolicy(delivery_seconds=300, delivery_timeout=120)
    assert policy.renewal_seconds == 15
    assert_readonly(policy, "delivery_seconds", 60, FrozenInstanceError)
    for changes in (
        {"delivery_seconds": True},
        {"delivery_seconds": 301},
        {"renewal_seconds": 30},
        {"renewal_seconds": float("nan")},
        {"scheduler_timeout": 11},
        {"delivery_timeout": 121},
        {"terminal_settlement_timeout": 0},
    ):
        with pytest.raises(ValueError):
            # Deliberately untrusted types exercise validation, not coercion.
            delegation.ConsumerPolicy(**cast(dict[str, Any], changes))


def test_delegated_internal_terminal_observation_needs_no_caller_callback(monkeypatch):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        runner = BoundedThreadRunner()
        try:
            worker = harness.worker(runner, None)
            assert await worker.run_one(actor_id=harness.actor, job_id=harness.job) == "succeeded"
            progress = worker.terminal_progress
            assert progress is not None and progress.phase == "publish"
            assert progress.committed_at is not None
            assert worker.last_progress is progress
            assert not worker.observation_failed
        finally:
            await join_runner(runner)

    asyncio.run(run())


def test_scheduler_progress_requires_actual_factory_context_exit(monkeypatch):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        runner = BoundedThreadRunner()
        scheduler = Scheduler(runner, harness)
        scheduler.hold = "claim"
        scheduler.release.clear()
        task = asyncio.create_task(scheduler.repo.claim())
        try:
            await until(scheduler.entered.is_set)
            entry = scheduler.repo.last_progress
            assert entry is not None
            assert entry.phase == "claim" and entry.committed_at is None
            assert scheduler.commits == []
            scheduler.release.set()
            assert await task == scheduler.grant
            committed = scheduler.repo.last_progress
            assert committed is not None and committed.committed_at is not None
            assert committed.started_at == entry.started_at
            assert committed.committed_at >= committed.started_at
            assert scheduler.commits == ["claim"]
        finally:
            scheduler.release.set()
            await asyncio.gather(task, return_exceptions=True)
            await join_runner(runner)

    asyncio.run(run())


def test_consumer_renews_exact_grant_on_shared_runner_without_backlog(monkeypatch):
    async def run():
        clock = Clock(monkeypatch)
        harness = SyntheticTransactions(monkeypatch)
        runner = BoundedThreadRunner()
        scheduler = Scheduler(runner, harness)
        reading, release_read = asyncio.Event(), asyncio.Event()

        async def read(actor, scope):
            harness.reads += 1
            reading.set()
            await release_read.wait()
            return b"hello"

        monkeypatch.setattr(harness, "read", read)
        worker = harness.worker(runner, None)
        task = None
        try:
            consumer = scheduler.consumer(worker, clock)
            task = asyncio.create_task(consumer.run_one())
            await asyncio.wait_for(reading.wait(), 2)
            await until(lambda: any(target == 115 for target, _ in clock.waiters))
            assert scheduler.commits == ["claim", "validate"]
            clock.advance(15)
            await until(lambda: "renew" in scheduler.commits)
            await until(lambda: any(target == 130 for target, _ in clock.waiters))
            assert consumer.snapshot().delivery_valid_until == 175
            assert [name for name, _, _ in scheduler.calls].count("renew") == 1
            renewal = next(params for name, params, _ in scheduler.calls if name == "renew")
            assert renewal == asdict(scheduler.grant) | {"seconds": 60}
            assert len({thread for _, _, thread in scheduler.calls}) == 1
            release_read.set()
            assert await task == "succeeded"
            snapshot = consumer.snapshot()
            assert snapshot.terminal_observed and snapshot.acknowledgment == "confirmed"
            assert not snapshot.renewal_active and not snapshot.runner.pending
            clock.advance(15)
            await asyncio.sleep(0)
            assert [name for name, _, _ in scheduler.calls] == [
                "claim",
                "validate",
                "renew",
                "acknowledge",
            ]
        finally:
            scheduler.release.set()
            harness.release.set()
            release_read.set()
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await join_runner(runner)

    asyncio.run(run())


class Delivery:
    """Owned local tasks, fake time, real executor; every barrier released at exit."""

    def __init__(self, monkeypatch, *, policy=None, runner_factory=BoundedThreadRunner):
        self.clock = Clock(monkeypatch)
        self.harness = SyntheticTransactions(monkeypatch)
        self.runner = runner_factory()
        self.scheduler = Scheduler(self.runner, self.harness)
        self.reading, self.release_read = asyncio.Event(), asyncio.Event()
        self.worker = self.harness.worker(self.runner, None)
        self.consumer = self.scheduler.consumer(
            self.worker, self.clock, policy=policy or delegation.ConsumerPolicy()
        )
        self._task: asyncio.Task[str] | None = None

        async def read(actor, scope):
            self.harness.reads += 1
            self.reading.set()
            await self.release_read.wait()
            return b"hello"

        monkeypatch.setattr(self.harness, "read", read)

    @property
    def task(self) -> asyncio.Task[str]:
        assert self._task is not None, "delivery must be started before observation"
        return self._task

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.scheduler.release.set()
        self.harness.release.set()
        self.release_read.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await join_runner(self.runner)
        assert not self.clock.waiters

    def start(self):
        self._task = asyncio.create_task(self.consumer.run_one())
        return self._task

    async def at_read(self):
        self.start()
        await asyncio.wait_for(self.reading.wait(), 2)
        await self.wait_timer(100 + self.consumer.policy.renewal_seconds)

    async def wait_timer(self, target):
        await until(lambda: any(deadline == target for deadline, _ in self.clock.waiters))

    def names(self):
        return [name for name, _, _ in self.scheduler.calls]


@pytest.mark.parametrize("failure", ["false", "exception", "timeout"])
def test_renewal_failure_before_publication_cancels_without_finish_or_ack(monkeypatch, failure):
    async def run():
        async with Delivery(monkeypatch) as d:
            if failure == "false":
                d.scheduler.values["renew"] = False
            elif failure == "exception":
                d.scheduler.fail = "renew"
            else:
                d.scheduler.hold = "renew"
                d.scheduler.release.clear()
            await d.at_read()
            d.clock.advance(15)
            await until(lambda: "renew" in d.names())
            if failure == "timeout":
                await until(d.scheduler.entered.is_set)
                await d.wait_timer(125)
                d.clock.advance(10)
            if failure == "exception":
                with pytest.raises(
                    delegation.DelegationUnavailable, match="^delegation_unavailable$"
                ):
                    await d.task
            else:
                assert await d.task == "lease_lost"
            snapshot = d.consumer.snapshot()
            assert snapshot.lease_failed
            assert snapshot.outcome_unknown == (failure != "false")
            assert not snapshot.terminal_observed and not snapshot.renewal_active
            assert d.harness.events.count("finish_returning") == 0
            assert "acknowledge" not in d.names()
            assert d.harness.reads == 1
            if failure == "timeout":
                assert snapshot.runner.pending
                assert await d.consumer.run_one() == "busy"
                d.scheduler.release.set()
                await until(lambda: not d.runner.snapshot().pending)
                assert d.scheduler.repo.last_progress is not None
                assert d.scheduler.repo.last_progress.committed_at is not None
                assert d.consumer.snapshot().outcome_unknown
                assert d.consumer.snapshot().lease_failed
                assert "acknowledge" not in d.names()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["publish", "finish"])
@pytest.mark.parametrize("late", [False, True])
def test_renewal_timeout_during_terminal_uses_original_entry_settlement_budget(
    monkeypatch, phase, late
):
    async def run():
        policy = delegation.ConsumerPolicy(renewal_seconds=1, scheduler_timeout=2)
        async with Delivery(monkeypatch, policy=policy) as d:
            d.harness.hold_phase = phase
            d.harness.release.clear()
            if phase == "finish":
                from app.documents.storage import StorageError

                async def failed_read(actor, scope):
                    raise StorageError()

                monkeypatch.setattr(d.harness, "read", failed_read)
            else:
                d.release_read.set()
            d.start()
            await until(d.harness.precommit.is_set)
            terminal = d.worker.terminal_progress
            assert terminal is not None and terminal.started_at == 100
            await d.wait_timer(101)
            d.clock.advance(1)
            await d.wait_timer(103)
            # Renewal waits on the SAME runner; not even a scheduler SQL call yet.
            assert d.names() == ["claim", "validate"]
            d.clock.advance(11 if late else 6)
            if late:
                assert await d.task == "lease_lost"
            else:
                await d.wait_timer(110)
                assert not d.task.done()
                d.clock.advance(3)
                assert await d.task == "lease_lost"
            assert d.runner.snapshot().pending
            assert d.consumer.snapshot().outcome_unknown
            assert d.harness.terminal is None
            assert await d.consumer.run_one() == "busy"
            d.harness.release.set()
            await until(lambda: not d.runner.snapshot().pending)
            snapshot = d.consumer.snapshot()
            assert snapshot.terminal_observed and snapshot.lease_failed
            assert snapshot.acknowledgment == "not_attempted"
            assert snapshot.outcome_unknown  # cancelled result never revived
            assert d.harness.events.count("finish_returning") == 1
            assert "renew" not in d.names() and "acknowledge" not in d.names()

    asyncio.run(run())


def test_normal_late_terminal_commit_is_known_but_cannot_ack(monkeypatch):
    async def run():
        async with Delivery(monkeypatch) as d:
            d.harness.hold_phase = "publish"
            d.harness.release.clear()
            d.release_read.set()
            d.start()
            await until(d.harness.precommit.is_set)
            d.clock.advance(11)
            d.harness.release.set()
            assert await d.task == "lease_lost"
            snapshot = d.consumer.snapshot()
            assert snapshot.terminal_observed and snapshot.lease_failed
            assert not snapshot.outcome_unknown
            assert snapshot.acknowledgment == "not_attempted"
            assert d.harness.terminal == "succeeded"
            assert "acknowledge" not in d.names()

    asyncio.run(run())


@pytest.mark.parametrize("ack", [False, "exception", True])
def test_terminal_reconciliation_ack_outcome_is_separate_from_committed_truth(monkeypatch, ack):
    async def run():
        async with Delivery(monkeypatch) as d:
            d.scheduler.values["validate"] = "succeeded"
            if ack == "exception":
                d.scheduler.fail = "acknowledge"
            else:
                d.scheduler.values["acknowledge"] = ack
            d.start()
            if ack == "exception":
                with pytest.raises(delegation.DelegationUnavailable):
                    await d.task
            else:
                assert await d.task == ("succeeded" if ack else "lease_lost")
            snapshot = d.consumer.snapshot()
            assert snapshot.terminal_observed
            assert snapshot.acknowledgment == ("confirmed" if ack is True else "unconfirmed")
            assert snapshot.outcome_unknown == (ack == "exception")
            assert d.harness.reads == 0 and d.harness.events == []
            assert d.names() == ["claim", "validate", "acknowledge"]
            assert not snapshot.renewal_active

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["claim", "validate", "renew", "acknowledge"])
def test_cancelled_scheduler_response_never_revives_ownership(monkeypatch, phase):
    async def run():
        async with Delivery(monkeypatch) as d:
            d.scheduler.hold = phase
            d.scheduler.release.clear()
            if phase == "acknowledge":
                d.scheduler.values["validate"] = "succeeded"
            if phase == "renew":
                await d.at_read()
                d.clock.advance(15)
            else:
                d.start()
            await until(d.scheduler.entered.is_set)
            d.task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await d.task
            before = d.consumer.snapshot()
            assert before.cancelled and before.outcome_unknown and before.runner.pending
            assert not before.renewal_active
            assert await d.consumer.run_one() == "busy"
            calls = list(d.names())
            d.scheduler.release.set()
            await until(lambda: not d.runner.snapshot().pending)
            after = d.consumer.snapshot()
            assert after.cancelled and after.outcome_unknown
            assert after.acknowledgment != "confirmed"
            assert d.names() == calls
            progress = after.scheduler_progress
            assert progress is not None and progress.committed_at is not None
            assert progress.phase == phase
            d.scheduler.values["claim"] = None
            assert await d.consumer.run_one() == "idle"
            assert d.names() == calls + ["claim"]  # ordinary new selection, no replay

    asyncio.run(run())


def test_request_stop_is_sticky_but_active_healthy_delivery_can_finish(monkeypatch):
    async def run():
        async with Delivery(monkeypatch) as d:
            await d.at_read()
            d.consumer.request_stop()
            assert d.consumer.snapshot().draining
            assert await d.consumer.run_one() == "busy"
            d.release_read.set()
            assert await d.task == "succeeded"
            snapshot = d.consumer.snapshot()
            assert snapshot.draining and snapshot.acknowledgment == "confirmed"
            with pytest.raises(delegation.DelegationUnavailable):
                await d.consumer.run_one()
            assert d.names() == ["claim", "validate", "acknowledge"]

    asyncio.run(run())


def test_repeated_renewals_never_slide_whole_delivery_deadline(monkeypatch):
    async def run():
        policy = delegation.ConsumerPolicy(delivery_timeout=40, renewal_seconds=5)
        async with Delivery(monkeypatch, policy=policy) as d:
            await d.at_read()
            for value in range(105, 140, 5):
                await d.wait_timer(value)
                d.clock.advance(5)
                await until(lambda value=value: d.names().count("renew") == (value - 100) // 5)
                await d.wait_timer(min(value + 5, 140))
                assert d.consumer.snapshot().delivery_deadline == 140
            d.clock.advance(5)
            assert await d.task == "lease_lost"
            assert d.consumer.snapshot().lease_failed
            assert d.harness.events.count("finish_returning") == 0
            assert "acknowledge" not in d.names()

    asyncio.run(run())


def test_external_cancellation_during_timer_join_must_propagate_not_ack(monkeypatch):
    async def run():
        async with Delivery(monkeypatch) as d:
            joining, release = asyncio.Event(), asyncio.Event()
            original_sleep = d.clock.sleep

            async def controlled_sleep(seconds):
                try:
                    await original_sleep(seconds)
                finally:
                    if not joining.is_set():
                        joining.set()
                        await release.wait()

            # Trusted test injection before run, no runner/private grant inspection.
            other = ExactTextConsumer(
                d.scheduler.repo,
                d.worker,
                pool_timeout=1,
                connect_timeout=3,
                clock=d.clock,
                sleep=controlled_sleep,
            )
            task = asyncio.create_task(other.run_one())
            d.release_read.set()
            try:
                await asyncio.wait_for(joining.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert other.snapshot().cancelled
                assert "acknowledge" not in d.names()
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["claim", "renew"])
def test_late_committed_scheduler_reply_cannot_extend_from_receipt(monkeypatch, phase):
    async def run():
        committed, return_reply = asyncio.Event(), asyncio.Event()
        scheduler = None

        class DelayedReply(BoundedThreadRunner):
            async def __call__(self, operation, *args):
                value = await super().__call__(operation, *args)
                assert scheduler is not None
                progress = scheduler.repo.last_progress
                if (
                    progress is not None
                    and progress.phase == phase
                    and progress.committed_at is not None
                    and not committed.is_set()
                ):
                    committed.set()
                    await return_reply.wait()
                return value

        async with Delivery(monkeypatch, runner_factory=DelayedReply) as d:
            scheduler = d.scheduler
            try:
                if phase == "renew":
                    await d.at_read()
                    d.clock.advance(15)
                else:
                    d.start()
                await asyncio.wait_for(committed.wait(), 2)
                assert not d.runner.snapshot().pending
                progress = d.scheduler.repo.last_progress
                assert progress is not None and progress.committed_at is not None
                d.clock.advance(61)
                return_reply.set()
                assert await d.task == "lease_lost"
                snapshot = d.consumer.snapshot()
                assert snapshot.lease_failed and snapshot.acknowledgment == "not_attempted"
                assert snapshot.delivery_valid_until == (160 if phase == "renew" else None)
                assert "acknowledge" not in d.names()
                assert d.harness.events.count("finish_returning") == 0
            finally:
                return_reply.set()

    asyncio.run(run())


def test_renewal_validity_uses_admission_before_runner_queue_and_commit(monkeypatch):
    async def run():
        policy = delegation.ConsumerPolicy(renewal_seconds=1)
        async with Delivery(monkeypatch, policy=policy) as d:
            await d.at_read()
            entered, release = threading.Event(), threading.Event()

            def occupied():
                entered.set()
                assert release.wait(3)

            occupied_task = asyncio.create_task(d.runner(occupied))
            try:
                await until(entered.is_set)
                d.clock.advance(1)
                await d.wait_timer(111)  # renewal ADMITTED at101, no SQL yet
                assert "renew" not in d.names()
                d.clock.advance(3)
                release.set()
                await occupied_task
                await until(lambda: "renew" in d.scheduler.commits)
                await d.wait_timer(105)
                progress = d.scheduler.repo.last_progress
                assert progress is not None and progress.started_at == 104
                assert progress.committed_at == 104
                assert d.consumer.snapshot().delivery_valid_until == 161  # NOT164
                d.release_read.set()
                assert await d.task == "succeeded"
            finally:
                release.set()
                await asyncio.gather(occupied_task, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("renewed", [False, True, "exception"])
def test_queued_renewal_settles_before_ack_and_never_runs_after_ack(monkeypatch, renewed):
    async def run():
        policy = delegation.ConsumerPolicy(renewal_seconds=1)
        async with Delivery(monkeypatch, policy=policy) as d:
            d.scheduler.hold = "renew"
            d.scheduler.release.clear()
            if renewed == "exception":
                d.scheduler.fail = "renew"
            else:
                d.scheduler.values["renew"] = renewed
            d.harness.hold_phase = "publish"
            d.harness.release.clear()
            d.release_read.set()
            d.start()
            await until(d.harness.precommit.is_set)
            await d.wait_timer(101)
            d.clock.advance(1)
            await d.wait_timer(111)
            assert "renew" not in d.names()
            d.clock.advance(1)
            d.harness.release.set()
            await until(d.scheduler.entered.is_set)
            assert d.harness.terminal == "succeeded"
            assert not d.task.done() and "acknowledge" not in d.names()
            assert d.consumer.snapshot().terminal_observed
            d.scheduler.release.set()
            if renewed == "exception":
                with pytest.raises(delegation.DelegationUnavailable):
                    await d.task
            else:
                assert await d.task == ("succeeded" if renewed else "lease_lost")
            snapshot = d.consumer.snapshot()
            assert snapshot.terminal_observed and not snapshot.renewal_active
            assert snapshot.acknowledgment == ("confirmed" if renewed is True else "not_attempted")
            assert d.names() == ["claim", "validate", "renew"] + (
                ["acknowledge"] if renewed is True else []
            )
            d.clock.advance(100)
            await asyncio.sleep(0)
            assert d.consumer.snapshot().acknowledgment == snapshot.acknowledgment
            assert d.names().count("renew") == 1
            assert d.harness.reads == 1 and d.harness.events.count("finish_returning") == 1

    asyncio.run(run())


def test_previous_terminal_same_clock_tick_does_not_classify_new_validation(monkeypatch):
    async def run():
        async with Delivery(monkeypatch) as d:
            d.release_read.set()
            assert (
                await d.worker.run_one(actor_id=d.harness.actor, job_id=d.harness.job)
                == "succeeded"
            )
            previous = d.worker.terminal_progress
            assert previous is not None and previous.started_at == d.clock.now
            d.scheduler.hold = "validate"
            d.scheduler.release.clear()
            d.start()
            await until(d.scheduler.entered.is_set)
            snapshot = d.consumer.snapshot()
            assert snapshot.phase == "validating"
            assert snapshot.terminal_progress is None and not snapshot.terminal_observed
            d.task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await d.task
            assert not d.consumer.snapshot().terminal_observed
            assert d.worker.terminal_progress is previous
            assert d.harness.reads == 1 and "acknowledge" not in d.names()

    asyncio.run(run())


def test_new_terminal_at_identical_clock_tick_uses_record_identity(monkeypatch):
    async def run():
        async with Delivery(monkeypatch) as d:
            d.release_read.set()
            d.start()
            assert await d.task == "succeeded"
            previous = d.worker.terminal_progress
            first = d.consumer.snapshot()
            d.harness.terminal = None
            assert await d.consumer.run_one() == "succeeded"
            current = d.worker.terminal_progress
            assert current == previous and current is not previous
            second = d.consumer.snapshot()
            assert second.terminal_observed and second.terminal_progress is current
            assert second.cycle == first.cycle + 1
            assert first.delivery_started_at == second.delivery_started_at
            assert second.acknowledgment == "confirmed"

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["claim", "validate", "acknowledge"])
def test_unknown_scheduler_commit_has_one_attempt_and_no_immediate_replay(monkeypatch, phase):
    async def run():
        async with Delivery(monkeypatch) as d:
            d.scheduler.fail = phase
            if phase == "acknowledge":
                d.scheduler.values["validate"] = "succeeded"
            d.start()
            with pytest.raises(delegation.DelegationUnavailable) as caught:
                await d.task
            assert str(caught.value) == "delegation_unavailable"
            assert caught.value.__suppress_context__
            snapshot = d.consumer.snapshot()
            assert snapshot.outcome_unknown and not snapshot.runner.pending
            assert d.names().count(phase) == 1
            assert not snapshot.renewal_active and d.harness.reads == 0
            assert phase not in d.scheduler.commits
            d.scheduler.fail = None
            d.scheduler.values["claim"] = None
            assert await d.consumer.run_one() == "idle"
            assert d.names()[-1] == "claim"

    asyncio.run(run())


@pytest.mark.parametrize("clock_fault", ["backwards", "nonfinite", "future_progress"])
def test_invalid_monotonic_domain_fails_closed_without_ack(monkeypatch, clock_fault):
    async def run():
        async with Delivery(monkeypatch) as d:
            await d.at_read()
            if clock_fault == "future_progress":
                monkeypatch.setattr(text_module, "monotonic", lambda: 1000.0)
            else:
                d.clock.now = 99 if clock_fault == "backwards" else float("nan")
            d.release_read.set()
            with pytest.raises(delegation.DelegationUnavailable):
                await d.task
            snapshot = d.consumer.snapshot()
            assert snapshot.lease_failed and snapshot.acknowledgment == "not_attempted"
            assert "acknowledge" not in d.names()

    asyncio.run(run())


def test_lifecycle_snapshot_is_frozen_sanitized_and_not_timer_progress(monkeypatch):
    async def run():
        async with Delivery(monkeypatch) as d:
            await d.at_read()
            before = d.consumer.snapshot()
            assert_readonly(before, "cycle", 900, FrozenInstanceError)
            assert set(asdict(before)) == {
                "generation",
                "cycle",
                "phase",
                "phase_started_at",
                "delivery_started_at",
                "delivery_deadline",
                "delivery_valid_until",
                "draining",
                "cancelled",
                "lease_failed",
                "outcome_unknown",
                "terminal_observed",
                "acknowledgment",
                "renewal_active",
                "runner",
                "scheduler_progress",
                "text_progress",
                "terminal_progress",
            }
            d.clock.advance(1)
            assert d.consumer.snapshot() == before
            rendered = repr(before)
            for identifier in asdict(d.scheduler.grant).values():
                assert str(identifier) not in rendered
            assert "secret" not in rendered and "credential" not in rendered
            d.release_read.set()
            assert await d.task == "succeeded"

    asyncio.run(run())


def test_repeated_external_cancel_during_final_renewal_join_releases_admission(monkeypatch):
    async def run():
        async with Delivery(monkeypatch) as d:
            joining, release = asyncio.Event(), asyncio.Event()
            original_sleep = d.clock.sleep

            async def controlled_sleep(seconds):
                try:
                    await original_sleep(seconds)
                finally:
                    if seconds == 15:
                        joining.set()
                        await release.wait()

            consumer = ExactTextConsumer(
                d.scheduler.repo,
                d.worker,
                pool_timeout=1,
                connect_timeout=3,
                clock=d.clock,
                sleep=controlled_sleep,
            )
            task = asyncio.create_task(consumer.run_one())
            try:
                await asyncio.wait_for(d.reading.wait(), 2)
                await d.wait_timer(115)
                task.cancel()
                await asyncio.wait_for(joining.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert consumer.snapshot().cancelled
                assert not consumer.snapshot().renewal_active
                assert not d.runner.snapshot().pending
                d.scheduler.values["claim"] = None
                assert await consumer.run_one() == "idle"
                assert "acknowledge" not in d.names()
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_settled_phase_timestamp_is_actual_post_ack_exit(monkeypatch):
    async def run():
        async with Delivery(monkeypatch) as d:
            d.scheduler.values["validate"] = "succeeded"
            d.scheduler.hold = "acknowledge"
            d.scheduler.release.clear()
            d.start()
            await until(d.scheduler.entered.is_set)
            entry = d.consumer.snapshot()
            assert entry.phase == "acknowledging" and entry.phase_started_at == 100
            d.clock.advance(1)
            d.scheduler.release.set()
            assert await d.task == "succeeded"
            settled = d.consumer.snapshot()
            assert settled.phase == "settled" and settled.phase_started_at == 101
            progress = settled.scheduler_progress
            assert progress is not None and progress.committed_at == 101

    asyncio.run(run())


def quota_busy_error():
    """Canonical typed fixture shape from test_concurrency_correction.DriverError/busy."""
    import psycopg
    from sqlalchemy.exc import DBAPIError

    class DriverError(psycopg.Error):
        def __init__(self):
            self.sqlstate = "55P03"
            self._diag = SimpleNamespace(message_primary="quota_gate_busy")

        @property
        def diag(self) -> Any:
            return self._diag

    return DBAPIError(None, None, DriverError())


class RetryTerminalTransactions:
    """Real decorated callback retries; synthetic rollback/close precedes backoff."""

    def __init__(self, monkeypatch, delivery, phase):
        from app.db.quota import classify_quota_error
        from app.documents import transactions
        from app.documents.storage import StorageError
        from sqlalchemy.exc import DBAPIError

        self.attempts = 0
        self.active = False
        self.events = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.observed = []
        original_factory = text_module.Session
        retry = self
        assert classify_quota_error(quota_busy_error()) == "quota_gate_busy"

        class Database:
            def __init__(self, engine):
                # The injected SyntheticTransactions session has test-only status.
                self.inner = cast(Any, original_factory(engine))

            def __getattr__(self, name):
                return getattr(self.inner, name)

            @property
            def status(self):
                return self.inner.status

            @status.setter
            def status(self, value):
                self.inner.status = value

            def __enter__(self):
                self.inner.__enter__()
                return self

            def __exit__(self, *args):
                result = self.inner.__exit__(*args)
                retry.events.append("session_closed")
                return result

            @contextmanager
            def begin(self):
                retry.active = True
                try:
                    with self.inner.begin():
                        yield
                        if delivery.harness.phase == phase:
                            retry.attempts += 1
                            if retry.attempts == 1:
                                raise quota_busy_error()
                            retry.entered.set()
                            assert retry.release.wait(3)
                except DBAPIError:
                    retry.events.append("rollback")
                    raise
                finally:
                    retry.active = False

        def backoff(delay):
            assert delay == 0.01
            assert not retry.active and retry.events[-2:] == ["rollback", "session_closed"]
            assert retry.attempts == 1 and phase not in delivery.harness.commits
            retry.events.append("backoff")
            delivery.clock.now = 100.1  # still within the real250ms retry admission budget

        monkeypatch.setattr(text_module, "Session", Database)
        monkeypatch.setattr(transactions, "monotonic", delivery.clock)
        monkeypatch.setattr(transactions, "sleep", backoff)
        if phase == "finish":

            async def failed_read(actor, scope):
                raise StorageError()

            monkeypatch.setattr(delivery.harness, "read", failed_read)
        delivery.worker = delivery.harness.worker(delivery.runner, self.observed.append)
        delivery.consumer = delivery.scheduler.consumer(
            delivery.worker, delivery.clock, policy=delivery.consumer.policy
        )
        delivery.release_read.set()

    def assert_attempt_truth(self, worker, phase, committed_at):
        records = [record for record in self.observed if record.phase == phase]
        assert [(record.started_at, record.committed_at) for record in records] == [
            (100, None),
            (100.1, None),
            (100.1, committed_at),
        ]
        assert worker.last_progress is records[-1]
        assert self.attempts == 2 and self.events.count("backoff") == 1


@pytest.mark.parametrize("phase", ["publish", "finish"])
@pytest.mark.parametrize("committed_at", [109.0, 110.05])
def test_terminal_retry_preserves_original_deadline_and_actual_attempt_observer(
    monkeypatch, phase, committed_at
):
    async def run():
        async with Delivery(monkeypatch) as d:
            retry = RetryTerminalTransactions(monkeypatch, d, phase)
            try:
                d.start()
                await until(lambda: retry.entered.is_set() or d.task.done())
                if d.task.done():
                    d.task.result()  # fail immediately on a fixture/consumer error
                assert retry.entered.is_set()
                assert d.runner.snapshot().pending and d.harness.terminal is None
                d.clock.now = committed_at
                retry.release.set()
                result = await d.task
                retry.assert_attempt_truth(d.worker, phase, committed_at)
                assert d.consumer.snapshot().acknowledgment == (
                    "confirmed" if committed_at < 110 else "not_attempted"
                )
                expected = "succeeded" if phase == "publish" else "failed"
                assert result == (expected if committed_at < 110 else "lease_lost")
                terminal = d.worker.terminal_progress
                assert terminal is not None and terminal.committed_at == committed_at
                assert terminal.started_at == 100
                assert d.consumer.snapshot().terminal_observed
                assert not d.consumer.snapshot().outcome_unknown
                assert d.names().count("acknowledge") == int(committed_at < 110)
                assert d.harness.commits.count(phase) == 1
            finally:
                retry.release.set()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["publish", "finish"])
@pytest.mark.parametrize("failure_at", [107.0, 110.05])
@pytest.mark.parametrize("renewal_error", [False, True])
def test_terminal_retry_renewal_failure_cannot_restart_original_settlement(
    monkeypatch, phase, failure_at, renewal_error
):
    async def run():
        reply_held, return_reply = asyncio.Event(), asyncio.Event()

        class DelayedTerminalReply(BoundedThreadRunner):
            async def __call__(self, operation, *args):
                result = await super().__call__(operation, *args)
                if getattr(operation, "__name__", None) == f"_{phase}":
                    # SQL/Future is ALREADY settled. Only async response is held;
                    # this lets the SAME runner perform the queued renewal.
                    reply_held.set()
                    await return_reply.wait()
                return result

        policy = delegation.ConsumerPolicy(renewal_seconds=1)
        async with Delivery(monkeypatch, policy=policy, runner_factory=DelayedTerminalReply) as d:
            retry = RetryTerminalTransactions(monkeypatch, d, phase)
            if renewal_error:
                d.scheduler.fail = "renew"
            else:
                d.scheduler.values["renew"] = False
            try:
                d.start()
                await until(lambda: retry.entered.is_set() or d.task.done())
                if d.task.done():
                    d.task.result()  # do not turn a fixture failure into a barrier timeout
                assert retry.entered.is_set()
                await d.wait_timer(101)
                d.clock.now = 101
                d.clock.advance(0)
                await d.wait_timer(111)
                assert "renew" not in d.names()  # actual terminal still owns runner
                d.clock.now = failure_at
                retry.release.set()
                await asyncio.wait_for(reply_held.wait(), 2)
                await until(lambda: d.consumer.snapshot().lease_failed)
                await until(
                    lambda: (
                        d.task.done()
                        or any(target in (110, 110.1) for target, _ in d.clock.waiters)
                    )
                )
                assert not d.runner.snapshot().pending  # not unresolved SQL/thread evidence
                retry.assert_attempt_truth(d.worker, phase, failure_at)
                if failure_at < 110:
                    assert any(target == 110 for target, _ in d.clock.waiters)
                    assert not any(target == 110.1 for target, _ in d.clock.waiters)
                    d.clock.now = 110
                    d.clock.advance(0)
                else:
                    assert not any(target == 110.1 for target, _ in d.clock.waiters)
                    assert d.task.done()
                if renewal_error:
                    with pytest.raises(delegation.DelegationUnavailable):
                        await d.task
                else:
                    assert await d.task == "lease_lost"
                snapshot = d.consumer.snapshot()
                assert snapshot.terminal_observed and snapshot.lease_failed
                assert snapshot.acknowledgment == "not_attempted"
                assert "acknowledge" not in d.names() and d.harness.commits.count(phase) == 1
            finally:
                retry.release.set()
                return_reply.set()

    asyncio.run(run())


def test_whole_deadline_checks_clock_before_expired_timer_resumes(monkeypatch):
    async def run():
        policy = delegation.ConsumerPolicy(delivery_timeout=1)
        async with Delivery(monkeypatch, policy=policy) as d:
            d.harness.claim_none = True
            d.harness.hold_phase = "ingestion_claim"
            d.harness.release.clear()
            d.start()
            await until(d.harness.precommit.is_set)
            await d.wait_timer(101)
            # Intentionally do NOT notify fake sleepers: callback completion wins
            # the wake ordering even though original whole deadline has passed.
            d.clock.now = 101.01
            d.harness.release.set()
            assert await d.task == "lease_lost"
            snapshot = d.consumer.snapshot()
            assert snapshot.delivery_deadline == 101
            assert snapshot.lease_failed and snapshot.phase == "uncertain"
            assert not snapshot.terminal_observed and snapshot.acknowledgment == "not_attempted"
            assert d.harness.reads == 0 and d.names() == ["claim", "validate"]

    asyncio.run(run())


class TimestampRetryTransactions:
    """Actual retry bodies; only producer clock readings and DB boundary are synthetic."""

    def __init__(self, monkeypatch, delivery, phase, entries, committed_at):
        from app.db.quota import classify_quota_error
        from app.documents import transactions
        from app.documents.storage import StorageError
        from sqlalchemy.exc import DBAPIError

        self.attempts = 0
        self.active = False
        self.events = []
        self.observed = []
        self.samples = []
        self.after_backoff_events = []
        self.entered, self.release = threading.Event(), threading.Event()
        retry = self
        original_factory = text_module.Session
        pending_entry = []
        assert classify_quota_error(quota_busy_error()) == "quota_gate_busy"

        class Database:
            def __init__(self, engine):
                # SyntheticTransactions' adapter has test-only status, not a Session API.
                self.inner = cast(Any, original_factory(engine))

            def __getattr__(self, name):
                return getattr(self.inner, name)

            @property
            def status(self):
                return self.inner.status

            @status.setter
            def status(self, value):
                self.inner.status = value

            def __enter__(self):
                self.inner.__enter__()
                return self

            def __exit__(self, *args):
                result = self.inner.__exit__(*args)
                retry.events.append("session_closed")
                return result

            @contextmanager
            def begin(self):
                retry.active = True
                try:
                    with self.inner.begin():
                        yield
                        if delivery.harness.phase == phase:
                            retry.attempts += 1
                            if retry.attempts <= len(entries):
                                raise quota_busy_error()
                            retry.entered.set()
                            assert retry.release.wait(3)
                except DBAPIError:
                    retry.events.append("rollback")
                    raise
                finally:
                    retry.active = False

        def backoff(delay):
            assert delay == 0.01 * retry.attempts
            assert not retry.active and retry.events[-2:] == ["rollback", "session_closed"]
            assert phase not in delivery.harness.commits and retry.attempts <= 2
            retry.events.append("backoff")
            retry.after_backoff_events = list(delivery.harness.events)
            delivery.clock.now = 100 + retry.attempts / 10
            pending_entry.append(entries[retry.attempts - 1])

        def producer_clock():
            if pending_entry:
                value = pending_entry.pop()
                retry.samples.append(("entry", value))
                return value
            if delivery.harness.terminal is not None:
                assert delivery.harness.events[-1] == "session_exit"
                retry.samples.append(("commit", committed_at))
                return committed_at
            return delivery.clock.now

        monkeypatch.setattr(text_module, "Session", Database)
        monkeypatch.setattr(text_module, "monotonic", producer_clock)
        # Retry admission remains in its real unchanged250ms budget; malformed
        # producer readings are NOT also fed into unrelated scheduler clocks.
        monkeypatch.setattr(transactions, "monotonic", delivery.clock)
        monkeypatch.setattr(transactions, "sleep", backoff)
        if phase == "finish":

            async def failed_read(actor, scope):
                raise StorageError()

            monkeypatch.setattr(delivery.harness, "read", failed_read)
        delivery.worker = delivery.harness.worker(delivery.runner, self.observed.append)
        delivery.consumer = delivery.scheduler.consumer(
            delivery.worker, delivery.clock, policy=delivery.consumer.policy
        )
        delivery.release_read.set()

    async def release_terminal_or_fault(self, delivery):
        await until(lambda: self.entered.is_set() or delivery.task.done())
        if self.entered.is_set():
            assert delivery.runner.snapshot().pending and delivery.harness.terminal is None
            delivery.clock.now = 109
            self.release.set()
        return (await asyncio.gather(delivery.task, return_exceptions=True))[0]


@pytest.mark.parametrize("phase", ["publish", "finish"])
@pytest.mark.parametrize(
    "entries,committed_at,committed",
    [
        pytest.param([99.0], 109.0, False, id="entry-backward"),
        pytest.param([float("nan")], 109.0, False, id="entry-nan"),
        pytest.param([None], 109.0, False, id="entry-none"),
        pytest.param([True], 109.0, False, id="entry-bool"),
        pytest.param(["invalid-clock"], 109.0, False, id="entry-string"),
        pytest.param([float("inf")], 109.0, False, id="entry-inf"),
        pytest.param([100.1, 100.05], 109.0, False, id="third-entry-backward"),
        pytest.param([1000.0], 109.0, True, id="future-retry-entry"),
        pytest.param([100.2], 100.1, True, id="commit-before-retry-entry"),
        pytest.param([100.1], None, True, id="commit-none"),
        pytest.param([100.1], float("nan"), True, id="commit-nan"),
        pytest.param([100.1], True, True, id="commit-bool"),
        pytest.param([100.1], "invalid-clock", True, id="commit-string"),
        pytest.param([100.1], float("inf"), True, id="commit-inf"),
    ],
)
def test_actual_retry_timestamp_fault_is_sticky_without_hiding_commit(
    monkeypatch, phase, entries, committed_at, committed
):
    async def run():
        async with Delivery(monkeypatch) as d:
            retry = TimestampRetryTransactions(monkeypatch, d, phase, entries, committed_at)
            try:
                d.start()
                result = await retry.release_terminal_or_fault(d)
                assert d.consumer.snapshot().acknowledgment == "not_attempted"
                assert d.worker.observation_failed
                assert isinstance(result, delegation.DelegationUnavailable)
                assert str(result) == "delegation_unavailable" and result.__suppress_context__
                snapshot = d.consumer.snapshot()
                assert snapshot.phase == "uncertain" and snapshot.lease_failed
                assert snapshot.terminal_observed is committed
                assert d.worker.terminal_committed is committed
                assert "acknowledge" not in d.names()
                assert not snapshot.runner.pending and not snapshot.renewal_active
                terminal = snapshot.terminal_progress
                assert terminal is not None and terminal.started_at == 100
                assert terminal.committed_at is None  # no invented/rejected time escapes
                records = [p for p in retry.observed if p.phase == phase]
                valid_entries = entries if committed else entries[:-1]
                assert [(p.started_at, p.committed_at) for p in records] == [
                    (100, None),
                    *((value, None) for value in valid_entries),
                ]
                assert d.worker.last_progress is records[-1]
                if committed:
                    assert not snapshot.outcome_unknown
                    assert d.harness.terminal == ("succeeded" if phase == "publish" else "failed")
                    assert d.harness.commits.count(phase) == 1
                    assert retry.attempts == len(entries) + 1
                    assert retry.samples[-1][0] == "commit"
                else:
                    assert d.harness.terminal is None and phase not in d.harness.commits
                    assert retry.attempts == len(entries)
                    assert d.harness.events == retry.after_backoff_events  # no new Session/SQL
                    assert all(kind == "entry" for kind, _ in retry.samples)
                before = (list(d.names()), list(d.harness.events))
                with pytest.raises(
                    delegation.DelegationUnavailable, match="^delegation_unavailable$"
                ):
                    await d.consumer.run_one()
                with pytest.raises(
                    text_module.ObservationUnavailable, match="^observation_unavailable$"
                ):
                    await d.worker.run_one(actor_id=d.harness.actor, job_id=d.harness.job)
                assert before == (d.names(), d.harness.events)
                assert_readonly(d.worker, "terminal_committed", False)
            finally:
                retry.release.set()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["publish", "finish"])
@pytest.mark.parametrize(
    "entries", [[100.1], [100.1, 100.2]], ids=["two-attempts", "three-attempts"]
)
def test_actual_retry_timestamp_healthy_attempts_keep_observer_and_ack(monkeypatch, phase, entries):
    async def run():
        async with Delivery(monkeypatch) as d:
            retry = TimestampRetryTransactions(monkeypatch, d, phase, entries, 109.0)
            try:
                d.start()
                assert await retry.release_terminal_or_fault(d) == (
                    "succeeded" if phase == "publish" else "failed"
                )
                records = [p for p in retry.observed if p.phase == phase]
                assert [(p.started_at, p.committed_at) for p in records] == [
                    (100, None),
                    *((value, None) for value in entries),
                    (entries[-1], 109),
                ]
                assert d.worker.last_progress is records[-1]
                terminal = d.worker.terminal_progress
                assert terminal is not None and (terminal.started_at, terminal.committed_at) == (
                    100,
                    109,
                )
                assert not d.worker.observation_failed
                snapshot = d.consumer.snapshot()
                assert snapshot.acknowledgment == "confirmed" and snapshot.phase == "settled"
                assert snapshot.terminal_observed and not snapshot.outcome_unknown
                assert retry.attempts == len(entries) + 1 and d.names().count("acknowledge") == 1
            finally:
                retry.release.set()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["publish", "finish"])
def test_actual_retry_timestamp_none_commit_truth_precedes_async_reply(monkeypatch, phase):
    async def run():
        reply_held, return_reply = asyncio.Event(), asyncio.Event()

        class DelayedReply(BoundedThreadRunner):
            async def __call__(self, operation, *args):
                result = await super().__call__(operation, *args)
                if getattr(operation, "__name__", None) == f"_{phase}":
                    reply_held.set()
                    await return_reply.wait()
                return result

        async with Delivery(monkeypatch, runner_factory=DelayedReply) as d:
            retry = TimestampRetryTransactions(monkeypatch, d, phase, [100.1], None)
            try:
                d.start()
                await until(lambda: retry.entered.is_set() or d.task.done())
                if d.task.done():
                    d.task.result()
                assert retry.entered.is_set()
                d.clock.now = 109
                retry.release.set()
                await asyncio.wait_for(reply_held.wait(), 2)
                assert not d.runner.snapshot().pending  # response held, NOT SQL pending
                snapshot = d.consumer.snapshot()
                assert snapshot.terminal_observed  # true BEFORE consumer receives worker result
                assert d.worker.observation_failed and d.worker.terminal_committed
                assert not snapshot.outcome_unknown and snapshot.acknowledgment == "not_attempted"
                assert snapshot.terminal_progress is not None
                assert (
                    snapshot.terminal_progress.committed_at is None
                )  # unknown timestamp, known exit
                assert [
                    (p.started_at, p.committed_at) for p in retry.observed if p.phase == phase
                ] == [(100, None), (100.1, None)]
                return_reply.set()
                with pytest.raises(
                    delegation.DelegationUnavailable, match="^delegation_unavailable$"
                ):
                    await d.task
                assert "acknowledge" not in d.names() and not d.consumer.snapshot().outcome_unknown
            finally:
                retry.release.set()
                return_reply.set()

    asyncio.run(run())


def test_actual_retry_timestamp_late_pulse_future_blocks_ack_before_clock_fault(monkeypatch):
    async def run():
        class HoldPublicationReply(BoundedThreadRunner):
            async def __call__(self, operation, *args):
                result = await super().__call__(operation, *args)
                if getattr(operation, "__name__", None) == "_publish":
                    # Publication is committed; wait for the REAL watcher to own
                    # the now-free runner, then let work cancel that watcher.
                    d.harness.hold_phase = "pulse"
                    d.harness.release.clear()
                    await until(d.harness.precommit.is_set)
                return result

        async with Delivery(monkeypatch, runner_factory=HoldPublicationReply) as d:
            # Permit the real late authorization transaction to reach context exit.
            # The old terminal result remains real and cannot authorize an ack yet.
            def late_pulse(*args, **kwargs):
                d.harness.phase = "pulse"
                return True

            monkeypatch.setattr(text_module, "heartbeat_job", late_pulse)

            def producer_clock():
                if (
                    d.harness.terminal is not None
                    and d.harness.phase == "pulse"
                    and d.harness.events[-1] == "session_exit"
                ):
                    return None
                return d.clock.now

            monkeypatch.setattr(text_module, "monotonic", producer_clock)
            d.worker = d.harness.worker(d.runner, None, heartbeat_seconds=0.01)
            d.consumer = d.scheduler.consumer(d.worker, d.clock)
            d.release_read.set()
            try:
                d.start()
                await until(lambda: d.task.done() or d.consumer.snapshot().phase == "acknowledging")
                assert d.task.done()  # never queue ack behind a cancelled watcher's Future
                assert await d.task == "lease_lost"
                snapshot = d.consumer.snapshot()
                assert snapshot.runner.pending and snapshot.terminal_observed
                assert snapshot.lease_failed and snapshot.outcome_unknown
                assert snapshot.phase == "uncertain" and snapshot.acknowledgment == "not_attempted"
                assert not snapshot.renewal_active and not d.worker.observation_failed
                assert d.harness.terminal == "succeeded" and d.names() == ["claim", "validate"]
                assert await d.consumer.run_one() == "busy"
                d.harness.release.set()
                await until(lambda: not d.runner.snapshot().pending)
                assert d.worker.observation_failed and d.worker.terminal_committed
                assert d.consumer.snapshot().terminal_observed
                with pytest.raises(
                    delegation.DelegationUnavailable, match="^delegation_unavailable$"
                ):
                    await d.consumer.run_one()
                assert d.names() == ["claim", "validate"]
            finally:
                d.harness.release.set()

    asyncio.run(run())
