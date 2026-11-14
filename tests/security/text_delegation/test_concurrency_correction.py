"""Synthetic direct-claim lifecycle tests. No app fixtures, network or database."""

import asyncio
from collections.abc import Callable
from dataclasses import asdict
from threading import Barrier, BrokenBarrierError, Event
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import psycopg
import pytest
from app.workers.delegation import DelegationUnavailable, JobExecutionGrant
from concurrency import collect_claims, direct_claim
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

GRANT = JobExecutionGrant(
    UUID(int=1), UUID(int=2), UUID(int=3), UUID(int=4), UUID(int=5), "text_extract_v1", UUID(int=6)
)


class DriverError(psycopg.Error):
    def __init__(self, state="55P03", message="quota_gate_busy"):
        self.sqlstate = state
        self._diag = SimpleNamespace(message_primary=message)

    @property
    def diag(self) -> Any:
        return self._diag


def busy(state="55P03", message="quota_gate_busy"):
    return DBAPIError(None, None, DriverError(state, message))


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def mappings(self):
        return self

    def one_or_none(self):
        return self.value


class Engine:
    def __init__(self, outcome="grant", *, pid: int | None = 1, fault=None, error=None):
        self.outcome, self.pid, self.fault = outcome, pid, fault
        self.error = error if error is not None else RuntimeError("synthetic")
        self.events = []
        self.active = False
        self.hook: Callable[[str], None] = lambda stage: None

    def step(self, stage):
        self.events.append(stage)
        self.hook(stage)
        if stage == self.fault:
            raise self.error

    def connect(self):
        self.step("connect")
        return self

    def begin(self):
        self.step("begin")
        self.active = True
        return self

    def execute(self, query, params=None):
        sql = str(query)
        if sql.startswith("SELECT current_user"):
            assert sql == (
                "SELECT current_user=session_user AND current_user='notebook_text_scheduler' "
                "AND NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            self.step("principal")
            return Result(self.outcome != "unsafe_role")
        if sql.startswith("SET LOCAL"):
            self.step("bounds")
            return Result(None)
        if sql.startswith("SHOW"):
            self.step("isolation")
            return Result(
                "serializable" if self.outcome == "unsafe_isolation" else "read committed"
            )
        if sql == "SELECT pg_backend_pid()":
            self.step("pid")
            return Result(self.pid)
        assert sql == "SELECT * FROM public.claim_text_job(:seconds)"
        assert params == {"seconds": 60} and self.active
        self.step("claim")
        if self.outcome == "busy":
            raise busy()
        return Result(asdict(GRANT) if self.outcome == "grant" else None)

    def commit(self):
        self.step("commit")
        self.active = False

    def rollback(self):
        self.step("rollback")
        self.active = False

    def close(self):
        self.step("close")

    def dispose(self):
        self.step("dispose")


class NoBarrier:
    def wait(self):
        pass


def one(engine):
    return direct_claim(0, lambda: engine, NoBarrier())


async def competition(
    engines, *, job=GRANT.job_id, fence=GRANT.delivery_token, state="executable", **kwargs
):
    import protocol

    calls = []

    def read_fence(job_id):
        assert all("dispose" in e.events for e in engines)
        calls.append("fence")
        return (fence,)

    async def validate(grant):
        calls.append("validate")
        assert grant == GRANT
        return state

    result = await collect_claims(lambda i: engines[i], job, read_fence, validate, **kwargs)
    protocol.safe_concurrency(result[1])
    return result, calls


@pytest.mark.parametrize("loser", ["idle", "busy"])
@pytest.mark.parametrize("winner", [0, 1])
def test_direct_claim_collector_accepts_only_confirmed_allowed_pairs(loser, winner):
    engines = [Engine(loser, pid=1), Engine(loser, pid=2)]
    engines[winner].outcome = "grant"
    (ok, diagnostic), calls = asyncio.run(competition(engines))
    assert ok and diagnostic["failure"] == "none" and calls == ["fence", "validate"]
    assert all(e.events.count("claim") == 1 for e in engines)


@pytest.mark.parametrize("outcome", ["grant", "idle", "busy"])
def test_direct_claim_does_not_publish_before_transaction_and_connection_finish(outcome):
    engine, finished, result = Engine(outcome), Event(), []
    from concurrent.futures import ThreadPoolExecutor

    entered, release = Event(), Event()

    def hook(stage):
        if stage == "close":
            entered.set()
            assert release.wait(2)

    engine.hook = hook
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: result.append(one(engine)) or finished.set())
        try:
            assert entered.wait(2) and not finished.is_set() and not result
            assert ("rollback" if outcome == "busy" else "commit") in engine.events
        finally:
            release.set()
        future.result(2)
    assert result[0].diagnostic["outcome"] == outcome


