"""S1 local ownership/observation only; synthetic transactions are not wire-COMMIT proof."""

import asyncio
import socket
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict
from hashlib import sha256
from time import monotonic
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import psycopg
import pytest
from app.documents.storage import ObjectScope, StorageError
from app.ingestion.text import chunks
from app.workers import text as text_module
from app.workers.delegation import (
    BoundedThreadRunner,
    DelegationRepository,
    DelegationUnavailable,
    ExactTextConsumer,
    JobExecutionGrant,
    SchedulerFactory,
)
from app.workers.text import Source, TextWorker
from sqlalchemy import Engine
from sqlalchemy.orm import Session


@pytest.fixture(autouse=True)
def deny_external_io(monkeypatch):
    attempts = []

    def deny(*args, **kwargs):
        attempts.append("external_io")
        raise AssertionError("unexpected_external_io")

    for target, names in (
        (socket.socket, ("connect", "connect_ex", "sendto")),
        (socket, ("getaddrinfo", "gethostbyname")),
        (subprocess, ("Popen",)),
        (psycopg, ("connect",)),
        (psycopg.Connection, ("connect",)),
        (psycopg.AsyncConnection, ("connect",)),
        (Engine, ("connect", "raw_connection")),
        (Session, ("connection", "execute")),
    ):
        for name in names:
            monkeypatch.setattr(target, name, deny)
    yield
    assert attempts == [], "unexpected I/O must fail even if production catches the sentinel"


class SyntheticTransactions:
    """Exercise actual TextWorker methods/_session, replacing only DB and lease helpers."""

    def __init__(self, monkeypatch):
        self.actor, self.job, self.token = (UUID(int=i) for i in (1, 2, 3))
        self.scope = ObjectScope(*(UUID(int=i) for i in (4, 5, 6, 7)))
        self.source = Source(self.scope, sha256(b"hello").hexdigest(), 5, "text/plain", False)
        self.phase: str | None = None
        self.hold_phase: str | None = None
        self.fail_phase: str | None = None
        self.precommit = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.events = []
        self.commits = []
        self.terminal = None
        self.claim_none = False
        self.read_failure = False
        self.reads = self.disposals = 0
        self.engine = SimpleNamespace(dispose=self.dispose)
        harness = self

        class Database:
            status = None

            def __enter__(self):
                harness.events.append("session_enter")
                return self

            def __exit__(self, *args):
                harness.events.append("session_exit")

            @contextmanager
            def begin(self):
                harness.events.append("transaction_enter")
                yield
                phase = harness.phase
                harness.events.append("precommit")
                if phase == harness.hold_phase:
                    harness.precommit.set()
                    assert harness.release.wait(3)
                if phase == harness.fail_phase:
                    raise RuntimeError("synthetic-secret-shaped://commit-credential")
                harness.commits.append(phase)
                if self.status is not None:
                    harness.terminal = self.status
                harness.events.append("commit")

            def execute(self, query, params=None):
                rendered = str(query)
                if "pg_advisory_xact_lock" in rendered:
                    harness.phase = "ingestion_claim"
                scalar = 0 if "count(*)" in rendered else True
                job = SimpleNamespace(
                    workspace_id=harness.scope.workspace_id,
                    notebook_id=harness.scope.notebook_id,
                    connection_id=None,
                    index_profile_id=None,
                )
                version = SimpleNamespace(
                    document_id=harness.scope.document_id,
                    id=harness.scope.version_id,
                    storage_key=harness.scope.key,
                    sha256=harness.source.sha256,
                    byte_count=5,
                    media_type="text/plain",
                )
                return SimpleNamespace(
                    scalar_one=lambda: scalar, one_or_none=lambda: (job, version)
                )

            def get(self, *args):
                return object()

            def add(self, chunk):
                harness.events.append("add")

            def flush(self):
                harness.events.append("flush")

        def claim(db, job, **kwargs):
            return None if self.claim_none else self.token

        def pulse(db, job, token, **kwargs):
            self.phase = "pulse"
            return self.terminal is None

        def finish(db, job, token, *, status, **kwargs):
            self.phase = "publish" if status == "succeeded" else "finish"
            self.events.append("finish_returning")
            db.status = status
            return True

        monkeypatch.setattr(text_module, "Session", lambda engine: Database())
        monkeypatch.setattr(text_module, "claim_job", claim)
        monkeypatch.setattr(text_module, "heartbeat_job", pulse)
        monkeypatch.setattr(text_module, "lock_job_for_publish", lambda *args: True)
        monkeypatch.setattr(text_module, "finish_job", finish)

    def dispose(self):
        self.disposals += 1

    async def read(self, actor_id, scope):
        self.reads += 1
        if self.read_failure:
            raise StorageError()
        return b"hello"

    def worker(self, runner, observer, *, heartbeat_seconds: float = 5):
        return TextWorker(
            cast(Engine, self.engine),
            operation_runner=runner,
            progress_observer=observer,
            store_factory=lambda auth: self,
            heartbeat_seconds=heartbeat_seconds,
        )

    def operation(self, worker, phase):
        if phase == "ingestion_claim":
            return worker._claim, (self.actor, self.job)
        if phase == "pulse":
            return worker._pulse, (self.actor, self.job, self.token)
        if phase == "publish":
            return worker._publish, (self.actor, self.job, self.token, self.source, chunks("hello"))
        return worker._finish, (self.actor, self.job, self.token, "failed", "synthetic_failure")

    def consumer(self, runner, worker):
        @contextmanager
        def unused():
            raise AssertionError("real scheduler transaction forbidden")
            yield

        repo = DelegationRepository(SchedulerFactory(unused, 1, 3), runner)
        repo.claim = AsyncMock(
            return_value=JobExecutionGrant(
                self.job,
                self.actor,
                self.scope.workspace_id,
                self.scope.notebook_id,
                self.scope.version_id,
                "text_extract_v1",
                self.token,
            )
        )
        repo.validate = AsyncMock(return_value="executable")
        repo.acknowledge = AsyncMock(return_value=True)
        consumer = ExactTextConsumer(repo, worker, pool_timeout=1, connect_timeout=3)
        return consumer, repo


