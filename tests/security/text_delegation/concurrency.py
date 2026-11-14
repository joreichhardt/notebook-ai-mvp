"""Packet-only fixed SQL contention probe; not concurrent repository coverage."""

import asyncio
from dataclasses import dataclass, field
from threading import Barrier
from typing import Any

from app.db.quota import classify_quota_error
from app.workers.delegation import BoundedThreadRunner, JobExecutionGrant
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def contender_diagnostic(index):
    return {
        "index": index,
        "outcome": "failed",
        "failure_stage": "not_started",
        "transaction": "not_started",
        **{
            key: False
            for key in (
                "connection_opened",
                "connection_closed",
                "engine_disposed",
                "runner_closed",
                "principal_verified",
                "read_committed",
                "pid_observed",
                "barrier_released",
                "claim_called",
            )
        },
    }


def empty_diagnostic():
    return {
        "version": 1,
        "failure": "fixture",
        "pid_count": 0,
        "distinct_pid_count": 0,
        "grant_count": 0,
        "fence_matches": False,
        "final_executable": False,
        "contenders": [contender_diagnostic(i) for i in range(2)],
    }


@dataclass
class ClaimResult:
    diagnostic: dict[str, Any]
    # Capabilities and actual backend IDs stay in memory, never in packet evidence.
    pid: int | None = field(default=None, repr=False)
    grant: JobExecutionGrant | None = field(default=None, repr=False)


def direct_claim(index, engine_factory, barrier):
    """Own one engine/connection/whole transaction; publish only after cleanup returns."""
    d = contender_diagnostic(index)
    result = ClaimResult(d)
    engine = connection = transaction = None
    stage, failure, outcome = "engine", "none", "failed"
    rollback_attempted = False
    try:
        engine = engine_factory()
        stage = "connect"
        connection = engine.connect()
        d["connection_opened"] = True
        stage = "begin"
        transaction = connection.begin()
        d["transaction"] = "unknown"
        stage = "principal"
        assert (
            connection.execute(
                text(
                    "SELECT current_user=session_user AND current_user='notebook_text_scheduler' "
                    "AND NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
            ).scalar_one()
            is True
        )
        d["principal_verified"] = True
        stage = "bounds"
        connection.execute(text("SET LOCAL statement_timeout='3s'"))
        connection.execute(text("SET LOCAL lock_timeout='1s'"))
        stage = "isolation"
        assert (
            connection.execute(text("SHOW transaction_isolation")).scalar_one() == "read committed"
        )
        d["read_committed"] = True
        stage = "pid"
        pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
        assert type(pid) is int and pid > 0
        result.pid, d["pid_observed"] = pid, True
        stage = "barrier"
        barrier.wait()  # both independent connections have an open transaction
        d["barrier_released"] = True
        stage = "claim"
        d["claim_called"] = True
        try:
            row = connection.execute(
                text("SELECT * FROM public.claim_text_job(:seconds)"), {"seconds": 60}
            )
        except DBAPIError as error:
            if classify_quota_error(error) != "quota_gate_busy":
                raise
            outcome = "busy"
        else:
            row = row.mappings().one_or_none()
            result.grant = JobExecutionGrant(**row) if row is not None else None
            outcome = "grant" if result.grant is not None else "idle"
        if outcome == "busy":
            stage = "rollback"
            rollback_attempted = True
            transaction.rollback()
            d["transaction"] = "rolled_back"
        else:
            stage = "commit"
            transaction.commit()
            d["transaction"] = "committed"
    except Exception:  # noqa: BLE001 - fixed stage only, never raw driver details
        failure = stage
    finally:
        if transaction is not None and d["transaction"] == "unknown" and not rollback_attempted:
            try:
                transaction.rollback()
                # A failed COMMIT remains unknown even if subsequent cleanup returns.
                if failure != "commit":
                    d["transaction"] = "rolled_back"
            except Exception:  # noqa: BLE001
                failure = failure if failure != "none" else "rollback"
        if connection is not None:
            try:
                connection.close()
                d["connection_closed"] = True
            except Exception:  # noqa: BLE001
                failure = failure if failure != "none" else "close"
        if engine is not None:
            try:
                engine.dispose()
                d["engine_disposed"] = True
            except Exception:  # noqa: BLE001
                failure = failure if failure != "none" else "dispose"
    d["failure_stage"] = failure
    d["outcome"] = outcome if failure == "none" else "failed"
    return result


async def collect_claims(
    engine_factory,
    job_id,
    read_fence,
    validate,
    *,
    barrier=None,
    runner_factory=BoundedThreadRunner,
    timeout=12,
):
    """One shared collection deadline, then concurrent bounded runner shutdown.

    Timeout is failure, not thread termination. A live DB operation may outlast this
    waiter and process exit; only the outer packet process/container bounds it.
    Unreturned diagnostics are unknown, never snapshots of mutable worker state.
    """
    d = empty_diagnostic()
    barrier = barrier if barrier is not None else Barrier(2, timeout=5)
    runners = [runner_factory(), runner_factory()]
    tasks = [
        asyncio.create_task(runner(direct_claim, i, lambda i=i: engine_factory(i), barrier))
        for i, runner in enumerate(runners)
    ]
    results = [ClaimResult(contender_diagnostic(i)) for i in range(2)]
    failure = "none"
    try:
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            failure = "collection"
        for i, task in enumerate(tasks):
            if task.done():
                try:
                    result = task.result()
                    assert isinstance(result, ClaimResult)
                    results[i] = result
                except Exception:  # noqa: BLE001
                    failure = failure if failure != "none" else "contender"
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()  # cancel waiters, NOT the underlying threads/transactions
        await asyncio.gather(*tasks, return_exceptions=True)
        closed = await asyncio.gather(
            *(runner.close(timeout=3) for runner in runners), return_exceptions=True
        )
        for result, status in zip(results, closed, strict=True):
            result.diagnostic["runner_closed"] = status is True
    d["contenders"] = [r.diagnostic for r in results]
    pids = [r.pid for r in results if r.pid is not None]
    live = [r.grant for r in results if r.diagnostic["outcome"] == "grant" and r.grant is not None]
    d.update(pid_count=len(pids), distinct_pid_count=len(set(pids)), grant_count=len(live))
    if failure == "none":
        if not all(r.diagnostic["runner_closed"] for r in results):
            failure = "runner_close"
        elif any(r.diagnostic["outcome"] == "failed" for r in results):
            failure = "contender"
        elif len(pids) != 2 or len(set(pids)) != 2:
            failure = "pid"
        elif sorted(r.diagnostic["outcome"] for r in results) not in (
            ["grant", "idle"],
            ["busy", "grant"],
        ):
            failure = "grants"
        elif live[0].job_id != job_id:
            failure = "job"
        else:
            failure = "fence"
            try:
                d["fence_matches"] = read_fence(job_id) == (live[0].delivery_token,)
                if d["fence_matches"]:
                    failure = "validate"
                    d["final_executable"] = await validate(live[0]) == "executable"
                    if d["final_executable"]:
                        failure = "none"
            except Exception:  # noqa: BLE001, S110 - fixed durable-check stage only
                pass
    d["failure"] = failure
    return failure == "none", d
