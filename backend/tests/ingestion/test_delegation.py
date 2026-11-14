"""No database fixtures or external I/O: exact delegation and cancellation mechanics."""

import asyncio
import threading
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.documents.storage import ObjectScope, StorageError
from app.workers.delegation import (
    BoundedThreadRunner,
    DelegationRepository,
    DelegationUnavailable,
    ExactTextConsumer,
    JobExecutionGrant,
    SchedulerFactory,
)
from app.workers.text import LeaseLost, Source, TextWorker
from sqlalchemy import Engine


def grant():
    return JobExecutionGrant(
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4(), "text_extract_v1", uuid4()
    )


@contextmanager
def unused_factory():
    raise AssertionError("unit test must not open database")
    yield  # pragma: no cover


def repository(runner):
    return DelegationRepository(SchedulerFactory(unused_factory, 1, 3), runner)


async def until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), 2)


class SyntheticWorker(TextWorker):
    def __init__(self, runner=None):
        super().__init__(
            cast(Engine, object()),
            operation_runner=runner,
            heartbeat_seconds=0.01,
            store_factory=lambda auth: self,
        )
        self.events = []
        self.publish_entered = threading.Event()
        self.publish_release = threading.Event()
        self.publish_release.set()
        self.finish_entered = threading.Event()
        self.finish_release = threading.Event()
        self.finish_release.set()
        self.finished = False
        self.fail_read = False
        self.committed = False
        self.source = Source(
            ObjectScope(*(uuid4() for _ in range(4))),
            sha256(b"hello").hexdigest(),
            5,
            "text/plain",
            False,
        )
        self.loop_thread = threading.get_ident()

    def record(self, name):
        if self.operation_runner is not None:
            assert threading.get_ident() != self.loop_thread
        self.events.append(name)

    def _claim(self, actor, job_id):
        self.record("claim")
        return uuid4(), self.source

    def _pulse(self, actor, job, token):
        self.record("pulse")
        if self.committed:
            raise LeaseLost()

    def _publish(self, actor, job, token, source, parts):
        self.record("publish")
        self.publish_entered.set()
        assert self.publish_release.wait(3)
        self.committed = True

    def _finish(self, actor, job, token, status, code):
        self.record("finish")
        self.finish_entered.set()
        assert self.finish_release.wait(3)
        self.finished = True
        return True

    async def read(self, actor_id, scope) -> bytes:
        self.events.append("read")
        if self.fail_read:
            raise StorageError()
        return b"hello"


def consumer(repo, worker):
    return ExactTextConsumer(repo, worker, pool_timeout=1, connect_timeout=3)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pool_timeout": 0},
        {"connect_timeout": 4},
        {"pool_timeout": float("nan")},
        {"pool_timeout": True},
    ],
)
def test_factory_requires_finite_real_connection_budgets(kwargs):
    with pytest.raises(ValueError):
        SchedulerFactory(unused_factory, **({"pool_timeout": 1, "connect_timeout": 3} | kwargs))


@pytest.mark.parametrize("seconds", [0, 301, True, 1.5, None])
def test_repository_rejects_unbounded_duration_before_io(seconds):
    async def run():
        runner = BoundedThreadRunner()
        repo = repository(runner)
        try:
            with pytest.raises(ValueError):
                await repo.claim(seconds=seconds)
            with pytest.raises(ValueError):
                await repo.renew(grant(), seconds=seconds)
        finally:
            assert await runner.close()

    asyncio.run(run())


def test_dataclass_not_authority_and_exact_grant_fresh_validation():
    async def run():
        runner = BoundedThreadRunner()
        repo, worker = repository(runner), SyntheticWorker(runner)
        value = grant()
        repo.claim = AsyncMock(return_value=value)
        repo.validate = AsyncMock(return_value=None)
        repo.acknowledge = AsyncMock()
        try:
            assert await consumer(repo, worker).run_one() == "not_claimed"
            repo.validate.assert_awaited_once_with(value)
            repo.acknowledge.assert_not_awaited()
            assert worker.events == []
            for invalid in (
                object(),
                replace(value, purpose="embedding"),
                replace(value, actor_user_id="not-uuid"),
            ):
                with pytest.raises(DelegationUnavailable, match="invalid_grant"):
                    await DelegationRepository.validate(repo, cast(JobExecutionGrant, invalid))
        finally:
            assert await runner.close()

    asyncio.run(run())


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
def test_terminal_crash_reconciliation_has_no_storage_or_worker_call(terminal):
    async def run():
        runner = BoundedThreadRunner()
        repo, worker = repository(runner), SyntheticWorker(runner)
        repo.claim = AsyncMock(return_value=grant())
        repo.validate = AsyncMock(return_value=terminal)
        repo.acknowledge = AsyncMock(return_value=True)
        try:
            assert await consumer(repo, worker).run_one() == terminal
            assert worker.events == []
            repo.acknowledge.assert_awaited_once()
        finally:
            assert await runner.close()

    asyncio.run(run())


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_text_worker_default_and_optional_execution_paths(delegated, failure):
    async def run():
        runner = BoundedThreadRunner() if delegated else None
        worker = SyntheticWorker(runner)
        worker.fail_read = failure
        try:
            assert await worker.run_one(actor_id=uuid4(), job_id=uuid4()) == (
                "failed" if failure else "succeeded"
            )
            assert worker.events.count("read") == 1
            assert worker.events.count("finish" if failure else "publish") == 1
        finally:
            if runner:
                assert await runner.close()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["publish", "finish"])