def assert_readonly(target, name, value, error=AttributeError):
    with pytest.raises(error):
        setattr(target, name, value)


async def until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), 2)


async def join_runner(runner):
    assert await runner.close()
    # Test cleanup explicitly joins every owned executor thread, not just the waiter.
    runner._executor.shutdown(wait=True)


@pytest.mark.parametrize("failure", [False, True])
def test_runner_snapshot_tracks_actual_future_after_waiter_cancel(failure):
    async def run():
        runner = BoundedThreadRunner()
        entered, release = threading.Event(), threading.Event()
        calls = []
        tasks = []

        def operation():
            calls.append("first")
            entered.set()
            assert release.wait(3)
            if failure:
                raise RuntimeError("synthetic-secret-shaped://credential")
            return "private_result"

        try:
            initial = runner.snapshot()
            assert initial.sequence == 0 and not initial.pending and not initial.closed
            assert initial.phase == "idle"
            assert initial.submitted_at is initial.started_at is initial.completed_at is None
            task = asyncio.create_task(runner(operation))
            tasks.append(task)
            await until(entered.is_set)
            running = runner.snapshot()
            assert running.sequence == 1 and running.pending and running.phase == "running"
            assert running.submitted_at is not None and running.started_at is not None
            assert running.submitted_at <= running.started_at <= monotonic()
            assert running.completed_at is None
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            for _ in range(3):
                waiting = asyncio.create_task(runner(lambda: calls.append("forbidden")))
                tasks.append(waiting)
                await asyncio.sleep(0)
                waiting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiting
                assert runner.snapshot() == running
            before = monotonic()
            assert not await runner.close(timeout=0.02)
            assert monotonic() - before < 3
            closed = runner.snapshot()
            assert closed.closed and closed.pending and closed.phase == "closed"
            assert closed.generation == running.generation and closed.sequence == 1
            with pytest.raises(DelegationUnavailable, match="runner_closed"):
                await runner(lambda: calls.append("replacement"))
            release.set()
            assert await runner.close()
            settled = runner.snapshot()
            assert settled.closed and not settled.pending and settled.phase == "closed"
            assert settled.generation == closed.generation and settled.sequence == 1
            assert settled.submitted_at == running.submitted_at
            assert settled.started_at == running.started_at
            assert settled.completed_at is not None and settled.started_at is not None
            assert settled.completed_at >= settled.started_at
            assert calls == ["first"]
            assert set(asdict(settled)) == {
                "generation",
                "sequence",
                "closed",
                "pending",
                "phase",
                "submitted_at",
                "started_at",
                "completed_at",
            }
            assert "private" not in repr(settled) and "credential" not in repr(settled)
            assert_readonly(settled, "closed", False, FrozenInstanceError)
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await join_runner(runner)

    asyncio.run(run())