@pytest.mark.parametrize(
    "stage",
    [
        "connect",
        "begin",
        "principal",
        "bounds",
        "isolation",
        "pid",
        "commit",
        "rollback",
        "close",
        "dispose",
    ],
)
def test_exact_busy_outside_claim_is_failure_and_preserves_primary_stage(stage):
    engine = Engine("busy" if stage == "rollback" else "grant", fault=stage, error=busy())
    result = one(engine)
    assert result.diagnostic["outcome"] == "failed"
    assert result.diagnostic["failure_stage"] == stage


@pytest.mark.parametrize(
    "error",
    [
        busy("XX000"),
        busy(message="other"),
        RuntimeError("quota_gate_busy"),
        DriverError(),
        DelegationUnavailable("delegation_unavailable"),
    ],
)
def test_only_actual_dbapi_exact_claim_error_can_be_busy(error):
    engine = Engine(fault="claim", error=error)
    assert one(engine).diagnostic["outcome"] == "failed"
    assert engine.events.count("claim") == 1


@pytest.mark.parametrize("pair", [("idle", "idle"), ("grant", "grant"), ("busy", "busy")])
def test_collector_rejects_invalid_pairs(pair):
    (ok, diagnostic), calls = asyncio.run(competition([Engine(pair[0]), Engine(pair[1], pid=2)]))
    assert not ok and diagnostic["failure"] == "grants" and not calls


@pytest.mark.parametrize(
    "fault",
    ["job", "fence", "validate", "duplicate_pid", "missing_pid", "role", "isolation", "barrier"],
)
def test_collector_rejects_delivery_and_session_failures(fault):
    engines = [Engine(), Engine("idle", pid=2)]
    kwargs = {}
    if fault in ("job", "fence"):
        kwargs[fault] = UUID(int=99)
    if fault == "validate":
        kwargs["state"] = "cancelled"
    if fault == "duplicate_pid":
        engines[1].pid = 1
    if fault == "missing_pid":
        engines[1].pid = None
    if fault in ("role", "isolation"):
        engines[1].outcome = "unsafe_" + fault
    if fault == "barrier":
        barrier = Barrier(2, timeout=5)
        barrier.abort()
        kwargs["barrier"] = barrier
    (ok, diagnostic), _ = asyncio.run(competition(engines, **kwargs))
    assert not ok and diagnostic["failure"] != "none"