def test_cancel_during_terminal_write_never_starts_conflicting_finish_or_ack(phase):
    async def run():
        runner = BoundedThreadRunner()
        repo, worker = repository(runner), SyntheticWorker(runner)
        repo.claim = AsyncMock(return_value=grant())
        repo.validate = AsyncMock(return_value="executable")
        repo.acknowledge = AsyncMock(return_value=True)
        entered = worker.publish_entered if phase == "publish" else worker.finish_entered
        release = worker.publish_release if phase == "publish" else worker.finish_release
        worker.fail_read = phase == "finish"
        release.clear()
        task = asyncio.create_task(consumer(repo, worker).run_one())
        try:
            await until(entered.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            repo.acknowledge.assert_not_awaited()
            assert worker.events.count("finish") == (1 if phase == "finish" else 0)
            assert not worker.committed and not worker.finished
            assert not await runner.close(timeout=0.01)
            with pytest.raises(DelegationUnavailable, match="runner_closed"):
                await runner(lambda: None)
            release.set()
            assert await runner.close()
            assert worker.committed or worker.finished  # cancellation did not prove rollback!
        finally:
            release.set()
            await runner.close()

    asyncio.run(run())


def test_watcher_failure_after_commit_before_publish_return_does_not_report_lease_loss():
    async def run():
        published, return_publish, watcher_lost = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class DelayedResultRunner(BoundedThreadRunner):
            async def __call__(self, operation, *args):
                try:
                    result = await super().__call__(operation, *args)
                except LeaseLost:
                    watcher_lost.set()
                    raise
                if operation.__name__ == "_publish":
                    published.set()
                    await return_publish.wait()
                return result

        runner = DelayedResultRunner()
        worker = SyntheticWorker(runner)
        task = asyncio.create_task(worker.run_one(actor_id=uuid4(), job_id=uuid4()))
        try:
            await asyncio.wait_for(published.wait(), 2)
            await asyncio.wait_for(watcher_lost.wait(), 2)
            await asyncio.sleep(0)
            assert not task.done()
            return_publish.set()
            assert await task == "succeeded"
            assert "finish" not in worker.events
        finally:
            return_publish.set()
            await runner.close()

    asyncio.run(run())


def test_actual_outstanding_thread_bound_survives_cancelled_waiters():
    async def run():
        runner = BoundedThreadRunner()
        entered, release = threading.Event(), threading.Event()
        second_started = threading.Event()

        def first():
            entered.set()
            assert release.wait(3)

        task = asyncio.create_task(runner(first))
        try:
            await until(entered.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # Cancellation while waiting for an unresolved previous operation must
            # not clear its fence or enqueue another underlying executor operation.
            waiting = asyncio.create_task(runner(second_started.set))
            await asyncio.sleep(0.01)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            next_task = asyncio.create_task(runner(second_started.set))
            await asyncio.sleep(0.01)
            assert not second_started.is_set()
            release.set()
            await next_task
            assert second_started.is_set()
        finally:
            release.set()
            assert await runner.close()

    asyncio.run(run())


def test_single_outstanding_delivery_and_sanitized_errors():
    async def run():
        runner = BoundedThreadRunner()
        repo, worker = repository(runner), SyntheticWorker(runner)
        started, release = asyncio.Event(), asyncio.Event()

        async def claim(*, seconds=60):
            started.set()
            await release.wait()
            raise RuntimeError("private driver detail")

        repo.claim = claim
        one = consumer(repo, worker)
        task = asyncio.create_task(one.run_one())
        try:
            await started.wait()
            assert await one.run_one() == "busy"
            release.set()
            with pytest.raises(DelegationUnavailable) as caught:
                await task
            assert str(caught.value) == "delegation_unavailable"
            assert caught.value.__suppress_context__
        finally:
            release.set()
            await runner.close()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["ok", "unsafe", "commit_failure"])
def test_repository_fresh_direct_principal_transaction_closes_before_worker(mode):
    from unittest.mock import MagicMock

    async def run():
        runner = BoundedThreadRunner()
        value = grant()
        active, queries = [], []
        main_thread = threading.get_ident()

        def execute(query, params=None):
            assert threading.get_ident() != main_thread
            rendered = str(query)
            queries.append(rendered)
            result = MagicMock()
            if "current_user=session_user" in rendered:
                result.scalar_one.return_value = mode != "unsafe"
            elif "transaction_isolation" in rendered:
                result.scalar_one.return_value = "read committed"
            elif "claim_text_job" in rendered:
                from dataclasses import asdict

                result.mappings.return_value.one_or_none.return_value = asdict(value)
            elif "validate_text_job" in rendered:
                result.scalar_one.return_value = "executable"
                assert params is not None and params["delivery_token"] == value.delivery_token
            elif "ack_text_job" in rendered:
                result.scalar_one.return_value = True
            return result

        @contextmanager
        def transaction():
            active.append(True)
            try:
                db = MagicMock()
                db.execute.side_effect = execute
                yield db
                if mode == "commit_failure":
                    raise RuntimeError("private commit failure")
            finally:
                active.pop()

        repo = DelegationRepository(SchedulerFactory(transaction, 1, 3), runner)
        worker = SyntheticWorker(runner)

        async def execute_worker(*, actor_id, job_id):
            assert not active
            assert actor_id == value.actor_user_id and job_id == value.job_id
            return "succeeded"

        worker.run_one = AsyncMock(side_effect=execute_worker)
        try:
            one = consumer(repo, worker)
            if mode == "ok":
                assert await one.run_one() == "succeeded"
                worker.run_one.assert_awaited_once()
                assert sum("current_user=session_user" in query for query in queries) == 3
            else:
                with pytest.raises(DelegationUnavailable, match="delegation_unavailable"):
                    await one.run_one()
                worker.run_one.assert_not_awaited()
            assert not active
        finally:
            assert await runner.close()

    asyncio.run(run())