def test_runner_snapshot_distinguishes_submission_thread_entry_and_return(monkeypatch):
    async def run():
        runner = BoundedThreadRunner()
        admitted, enter = threading.Event(), threading.Event()
        original_submit = runner._executor.submit
        tasks = []

        def submit(function, *args):
            def held():
                admitted.set()
                assert enter.wait(3)
                return function(*args)

            return original_submit(held)

        monkeypatch.setattr(runner._executor, "submit", submit)
        try:
            task = asyncio.create_task(runner(lambda: None))
            tasks.append(task)
            await until(admitted.is_set)
            queued = runner.snapshot()
            assert queued.phase == "submitted" and queued.pending
            assert queued.submitted_at is not None
            assert queued.started_at is queued.completed_at is None
            enter.set()
            await task
            first = runner.snapshot()
            assert first.phase == "completed" and not first.pending
            assert first.submitted_at is not None and first.started_at is not None
            assert first.completed_at is not None
            assert first.submitted_at <= first.started_at <= first.completed_at
            assert await runner(lambda: 42) == 42
            second = runner.snapshot()
            assert second.generation == first.generation and second.sequence == 2
            assert second.submitted_at is not None
            assert second.submitted_at >= first.completed_at
            assert first.sequence == 1  # old immutable view is unchanged
        finally:
            enter.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await join_runner(runner)

    asyncio.run(run())


def test_runner_generation_is_not_reused_after_closed_lifetime():
    async def run():
        first = BoundedThreadRunner()
        try:
            old = first.snapshot()
        finally:
            await join_runner(first)
        second = BoundedThreadRunner()
        try:
            assert second.snapshot().generation > old.generation
            assert first.snapshot().closed
        finally:
            await join_runner(second)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["ingestion_claim", "pulse", "publish", "finish"])
@pytest.mark.parametrize("commit_failure", [False, True])
def test_phase_progress_requires_context_exit(monkeypatch, phase, commit_failure):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        runner = BoundedThreadRunner()
        observed = []
        harness.hold_phase = phase
        harness.fail_phase = phase if commit_failure else None
        harness.release.clear()
        task = None
        try:
            worker = harness.worker(runner, observed.append)
            operation, args = harness.operation(worker, phase)
            task = asyncio.create_task(runner(operation, *args))
            await until(harness.precommit.is_set)
            assert len(observed) == 1
            entry = worker.last_progress
            assert entry is not None
            assert entry == observed[0] and entry.phase == phase
            assert entry.committed_at is None
            assert runner.snapshot().pending
            assert "session_exit" not in harness.events
            if phase == "publish":
                assert "flush" in harness.events and "finish_returning" in harness.events
            harness.release.set()
            if commit_failure:
                with pytest.raises(RuntimeError, match="commit-credential"):
                    await task
                assert observed == [entry] and worker.last_progress == entry
                assert harness.terminal is None
            else:
                await task
                commit = worker.last_progress
                assert commit is not None and commit.committed_at is not None
                assert observed == [entry, commit]
                assert commit.started_at == entry.started_at
                assert commit.committed_at >= entry.started_at
                assert harness.events[-1] == "session_exit"
                assert set(asdict(commit)) == {"phase", "started_at", "committed_at"}
                assert_readonly(commit, "phase", "private-token", FrozenInstanceError)
            assert not worker.observation_failed
        finally:
            harness.release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await join_runner(runner)

    asyncio.run(run())


def test_claim_none_does_not_record_committed_ingestion_progress(monkeypatch):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        harness.claim_none = True
        runner = BoundedThreadRunner()
        observed = []
        try:
            worker = harness.worker(runner, observed.append)
            assert await worker.run_one(actor_id=harness.actor, job_id=harness.job) == "not_claimed"
            assert len(observed) == 1 and observed[0].committed_at is None
            assert harness.commits == ["ingestion_claim"] and harness.reads == 0
        finally:
            await join_runner(runner)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["ingestion_claim", "pulse", "publish", "finish"])