def test_broken_barrier_never_claims():
    class Broken:
        def wait(self):
            raise BrokenBarrierError

    engine = Engine()
    result = direct_claim(0, lambda: engine, Broken())
    assert result.diagnostic["failure_stage"] == "barrier"
    assert "claim" not in engine.events


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    import socket
    import subprocess

    import sqlalchemy

    def forbidden(*args, **kwargs):
        raise AssertionError("external I/O forbidden")

    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(sqlalchemy, "create_engine", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def test_actual_repository_sanitization_explains_old_red_without_broad_acceptance():
    from contextlib import contextmanager

    from app.workers.delegation import BoundedThreadRunner, DelegationRepository, SchedulerFactory

    engine = Engine("busy")

    @contextmanager
    def transaction():
        engine.begin()
        try:
            yield cast(Session, engine)
            engine.commit()
        except Exception:
            engine.rollback()
            raise
        finally:
            engine.close()

    async def legacy():
        runner = BoundedThreadRunner()
        repo = DelegationRepository(SchedulerFactory(transaction, 1, 3), runner)
        try:
            # The old collector demanded a successful result from BOTH repository claims.
            with pytest.raises(DelegationUnavailable, match="^delegation_unavailable$"):
                await repo.claim()
        finally:
            assert await runner.close()

    asyncio.run(legacy())
    assert engine.events.count("claim") == 1
    assert one(Engine("busy")).diagnostic["outcome"] == "busy"


def test_actual_nonblocking_gate_schedule_is_new_green():
    from threading import Lock

    gate, contended = Lock(), Event()
    persisted = []

    class GatedEngine(Engine):
        owns_gate = False

        def execute(self, query, params=None):
            if str(query) == "SELECT * FROM public.claim_text_job(:seconds)":
                self.owns_gate = gate.acquire(blocking=False)
                if not self.owns_gate:
                    contended.set()
                    self.outcome = "busy"
                else:
                    assert contended.wait(2)
            return super().execute(query, params)

        def commit(self):
            super().commit()
            assert self.owns_gate
            persisted.append(GRANT.delivery_token)
            gate.release()

    engines = [GatedEngine(pid=1), GatedEngine(pid=2)]
    (ok, d), calls = asyncio.run(competition(engines))
    assert ok and calls == ["fence", "validate"] and persisted == [GRANT.delivery_token]
    assert sorted(r["outcome"] for r in d["contenders"]) == ["busy", "grant"]
    assert all(e.events.count("claim") == 1 for e in engines)


@pytest.mark.parametrize(
    "outcome,stage", [("grant", "commit"), ("busy", "rollback"), ("busy", "close")]
)
def test_no_early_result_at_commit_rollback_or_close(outcome, stage):
    from concurrent.futures import ThreadPoolExecutor

    engine, entered, release = Engine(outcome), Event(), Event()

    def hook(current):
        if current == stage:
            entered.set()
            assert release.wait(2)

    engine.hook = hook
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(one, engine)
        try:
            assert entered.wait(2) and not future.done()
        finally:
            release.set()
        assert future.result(2).diagnostic["outcome"] == outcome


def test_primary_claim_failure_survives_rollback_close_and_dispose_failures():
    engine = Engine()

    def hook(stage):
        if stage in ("claim", "rollback", "close", "dispose"):
            raise busy("XX000")

    engine.hook = hook
    d = one(engine).diagnostic
    assert d["failure_stage"] == "claim" and d["outcome"] == "failed"
    assert d["transaction"] == "unknown" and not d["connection_closed"]
    assert not d["engine_disposed"] and engine.events.count("rollback") == 1


@pytest.mark.parametrize(
    "outcome,fault", [("grant", "commit"), ("busy", "rollback"), ("busy", "close")]
)
def test_collector_never_accepts_unknown_commit_failed_rollback_or_close(outcome, fault):
    engines = [
        Engine(outcome, fault=fault),
        Engine("grant" if outcome == "busy" else "busy", pid=2),
    ]
    (ok, d), calls = asyncio.run(competition(engines))
    assert not ok and d["failure"] == "contender" and not calls
    if fault == "commit":
        assert d["contenders"][0]["transaction"] == "unknown"
    if fault == "rollback":
        assert engines[0].events.count("rollback") == 1


@pytest.mark.parametrize("mode", ["unresolved", "raises"])
def test_collector_rejects_runner_shutdown_without_confirmation(mode):
    from app.workers.delegation import BoundedThreadRunner

    class Runner(BoundedThreadRunner):
        async def close(self, *, timeout=3):
            assert await super().close(timeout=timeout)
            if mode == "raises":
                raise RuntimeError("synthetic close")
            return False

    (ok, d), calls = asyncio.run(
        competition([Engine(), Engine("idle", pid=2)], runner_factory=Runner)
    )
    assert not ok and d["failure"] == "runner_close" and not calls
    assert all(not row["runner_closed"] and row["connection_closed"] for row in d["contenders"])


def test_collection_timeout_is_bounded_and_does_not_pretend_to_kill_running_claims():
    from time import monotonic

    from app.workers.delegation import BoundedThreadRunner

    release, entered = Event(), Event()
    engines = [Engine(), Engine("busy", pid=2)]
    runners = []

    def hook(stage):
        if stage == "claim":
            entered.set()
            assert release.wait(3)

    for engine in engines:
        engine.hook = hook

    class Runner(BoundedThreadRunner):
        def __init__(self):
            super().__init__()
            runners.append(self)

        async def close(self, *, timeout=3):
            assert timeout == 3
            return await super().close(timeout=0.02)

    async def scenario():
        start = monotonic()
        try:
            (ok, d), calls = await competition(engines, runner_factory=Runner, timeout=0.05)
            assert entered.is_set() and not ok and d["failure"] == "collection"
            assert not calls and monotonic() - start < 1
            assert all(
                not r["runner_closed"] and not r["connection_closed"] for r in d["contenders"]
            )
            assert all("close" not in e.events for e in engines)
        finally:
            release.set()  # synthetic threads are explicitly released and joined, not killed
            assert all(await asyncio.gather(*(BoundedThreadRunner.close(r) for r in runners)))
        assert all(e.events.count("claim") == 1 and "close" in e.events for e in engines)

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["fence", "validate"])
def test_collector_durable_read_and_final_validation_errors_remain_failures(stage):
    engines = [Engine(), Engine("idle", pid=2)]

    def fence(job):
        if stage == "fence":
            raise busy()
        return (GRANT.delivery_token,)

    async def validate(grant):
        raise DelegationUnavailable("delegation_unavailable")

    ok, d = asyncio.run(collect_claims(lambda i: engines[i], GRANT.job_id, fence, validate))
    assert not ok and d["failure"] == stage