@pytest.mark.parametrize("at_commit", [False, True])
@pytest.mark.parametrize("fault", ["exception", "cancelled", "return", "awaitable"])
def test_observer_fault_is_sticky_sanitized_and_preserves_terminal_truth(
    monkeypatch,
    phase,
    at_commit,
    fault,
):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        runner = BoundedThreadRunner()
        calls, coroutines = [], []
        observer_thread = []
        loop_thread = threading.get_ident()

        async def forbidden_await():
            raise AssertionError("observer must not be awaited")

        def observe(progress):
            observer_thread.append(threading.get_ident())
            assert worker.last_progress == progress
            calls.append(progress)
            if (progress.committed_at is not None) == at_commit:
                if fault == "exception":
                    raise RuntimeError("synthetic-secret-shaped://observer-credential")
                if fault == "cancelled":
                    raise asyncio.CancelledError("synthetic-secret-shaped://observer-credential")
                if fault == "awaitable":
                    coroutine = forbidden_await()
                    coroutines.append(coroutine)
                    return coroutine
                return "synthetic-secret-shaped://observer-credential"
            return None

        try:
            worker = harness.worker(runner, observe)
            operation, args = harness.operation(worker, phase)
            terminal_commit = phase in ("publish", "finish") and at_commit
            if terminal_commit:
                result = await runner(operation, *args)
                assert result is (True if phase == "finish" else None)
                assert harness.terminal == ("succeeded" if phase == "publish" else "failed")
            else:
                with pytest.raises(text_module.ObservationUnavailable) as caught:
                    await runner(operation, *args)
                assert str(caught.value) == "observation_unavailable"
                assert caught.value.__suppress_context__
                assert harness.terminal is None
            assert worker.observation_failed
            assert worker.last_progress == calls[-1]
            assert len(calls) == (2 if at_commit else 1)
            assert all(thread != loop_thread for thread in observer_thread)
            assert all(coroutine.cr_frame is None for coroutine in coroutines)
            assert "credential" not in repr(worker.last_progress)
            assert_readonly(worker, "observation_failed", False)
            assert_readonly(worker, "last_progress", None)
            before = len(harness.events)
            with pytest.raises(text_module.ObservationUnavailable, match="observation_unavailable"):
                await worker.run_one(actor_id=harness.actor, job_id=harness.job)
            assert len(harness.events) == before and harness.reads == 0
            if not at_commit:
                assert harness.events == []  # observer entry failure precedes even session open
        finally:
            for coroutine in coroutines:
                coroutine.close()
            await join_runner(runner)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["publish", "finish"])
def test_consumer_observer_fault_blocks_ack_and_next_claim_without_relabeling_commit(
    monkeypatch,
    phase,
):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        harness.read_failure = phase == "finish"
        runner = BoundedThreadRunner()
        worker_results = []

        def observe(progress):
            if progress.phase == phase and progress.committed_at is not None:
                raise RuntimeError("synthetic-secret-shaped://terminal-observer")

        try:
            worker = harness.worker(runner, observe)
            actual_run = worker.run_one

            async def record_result(**kwargs):
                result = await actual_run(**kwargs)
                worker_results.append(result)
                return result

            monkeypatch.setattr(worker, "run_one", record_result)
            consumer, repo = harness.consumer(runner, worker)
            with pytest.raises(DelegationUnavailable, match="^delegation_unavailable$"):
                await consumer.run_one()
            assert worker_results == ["succeeded" if phase == "publish" else "failed"]
            assert harness.terminal == worker_results[0]
            terminal_progress = worker.last_progress
            assert terminal_progress is not None and terminal_progress.phase == phase
            assert terminal_progress.committed_at is not None and worker.observation_failed
            cast(AsyncMock, repo.acknowledge).assert_not_awaited()
            with pytest.raises(DelegationUnavailable, match="^delegation_unavailable$"):
                await consumer.run_one()
            cast(AsyncMock, repo.claim).assert_awaited_once()
            assert harness.events.count("finish_returning") == 1
        finally:
            await join_runner(runner)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["ingestion_claim", "pulse"])