def report_base(ok=True):
    return {
        "stage": "concurrency",
        "ok": ok,
        "collection": True,
        "exit": 0 if ok else 1,
        "cases": [
            {
                "case": 0,
                "when": when,
                "outcome": "passed" if ok or when != "call" else "failed",
                "xfail": False,
            }
            for when in ("setup", "call", "teardown")
        ],
    }


def good_diagnostic():
    (ok, d), _ = asyncio.run(competition([Engine(), Engine("busy", pid=2)]))
    assert ok
    return d


@pytest.mark.parametrize("boundary", ["producer", "consumer"])
@pytest.mark.parametrize(
    "fault",
    [
        "extra",
        "row_extra",
        "version",
        "bool_version",
        "failure",
        "outcome",
        "stage",
        "transaction",
        "duplicate",
        "missing",
        "bool_index",
        "string_flag",
        "bool_count",
        "count",
        "pid_count",
        "busy_commit",
        "grant_rollback",
        "close",
        "runner",
        "principal",
        "isolation",
        "barrier",
        "not_called",
        "unknown_commit",
        "fence",
        "validate",
    ],
)
def test_diagnostic_producer_and_consumer_reject_nonclosed_or_impossible_success(boundary, fault):
    import protocol
    import run_packet

    d = good_diagnostic()
    row = d["contenders"][1]
    if fault == "extra":
        d["raw"] = "CANARY"
    elif fault == "row_extra":
        row["raw"] = "CANARY"
    elif fault in ("version", "bool_version"):
        d["version"] = True if fault == "bool_version" else 2
    elif fault == "failure":
        d["failure"] = "unknown"
    elif fault in ("outcome", "transaction"):
        row[fault] = "unknown_enum"
    elif fault == "stage":
        row["failure_stage"] = "unknown"
    elif fault == "duplicate":
        row["index"] = 0
    elif fault == "missing":
        d["contenders"].pop()
    elif fault == "bool_index":
        row["index"] = True
    elif fault == "string_flag":
        row["connection_closed"] = "true"
    elif fault == "bool_count":
        d["grant_count"] = True
    elif fault == "count":
        d["grant_count"] = 2
    elif fault == "pid_count":
        d["distinct_pid_count"] = 1
    elif fault == "busy_commit":
        row["transaction"] = "committed"
    elif fault == "grant_rollback":
        d["contenders"][0]["transaction"] = "rolled_back"
    elif fault == "unknown_commit":
        d["contenders"][0]["transaction"] = "unknown"
    elif fault in ("fence", "validate"):
        d["fence_matches" if fault == "fence" else "final_executable"] = False
    else:
        key = {
            "close": "connection_closed",
            "runner": "runner_closed",
            "principal": "principal_verified",
            "isolation": "read_committed",
            "barrier": "barrier_released",
            "not_called": "claim_called",
        }[fault]
        row[key] = False
    with pytest.raises(protocol.Refused):
        if boundary == "producer":
            run_packet.concurrency_report(report_base(), d)
        else:
            protocol.safe_report(report_base() | {"diagnostic": d})