def test_nonterminal_observer_fault_prevents_storage_and_terminal_writes(monkeypatch, phase):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        runner = BoundedThreadRunner()

        def observe(progress):
            if progress.phase == phase and progress.committed_at is not None:
                raise asyncio.CancelledError("synthetic-secret-shaped://not-task-cancel")

        try:
            worker = harness.worker(runner, observe)
            consumer, repo = harness.consumer(runner, worker)
            with pytest.raises(DelegationUnavailable, match="^delegation_unavailable$"):
                await consumer.run_one()
            assert harness.reads == 0 and harness.terminal is None
            assert "finish_returning" not in harness.events
            assert phase in harness.commits
            cast(AsyncMock, repo.acknowledge).assert_not_awaited()
        finally:
            await join_runner(runner)

    asyncio.run(run())


@pytest.mark.parametrize("failure", [False, True])
def test_nondelegated_identity_path_has_no_observation_and_retains_result(monkeypatch, failure):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        harness.read_failure = failure
        worker = harness.worker(None, None)
        assert await worker.run_one(actor_id=harness.actor, job_id=harness.job) == (
            "failed" if failure else "succeeded"
        )
        assert worker.last_progress is None and not worker.observation_failed
        assert harness.events.count("finish_returning") == 1
        # asyncio.run joins its default executor after this coroutine returns.

    asyncio.run(run())


@pytest.mark.parametrize("invalid", ["noncallable", "async", "async_callable", "no_runner"])
def test_observer_configuration_rejects_non_synchronous_or_nondelegated_use(monkeypatch, invalid):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        runner = BoundedThreadRunner()

        async def async_observer(progress):
            pass

        class AsyncObserver:
            async def __call__(self, progress):
                pass

        try:
            observer = {
                "noncallable": object(),
                "async": async_observer,
                "async_callable": AsyncObserver(),
                "no_runner": lambda progress: None,
            }[invalid]
            with pytest.raises(ValueError, match="^invalid_progress_observer$"):
                harness.worker(None if invalid == "no_runner" else runner, observer)
            assert harness.events == []
        finally:
            await join_runner(runner)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["publish", "finish"])