@pytest.mark.parametrize("diagnostic_state", ["success", "failed", "missing"])
@pytest.mark.parametrize("pytest_ok", [True, False])
def test_diagnostics_never_override_pytest_failure_or_missing_evidence(diagnostic_state, pytest_ok):
    import protocol
    import run_packet
    from concurrency import empty_diagnostic

    d = good_diagnostic() if diagnostic_state == "success" else empty_diagnostic()
    if diagnostic_state == "missing":
        d = None
    report = run_packet.concurrency_report(report_base(pytest_ok), d)
    assert protocol.safe_report(report)["ok"] == (pytest_ok and diagnostic_state == "success")


def test_json_duplicate_diagnostic_keys_are_refused():
    import json

    import protocol

    with pytest.raises(protocol.Refused):
        json.loads('{"version":1,"version":1}', object_pairs_hook=protocol.unique)


@pytest.mark.parametrize("fault", ["none", "missing", "failed", "malformed", "pytest_failed"])
def test_actual_pytest_reporter_exports_only_validated_concurrency_diagnostics(monkeypatch, fault):
    import protocol
    import run_packet
    from concurrency import empty_diagnostic

    name = protocol.contract()["concurrency"][0]
    item = SimpleNamespace(nodeid=name, originalname=name.split("::")[1])
    if fault != "missing":
        item.concurrency_diagnostic = empty_diagnostic() if fault == "failed" else good_diagnostic()
        if fault == "malformed":
            item.concurrency_diagnostic["raw"] = "CANARY"

    def fake_main(args, plugins):
        reporter = plugins[0]
        reporter.pytest_collection_modifyitems(None, None, [item])
        for when in ("setup", "call", "teardown"):
            report = SimpleNamespace(
                when=when,
                outcome="failed" if fault == "pytest_failed" and when == "call" else "passed",
            )
            hook = reporter.pytest_runtest_makereport(item, None)
            next(hook)
            with pytest.raises(StopIteration):
                hook.send(SimpleNamespace(get_result=lambda current=report: current))
        return 1 if fault == "pytest_failed" else 0

    monkeypatch.setattr(pytest, "main", fake_main)
    if fault == "malformed":
        with pytest.raises(protocol.Refused):
            run_packet.pytest_report("concurrency")
    else:
        report = run_packet.pytest_report("concurrency")
        assert report["ok"] == (fault == "none")
        assert "diagnostic" in report


def test_engine_failure_is_not_quota_contention():
    def fail():
        raise busy()

    result = direct_claim(0, fail, NoBarrier())
    assert result.diagnostic["failure_stage"] == "engine"
    assert result.diagnostic["outcome"] == "failed"
    assert not result.diagnostic["connection_opened"]


def test_busy_during_row_materialization_is_not_claim_execute_busy():
    class BadResult(Result):
        def mappings(self):
            raise busy()

    class BadRowEngine(Engine):
        def execute(self, query, params=None):
            result = super().execute(query, params)
            return BadResult(None) if str(query).startswith("SELECT * FROM") else result

    result = one(BadRowEngine())
    assert result.diagnostic["outcome"] == "failed"
    assert result.diagnostic["failure_stage"] == "claim"


def test_collector_calls_fresh_real_repository_validate_only_after_both_contenders_close():
    from contextlib import contextmanager

    from app.workers.delegation import BoundedThreadRunner, DelegationRepository, SchedulerFactory

    engines = [Engine(), Engine("busy", pid=2)]
    validated = []

    class ValidationEngine(Engine):
        def execute(self, query, params=None):
            if str(query).startswith("SELECT public.validate_text_job("):
                assert params == asdict(GRANT)
                validated.append(True)
                return Result("executable")
            return super().execute(query, params)

    @contextmanager
    def transaction():
        assert all("close" in e.events and "dispose" in e.events for e in engines)
        db = ValidationEngine()
        db.begin()
        try:
            yield cast(Session, db)
            db.commit()
        finally:
            db.close()

    async def scenario():
        runner = BoundedThreadRunner()
        repo = DelegationRepository(SchedulerFactory(transaction, 1, 3), runner)
        try:
            ok, d = await collect_claims(
                lambda i: engines[i],
                GRANT.job_id,
                lambda job: (GRANT.delivery_token,),
                repo.validate,
            )
            assert ok and d["final_executable"]
        finally:
            assert await runner.close()

    asyncio.run(scenario())
    assert validated == [True]