def test_shutdown_reports_unresolved_thread_without_replacement(monkeypatch, phase):
    async def run():
        from app.workers import delegation as delegation_module

        harness = SyntheticTransactions(monkeypatch)
        harness.read_failure = phase == "finish"
        harness.hold_phase = phase
        harness.release.clear()
        runner = BoundedThreadRunner()
        task = None
        observed = []

        def no_replacement(*args, **kwargs):
            raise AssertionError("replacement_executor_forbidden")

        monkeypatch.setattr(delegation_module, "ThreadPoolExecutor", no_replacement)
        try:
            worker = harness.worker(runner, observed.append)
            consumer, repo = harness.consumer(runner, worker)
            task = asyncio.create_task(consumer.run_one())
            await until(harness.precommit.is_set)
            entry = worker.last_progress
            assert entry is not None and entry.phase == phase and entry.committed_at is None
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            cast(AsyncMock, repo.acknowledge).assert_not_awaited()
            assert harness.events.count("finish_returning") == 1
            assert harness.terminal is None and harness.disposals == 0
            before = monotonic()
            assert not await runner.close(timeout=0.02)
            assert monotonic() - before < 3
            pending = runner.snapshot()
            assert pending.closed and pending.pending and pending.completed_at is None
            with pytest.raises(DelegationUnavailable, match="runner_closed"):
                await runner(lambda: pytest.fail("new operation after stop"))
            assert worker.last_progress == entry
            harness.release.set()
            assert await runner.close()
            settled = runner.snapshot()
            assert settled.closed and not settled.pending
            assert settled.generation == pending.generation and settled.sequence == pending.sequence
            assert worker.last_progress is not None
            assert worker.last_progress.committed_at is not None
            assert harness.terminal == ("succeeded" if phase == "publish" else "failed")
            cast(AsyncMock, repo.acknowledge).assert_not_awaited()
            assert harness.events.count("finish_returning") == 1 and harness.disposals == 0
        finally:
            harness.release.set()
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await join_runner(runner)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["publish", "finish"])
def test_watcher_failure_after_commit_before_terminal_return_preserves_progress(monkeypatch, phase):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        harness.read_failure = phase == "finish"
        committed, return_terminal, watcher_lost = asyncio.Event(), asyncio.Event(), asyncio.Event()
        observed = []

        class DelayedResultRunner(BoundedThreadRunner):
            async def __call__(self, operation, *args):
                try:
                    result = await super().__call__(operation, *args)
                except text_module.LeaseLost:
                    watcher_lost.set()
                    raise
                if operation.__name__ == f"_{phase}":
                    committed.set()
                    await return_terminal.wait()
                return result

        runner = DelayedResultRunner()
        task = None
        try:
            worker = harness.worker(runner, observed.append, heartbeat_seconds=0.001)
            consumer, repo = harness.consumer(runner, worker)
            task = asyncio.create_task(consumer.run_one())
            await asyncio.wait_for(committed.wait(), 2)
            terminal = worker.last_progress
            assert terminal is not None and terminal.phase == phase
            assert terminal.committed_at is not None
            await asyncio.wait_for(watcher_lost.wait(), 2)
            assert not task.done()
            assert worker.last_progress is terminal and observed[-1] is terminal
            assert not runner.snapshot().pending
            cast(AsyncMock, repo.acknowledge).assert_not_awaited()
            return_terminal.set()
            assert await task == ("succeeded" if phase == "publish" else "failed")
            assert worker.last_progress is terminal
            assert harness.events.count("finish_returning") == 1
            cast(AsyncMock, repo.acknowledge).assert_awaited_once()
        finally:
            return_terminal.set()
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await join_runner(runner)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["publish", "finish"])
@pytest.mark.parametrize("pulse_success", [False, True])
def test_late_pulse_cannot_erase_terminal_progress_but_new_claim_entry_can(
    monkeypatch,
    phase,
    pulse_success,
):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        runner = BoundedThreadRunner()
        observed = []
        task = None
        try:
            worker = harness.worker(runner, observed.append)
            operation, args = harness.operation(worker, phase)
            await runner(operation, *args)
            terminal = worker.last_progress
            assert terminal is not None and terminal.committed_at is not None
            # Countermodel: observation must not reinterpret either real helper outcome.
            monkeypatch.setattr(text_module, "heartbeat_job", lambda *args, **kwargs: pulse_success)
            if pulse_success:
                await runner(worker._pulse, harness.actor, harness.job, harness.token)
            else:
                with pytest.raises(text_module.LeaseLost):
                    await runner(worker._pulse, harness.actor, harness.job, harness.token)
            assert worker.last_progress is terminal and observed[-1] is terminal
            assert len(observed) == 2
            harness.hold_phase = "ingestion_claim"
            harness.release.clear()
            task = asyncio.create_task(runner(worker._claim, harness.actor, harness.job))
            await until(harness.precommit.is_set)
            entry = worker.last_progress
            assert entry is not None and entry.phase == "ingestion_claim"
            assert entry.committed_at is None and entry.started_at >= terminal.committed_at
            harness.release.set()
            assert await task is not None
            fresh = worker.last_progress
            assert fresh is not None and fresh.phase == "ingestion_claim"
            assert fresh.started_at == entry.started_at and fresh.committed_at is not None
            assert fresh.committed_at >= fresh.started_at
        finally:
            harness.release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await join_runner(runner)

    asyncio.run(run())


def test_nondelegated_external_cancellation_retains_original_finish_boundary(monkeypatch):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        entered = asyncio.Event()

        async def read(actor, scope):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(harness, "read", read)
        worker = harness.worker(None, None)
        task = asyncio.create_task(worker.run_one(actor_id=harness.actor, job_id=harness.job))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert harness.terminal == "cancelled"
            assert harness.events.count("finish_returning") == 1
            assert worker.last_progress is None and not worker.observation_failed
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("failure", [False, True])
def test_callback_completion_timestamp_does_not_claim_future_quiescence(monkeypatch, failure):
    async def run():
        runner = BoundedThreadRunner()
        returned, release = threading.Event(), threading.Event()
        submit = runner._executor.submit
        task = None

        def submit_held(function, *args):
            def held():
                try:
                    return function(*args)
                finally:
                    returned.set()
                    assert release.wait(3)

            return submit(held)

        def operation():
            if failure:
                raise RuntimeError("synthetic-secret-shaped://operation-failure")

        monkeypatch.setattr(runner._executor, "submit", submit_held)
        try:
            task = asyncio.create_task(runner(operation))
            await until(returned.is_set)
            finished = runner.snapshot()
            assert finished.completed_at is not None and finished.phase == "completed"
            assert finished.pending  # the real Future has NOT settled
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not await runner.close(timeout=0.02)
            assert runner.snapshot().pending
            release.set()
            assert await runner.close()
            assert not runner.snapshot().pending
            assert runner.snapshot().completed_at == finished.completed_at
        finally:
            release.set()
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await join_runner(runner)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["ingestion_claim", "pulse", "publish", "finish"])
def test_commit_exit_failure_is_sanitized_by_consumer_without_commit_progress(monkeypatch, phase):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        harness.read_failure = phase == "finish"
        harness.fail_phase = phase
        runner = BoundedThreadRunner()
        observed = []
        try:
            worker = harness.worker(runner, observed.append)
            consumer, repo = harness.consumer(runner, worker)
            with pytest.raises(DelegationUnavailable) as caught:
                await consumer.run_one()
            assert str(caught.value) == "delegation_unavailable"
            assert caught.value.__suppress_context__
            progress = worker.last_progress
            assert progress is not None and progress.phase == phase
            assert progress.committed_at is None
            assert all(p.committed_at is None for p in observed if p.phase == phase)
            assert not worker.observation_failed  # DB failure is not an observer failure
            assert harness.terminal is None
            cast(AsyncMock, repo.acknowledge).assert_not_awaited()
            assert phase not in harness.commits
        finally:
            await join_runner(runner)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["publish", "finish"])
@pytest.mark.parametrize("observer_fault", [False, True])
@pytest.mark.parametrize("future_held_after_callback", [False, True])
def test_consumer_pending_future_blocks_claim_after_cancel_and_late_observer_fault(
    monkeypatch,
    phase,
    observer_fault,
    future_held_after_callback,
):
    async def run():
        harness = SyntheticTransactions(monkeypatch)
        harness.read_failure = phase == "finish"
        harness.hold_phase = None if future_held_after_callback else phase
        harness.release.clear()
        runner = BoundedThreadRunner()
        returned = threading.Event()
        submit = runner._executor.submit
        tasks = []

        def submit_held(function, *args):
            def held():
                try:
                    return function(*args)
                finally:
                    if args[0].__name__ == f"_{phase}":
                        returned.set()
                        assert harness.release.wait(3)

            return submit(held)

        if future_held_after_callback:
            monkeypatch.setattr(runner._executor, "submit", submit_held)

        def observe(progress):
            if observer_fault and progress.phase == phase and progress.committed_at is not None:
                raise RuntimeError("synthetic-secret-shaped://late-observer-fault")

        try:
            worker = harness.worker(runner, observe)
            consumer, repo = harness.consumer(runner, worker)
            task = asyncio.create_task(consumer.run_one())
            tasks.append(task)
            await until(returned.is_set if future_held_after_callback else harness.precommit.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            pending = runner.snapshot()
            assert pending.pending
            assert (pending.completed_at is not None) == future_held_after_callback
            original_events = list(harness.events)
            for _ in range(3):
                next_cycle = asyncio.create_task(consumer.run_one())
                tasks.append(next_cycle)
                await asyncio.sleep(0)
                assert next_cycle.done(), "pending real work must not enqueue a new claim waiter"
                assert await next_cycle == "busy"
                next_cycle.cancel()  # even repeated cancelled callers admit no real work
            cast(AsyncMock, repo.claim).assert_awaited_once()
            cast(AsyncMock, repo.validate).assert_awaited_once()
            cast(AsyncMock, repo.acknowledge).assert_not_awaited()
            assert harness.events == original_events and harness.reads == 1
            harness.release.set()
            await until(lambda: not runner.snapshot().pending)
            terminal = worker.last_progress
            assert terminal is not None and terminal.phase == phase
            assert terminal.committed_at is not None
            assert harness.terminal == ("succeeded" if phase == "publish" else "failed")
            if observer_fault:
                with pytest.raises(DelegationUnavailable, match="^delegation_unavailable$"):
                    await consumer.run_one()
                cast(AsyncMock, repo.claim).assert_awaited_once()
                assert worker.last_progress is terminal and worker.observation_failed
            else:
                cast(AsyncMock, repo.claim).return_value = None
                assert await consumer.run_one() == "idle"
                assert cast(AsyncMock, repo.claim).await_count == 2
            cast(AsyncMock, repo.acknowledge).assert_not_awaited()
            assert harness.events.count("finish_returning") == 1 and harness.disposals == 0
        finally:
            harness.release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await join_runner(runner)

    asyncio.run(run())
