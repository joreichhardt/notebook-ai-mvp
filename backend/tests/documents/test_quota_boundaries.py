"""0006 seam regressions: real SCRAM sessions/gate, synthetic Storage only."""

import asyncio
from contextlib import contextmanager

import psycopg
import pytest
from app.db.uploads import reserve_upload
from app.documents.service import DocumentError
from app.workers.text import TextWorker
from sqlalchemy import event, text
from test_service import URL, perform, sample

pytest_plugins = ["test_service"]
pytestmark = pytest.mark.skipif(not URL, reason="isolated migrated PostgreSQL required")


@pytest.fixture(autouse=True)
def backoff_has_no_transaction(upload_scene, monkeypatch):
    from threading import Lock, get_ident
    from time import sleep

    from psycopg.pq import TransactionStatus

    # Each callback is synchronous on one thread (no await inside its DB Tx).
    # The pool is shared with other threads; global checkedout/active counts
    # cannot establish ownership. Keep strong Connection references so reuse of
    # a pooled DBAPI connection cannot masquerade as the previous callback.
    lock = Lock()
    leases, transactions, latest = {}, {}, {}
    before_check = []

    def checkout(raw, record, proxy):
        with lock:
            assert raw not in leases
            leases[raw] = {"owner": get_ident(), "transactions": []}

    def begin(connection):
        with lock:
            state = {"connection": connection, "outcome": None, "returned": False}
            transactions[connection] = state
            latest[get_ident()] = state
            leases[connection.connection.dbapi_connection]["transactions"].append(state)

    def rollback(connection):
        with lock:
            transactions[connection]["outcome"] = "rollback"

    def commit(connection):
        with lock:
            transactions[connection]["outcome"] = "commit"

    def checkin(raw, record):
        with lock:
            # Pool checkin occurs AFTER DBAPI rollback/commit, not at the
            # before-rollback Engine event. Validate actual PostgreSQL IDLE.
            assert raw is not None and raw.info.transaction_status == TransactionStatus.IDLE
            for state in leases.pop(raw)["transactions"]:
                state["returned"] = True

    def checked_sleep(delay):
        for hook in before_check:
            hook()
        with lock:
            own = latest[get_ident()]
            assert own["outcome"] == "rollback", "retry callback did not roll back"
            assert own["returned"], "retry callback did not return its connection"
            assert own["connection"].closed, "retry callback Connection is still open"
            assert not own["connection"].in_transaction()
            assert not any(lease["owner"] == get_ident() for lease in leases.values())
        sleep(delay)  # Independent sibling transactions are intentionally allowed.

    listeners = [
        ("checkout", checkout),
        ("begin", begin),
        ("rollback", rollback),
        ("commit", commit),
        ("checkin", checkin),
    ]
    engines = [upload_scene["actor_engine"], upload_scene["io_engine"]]
    for engine in engines:
        for name, listener in listeners:
            event.listen(engine, name, listener)
    monkeypatch.setattr("app.documents.transactions.sleep", checked_sleep)
    try:
        yield before_check
        assert not leases, "test leaked a checked-out connection"
    finally:
        for engine in engines:
            for name, listener in listeners:
                event.remove(engine, name, listener)


@contextmanager
def contend(engine, *, target=1, marker="SELECT public.quota_gate()", exhaust=False):
    """Acquire real competing gate immediately before a chosen statement.

    Release transient contention only after the failed transaction rolls back.
    Observe actual XIDs, not manufactured driver exceptions or protected writes.
    """
    from threading import Lock

    holder = psycopg.connect(URL)
    seen, xids, rollbacks = [], [], []
    blocked = {}
    lock = Lock()
    acquired = False
    released = False

    def before(connection, cursor, statement, parameters, context, many):
        nonlocal acquired
        if marker not in statement:
            return
        with lock:
            seen.append(statement)
            if len(seen) < target:
                return
            with connection.connection.cursor() as probe:
                probe.execute("SELECT txid_current()")
                xids.append(probe.fetchone()[0])
            if released:
                return
            blocked[connection] = [connection.connection.dbapi_connection, False]
            if not acquired:
                holder.execute("SELECT public.quota_gate()")
                acquired = True

    def rollback(connection):
        with lock:
            if connection in blocked:
                blocked[connection][1] = True
                rollbacks.append(True)

    def checked_in(connection, record):
        nonlocal released
        with lock:
            # Only the rejected callback's own completed rollback/checkin
            # releases the holder, never an unrelated sibling checkin.
            own_rollback = any(raw is connection and rolled for raw, rolled in blocked.values())
            if acquired and own_rollback and not released and not exhaust:
                holder.rollback()
                released = True

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "rollback", rollback)
    event.listen(engine, "checkin", checked_in)
    try:
        yield seen, xids, rollbacks
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "rollback", rollback)
        event.remove(engine, "checkin", checked_in)
        holder.rollback()
        holder.close()


@pytest.mark.parametrize("advance", [0.01, 0.25, 1.0])
def test_retry_start_admission_rechecks_clock_after_backoff(upload_scene, monkeypatch, advance):
    from app.documents import transactions

    c = upload_scene
    clock, delays = [100.0], []
    owned_sleep = transactions.sleep  # Keep the real caller-owned rollback/CLOSED/IDLE probe.

    def suspended_backoff(delay):
        delays.append(delay)
        owned_sleep(0)  # No wall-clock suspension: only the controlled monotonic clock advances.
        clock[0] += advance

    monkeypatch.setattr(transactions, "monotonic", lambda: clock[0])
    monkeypatch.setattr(transactions, "sleep", suspended_backoff)
    with contend(c["actor_engine"]) as (seen, xids, rollbacks):
        if advance >= 0.25:
            with pytest.raises(DocumentError) as caught:
                perform(c)
            assert (caught.value.status_code, caught.value.code) == (503, "quota_gate_busy")
            # The failed first callback rolled back; no second gate/callback is admitted.
            assert len(seen) == len(xids) == 1
            assert c["store"].actions == []
        else:
            assert perform(c).job.status == "queued"
            assert len(xids) >= 2 and xids[0] != xids[1]
            assert c["store"].actions == ["put", "read"]
        assert delays == [0.01] and len(rollbacks) == 1
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT count(*) FROM upload_intents WHERE notebook_id=%s", (c["notebook"],)
        ).fetchone() == (0 if advance >= 0.25 else 1,)
        assert db.execute(
            "SELECT count(*) FROM ingestion_jobs WHERE notebook_id=%s", (c["notebook"],)
        ).fetchone() == (0 if advance >= 0.25 else 1,)


def test_service_capacity_is_opaque429_before_any_io(upload_scene):
    c = upload_scene
    for n in range(10):
        with c["actor_tx"](c["actor"]) as db:
            reserve_upload(
                db,
                c["notebook"],
                idempotency_key=f"full-{n}",
                filename="sample.txt",
                sha256=sample().sha256,
                byte_count=5,
                media_type="text/plain",
            )
    with pytest.raises(DocumentError) as caught:
        perform(c)
    assert (caught.value.status_code, caught.value.code) == (429, "quota_capacity")
    assert c["store"].actions == [] and not c["active"]


@pytest.mark.parametrize("phase", ["reserve", "claim", "receipt", "finalize"])
@pytest.mark.parametrize("exhaust", [False, True])
def test_service_busy_retries_only_whole_short_transaction(upload_scene, phase, exhaust):
    c = upload_scene
    engine = c["actor_engine"] if phase in ("reserve", "finalize") else c["io_engine"]
    marker = {
        "reserve": "SELECT public.quota_gate()",
        "finalize": "SELECT public.quota_gate()",
        "claim": "SELECT * FROM public.claim_upload_io",
        "receipt": "SELECT public.confirm_upload_io",
    }[phase]
    with contend(
        engine, target=2 if phase == "finalize" else 1, marker=marker, exhaust=exhaust
    ) as (seen, xids, rollbacks):
        if exhaust:
            with pytest.raises(DocumentError) as caught:
                perform(c)
            assert (caught.value.status_code, caught.value.code) == (503, "quota_gate_busy")
            assert len(xids) >= 2 and len(set(xids)) == len(xids)
        else:
            assert perform(c).document.status == "queued"
            assert len(seen) >= (3 if phase == "finalize" else 2)
        assert rollbacks and not c["active"]
    expected_io = [] if exhaust and phase in ("reserve", "claim") else ["put", "read"]
    assert c["store"].actions == expected_io
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT count(*) FROM documents WHERE notebook_id=%s", (c["notebook"],)
        ).fetchone() == (0 if exhaust else 1,)
        assert db.execute(
            "SELECT count(*) FROM ingestion_jobs WHERE notebook_id=%s", (c["notebook"],)
        ).fetchone() == (0 if exhaust else 1,)
    if exhaust and phase == "finalize":
        assert perform(c).document.status == "queued"
        assert c["store"].actions == ["put", "read"]


@pytest.mark.parametrize(
    "phase,target", [("claim", 1), ("pulse", 3), ("publish", 5), ("cancel", 3)]
)
@pytest.mark.parametrize("exhaust", [False, True])
def test_worker_busy_is_bounded_without_repeating_io_or_losing_fence(
    upload_scene, phase, target, exhaust
):
    c = upload_scene
    receipt = perform(c)
    calls = []

    class Original:
        async def read(self, actor_id, scope):
            calls.append("read")
            if phase == "cancel":
                raise asyncio.CancelledError
            return b"hello"

    worker = TextWorker(c["actor_engine"], store_factory=lambda auth: Original())
    with contend(c["actor_engine"], target=target, exhaust=exhaust) as (_, xids, rollbacks):
        if phase == "cancel":
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(worker.run_one(actor_id=c["actor"], job_id=receipt.job.id))
        else:
            result = asyncio.run(worker.run_one(actor_id=c["actor"], job_id=receipt.job.id))
            assert result == (
                ("not_claimed" if phase == "claim" else "yielded") if exhaust else "succeeded"
            )
        assert rollbacks
        if exhaust:
            assert len(xids) >= 2 and len(set(xids)) == len(xids)
    assert calls == ([] if phase == "claim" and exhaust else ["read"])
    with c["actor_tx"](c["actor"]) as db:
        row = db.execute(
            text("SELECT status,attempts,lease_token FROM ingestion_jobs WHERE id=:j"),
            {"j": receipt.job.id},
        ).one()
        chunks = db.execute(
            text("SELECT count(*) FROM chunks WHERE document_version_id=:v"),
            {"v": receipt.document.version_id},
        ).scalar_one()
    if exhaust:
        assert row[0:2] == (("queued", 0) if phase == "claim" else ("running", 1))
        assert (row[2] is None) == (phase == "claim")
        assert chunks == 0
    else:
        assert row == ("cancelled" if phase == "cancel" else "succeeded", 1, None)
        assert chunks == (0 if phase == "cancel" else 1)


@pytest.mark.parametrize(
    "code,message,expected",
    [
        ("P0001", "other_error", "database_unavailable"),
        ("42501", "quota_capacity", "database_unavailable"),
        ("55P03", "other_error", "database_unavailable"),
        ("P0001", "quota_legacy_unsupported", "quota_legacy_unsupported"),
        ("P0001", "quota_preflight_required", "quota_preflight_required"),
        ("P0001", "quota_isolation_unsupported", "quota_isolation_unsupported"),
    ],
)
def test_service_nearby_or_unsupported_errors_never_retry(upload_scene, code, message, expected):
    c = upload_scene
    original = c["service"].actor_tx
    invocations = []

    @contextmanager
    def failing(actor):
        invocations.append(actor)
        with original(actor) as db:
            db.execute(
                text(
                    "SELECT set_config('test.quota_code',:c,true),set_config('test.quota_message',:m,true)"
                ),
                {"c": code, "m": message},
            )
            db.execute(
                text(
                    "DO $$ BEGIN RAISE EXCEPTION USING MESSAGE=current_setting('test.quota_message'), ERRCODE=current_setting('test.quota_code'); END $$"
                )
            )
            yield db

    c["service"].actor_tx = failing
    with pytest.raises(DocumentError) as caught:
        perform(c)
    assert (caught.value.status_code, caught.value.code) == (503, expected)
    assert len(invocations) == 1 and c["store"].actions == []


def test_finalize_exit_busy_rolls_back_all_rows_and_retries_no_storage(upload_scene):
    # Server-injected exact error at factory exit AFTER writes, distinct from
    # real gate-contention tests: proves commit-context errors aren't sanitized early.
    c = upload_scene
    original = c["service"].actor_tx
    exits = []

    @contextmanager
    def exit_busy(actor):
        with original(actor) as db:
            yield db
            state = db.execute(
                text("SELECT state FROM upload_intents WHERE notebook_id=:n"), {"n": c["notebook"]}
            ).scalar_one_or_none()
            if state == "finalized":
                exits.append(db.execute(text("SELECT txid_current()")).scalar_one())
                if len(exits) == 1:
                    db.execute(
                        text(
                            "DO $$ BEGIN RAISE EXCEPTION 'quota_gate_busy' USING ERRCODE='55P03'; END $$"
                        )
                    )

    c["service"].actor_tx = exit_busy
    assert perform(c).job.status == "queued"
    assert len(exits) == 2 and len(set(exits)) == 2
    assert c["store"].actions == ["put", "read"]
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT count(*) FROM documents WHERE notebook_id=%s", (c["notebook"],)
        ).fetchone() == (1,)
        assert db.execute(
            "SELECT count(*) FROM ingestion_jobs WHERE notebook_id=%s", (c["notebook"],)
        ).fetchone() == (1,)


def test_head_pdf_is_rejected_before_intent_job_or_storage(upload_scene):
    from dataclasses import replace

    c = upload_scene
    with pytest.raises(DocumentError) as caught:
        perform(c, upload=replace(sample(), filename="sample.pdf", media_type="application/pdf"))
    assert (caught.value.status_code, caught.value.code) == (415, "unsupported_media")
    assert c["store"].actions == []
    with psycopg.connect(URL) as db:
        for table in ("upload_intents", "documents", "ingestion_jobs"):
            # Fixed identifiers, bound notebook; no user-controlled SQL.
            from psycopg import sql

            assert db.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE notebook_id=%s").format(
                    sql.Identifier(table)
                ),
                (c["notebook"],),
            ).fetchone() == (0,)


def test_worker_capacity_honors_deleted_jobs_live_mirror_without_io(upload_scene):
    c = upload_scene
    receipts = [perform(c, key=f"mirror-{n}") for n in range(3)]
    calls = []

    class Original:
        async def read(self, actor_id, scope):
            calls.append("read")
            return b"hello"

    worker = TextWorker(c["actor_engine"], store_factory=lambda auth: Original())
    for receipt in receipts[:2]:
        assert worker._claim(c["actor"], receipt.job.id) is not None
        with psycopg.connect(URL) as setup:
            setup.execute("DELETE FROM ingestion_jobs WHERE id=%s", (receipt.job.id,))
    assert (
        asyncio.run(worker.run_one(actor_id=c["actor"], job_id=receipts[2].job.id)) == "not_claimed"
    )
    assert calls == []
    with c["actor_tx"](c["actor"]) as db:
        assert db.execute(
            text("SELECT status,attempts,lease_token FROM ingestion_jobs WHERE id=:j"),
            {"j": receipts[2].job.id},
        ).one() == ("queued", 0, None)
    with psycopg.connect(URL) as setup:
        assert setup.execute(
            "SELECT count(*) FROM upload_intents WHERE notebook_id=%s AND work_cancelled AND job_state='running' AND job_lease_expires_at>clock_timestamp()",
            (c["notebook"],),
        ).fetchone() == (2,)


def test_readmission_never_reactivates_sticky_cancelled_work(upload_scene):
    c = upload_scene
    receipt = perform(c)
    with psycopg.connect(URL) as setup:
        setup.execute("DELETE FROM app_admissions WHERE user_id=%s", (c["actor"],))
        setup.execute("INSERT INTO app_admissions VALUES (%s)", (c["actor"],))
    calls = []

    class Original:
        async def read(self, actor_id, scope):
            calls.append("read")
            return b"hello"

    worker = TextWorker(c["actor_engine"], store_factory=lambda auth: Original())
    assert asyncio.run(worker.run_one(actor_id=c["actor"], job_id=receipt.job.id)) == "not_claimed"
    assert calls == []
    with psycopg.connect(URL) as setup:
        assert setup.execute(
            "SELECT work_cancelled,job_state,job_attempts FROM upload_intents WHERE job_id=%s",
            (receipt.job.id,),
        ).fetchone() == (True, "queued", 0)


@pytest.mark.parametrize("rolled_back", [False, True])
def test_backoff_probe_rejects_own_unclosed_connection(upload_scene, rolled_back):
    from app.documents import transactions

    connection = upload_scene["actor_engine"].connect()
    try:
        transaction = connection.begin()
        connection.execute(text("SELECT 1"))
        if rolled_back:
            transaction.rollback()
        message = "did not return its connection" if rolled_back else "did not roll back"
        with pytest.raises(AssertionError, match=message):
            transactions.sleep(0)
    finally:
        connection.close()


def test_retry_owns_no_transaction_while_sibling_connection_is_checked_out(
    upload_scene, backoff_has_no_transaction
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy.orm import Session

    c = upload_scene
    receipt = perform(c)
    start, ready, release = Event(), Event(), Event()
    calls, overlap = [], []

    def sibling():
        assert start.wait(5)
        with Session(c["actor_engine"]) as db, db.begin():
            db.execute(
                text("SELECT set_config('request.jwt.claim.sub',:actor,true)"),
                {"actor": str(c["actor"])},
            )
            assert (
                db.execute(
                    text("SELECT user_id FROM app_admissions WHERE user_id=:actor"),
                    {"actor": c["actor"]},
                ).scalar_one()
                == c["actor"]
            )
            ready.set()
            assert release.wait(5)

    def overlap_at_backoff():
        start.set()
        assert ready.wait(5)
        # Independent, legitimate reader is pinned across the retry's sleep.
        assert c["actor_engine"].pool.checkedout() == 1
        overlap.append(True)

    class Original:
        async def read(self, actor_id, scope):
            calls.append("read")
            return b"hello"

    backoff_has_no_transaction.append(overlap_at_backoff)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(sibling)
        try:
            with contend(c["actor_engine"], target=3) as (seen, xids, rollbacks):
                result = asyncio.run(
                    TextWorker(c["actor_engine"], store_factory=lambda auth: Original()).run_one(
                        actor_id=c["actor"], job_id=receipt.job.id
                    )
                )
            assert result == "succeeded"
            assert overlap and rollbacks and len(seen) >= 6
            assert len(xids) >= 2 and xids[0] != xids[1]
            assert calls == ["read"]
        finally:
            release.set()
            start.set()
            future.result(timeout=5)
            backoff_has_no_transaction.remove(overlap_at_backoff)
    with c["actor_tx"](c["actor"]) as db:
        assert db.execute(
            text("SELECT status,attempts,lease_token FROM ingestion_jobs WHERE id=:j"),
            {"j": receipt.job.id},
        ).one() == ("succeeded", 1, None)
        assert (
            db.execute(
                text("SELECT count(*) FROM chunks WHERE document_version_id=:v"),
                {"v": receipt.document.version_id},
            ).scalar_one()
            == 1
        )


@contextmanager
def _watchdog_test_schedule(monkeypatch):
    """Test-only event schedule; no production timing/acquisition bound implied.

    Identify the actual created watcher Task by its exact nested code object,
    then compare Task identity. Context propagation labels only that Task's real
    to_thread heartbeat; foreground pulses cannot signal its commit event.
    """
    from contextvars import ContextVar
    from types import CodeType

    loop = asyncio.get_running_loop()
    read_started, heartbeat_committed, parked = asyncio.Event(), asyncio.Event(), asyncio.Event()
    origin = ContextVar("b3_watchdog_origin", default=None)
    tasks, ticks = [], []
    real_create, real_sleep = asyncio.create_task, asyncio.sleep
    watch_code = next(
        code
        for code in TextWorker._run_one.__code__.co_consts
        if isinstance(code, CodeType) and code.co_name == "watch"
    )

    def create(coro, *args, **kwargs):
        task = real_create(coro, *args, **kwargs)
        if getattr(coro, "cr_code", None) is watch_code:
            assert not tasks, "fixture expects exactly one worker watcher"
            tasks.append(task)
        return task

    def is_watcher():
        return bool(tasks) and origin.get() is tasks[0]

    async def sleep(delay):
        if tasks and asyncio.current_task() is tasks[0]:
            if not ticks:
                ticks.append(True)
                await asyncio.wait_for(read_started.wait(), 3)
                origin.set(tasks[0])
                return
            await asyncio.wait_for(parked.wait(), 3)
        else:
            await real_sleep(delay)

    class Worker(TextWorker):
        def _pulse(self, actor, job, token):
            result = super()._pulse(actor, job, token)
            if is_watcher():
                loop.call_soon_threadsafe(heartbeat_committed.set)
            return result

    with monkeypatch.context() as scoped:
        scoped.setattr(asyncio, "create_task", create)
        scoped.setattr(asyncio, "sleep", sleep)
        try:
            yield Worker, read_started, heartbeat_committed, is_watcher
        finally:
            read_started.set()
            heartbeat_committed.set()
            parked.set()
            assert tasks and all(task.done() for task in tasks), "worker did not cancel watcher"


@pytest.mark.parametrize("exhaust", [False, True])
def test_watchdog_busy_closes_unfinished_io_but_keeps_live_lease(
    upload_scene, exhaust, monkeypatch
):
    c = upload_scene
    receipt = perform(c)
    calls, closed = [], []

    class Original:
        async def read(self, actor_id, scope):
            calls.append("read")
            try:
                await asyncio.sleep(0.15)
                return b"hello"
            finally:
                closed.append(True)

    async def transient():
        with _watchdog_test_schedule(monkeypatch) as (Worker, read_started, committed, _):

            class EventOriginal:
                async def read(self, actor_id, scope) -> bytes:
                    assert not c["active"]
                    calls.append("read")
                    try:
                        read_started.set()
                        await committed.wait()
                        return b"hello"
                    finally:
                        closed.append(True)

            worker = Worker(
                c["actor_engine"],
                store_factory=lambda auth: EventOriginal(),
                heartbeat_seconds=0.03,
            )
            with contend(c["actor_engine"], target=3, exhaust=False):
                return await asyncio.wait_for(
                    worker.run_one(actor_id=c["actor"], job_id=receipt.job.id), 5
                )

    if exhaust:
        # Preserve the original real exhaustion/I/O cancellation scenario verbatim.
        worker = TextWorker(
            c["actor_engine"], store_factory=lambda auth: Original(), heartbeat_seconds=0.03
        )
        with contend(c["actor_engine"], target=3, exhaust=exhaust):
            result = asyncio.run(worker.run_one(actor_id=c["actor"], job_id=receipt.job.id))
    else:
        result = asyncio.run(transient())
    assert result == ("yielded" if exhaust else "succeeded")
    assert calls == ["read"] and closed == [True]
    with c["actor_tx"](c["actor"]) as db:
        row = db.execute(
            text("SELECT status,attempts,lease_token FROM ingestion_jobs WHERE id=:j"),
            {"j": receipt.job.id},
        ).one()
        assert row[:2] == ("running" if exhaust else "succeeded", 1)
        assert (row[2] is not None) == exhaust
        assert db.execute(
            text("SELECT count(*) FROM chunks WHERE document_version_id=:v"),
            {"v": receipt.document.version_id},
        ).scalar_one() == (0 if exhaust else 1)


@pytest.mark.parametrize("hold_second_work", [False, True])
def test_watchdog_second_real_work_has_exact_cap_and_live_lease(
    upload_scene, monkeypatch, hold_second_work
):
    """Separate deliberate self-contention regression, not a relaxed success test.

    Historical0cf9f61 demonstrates the old success assertion's deterministic RED.
    This case instead specifies exact yielded/owned live-lease semantics when a
    second real heartbeat holds the gate after the transient holder releases.
    """
    import json
    from threading import Event, Thread, get_ident
    from time import monotonic

    from app.db.leases import heartbeat_job
    from app.documents.transactions import QuotaUnavailable
    from psycopg.pq import TransactionStatus
    from sqlalchemy.exc import SQLAlchemyError

    c = upload_scene
    receipt = perform(c)
    sibling_acquired, sibling_release = Event(), Event()
    state = {"actor": c["actor"], "job": receipt.job.id, "owned": [], "sibling_errors": []}
    calls, closed = [], []

    def sibling():
        try:
            with c["actor_tx"](state["actor"]) as db:
                assert heartbeat_job(db, state["job"], state["token"], lease_seconds=30)
                sibling_acquired.set()
                assert sibling_release.wait(3), "independent heartbeat was not released"
        except (AssertionError, SQLAlchemyError, psycopg.Error, RuntimeError) as exc:
            state["sibling_errors"].append(type(exc).__name__)
            sibling_acquired.set()

    @contextmanager
    def second_contend(db_engine, **kwargs):
        with contend(db_engine, **kwargs) as evidence:

            def before(connection, cursor, statement, parameters, context, many):
                if "SELECT public.quota_gate()" not in statement:
                    return
                if get_ident() == state.get("watch_thread"):
                    state["owned"].append(
                        {
                            "connection": connection,
                            "raw": connection.connection.dbapi_connection,
                            "rolled": False,
                            "idle": False,
                        }
                    )

            def rollback(connection):
                for item in state["owned"]:
                    if item["connection"] is connection:
                        item["rolled"] = True

            def checkin(raw, record):
                for item in state["owned"]:
                    if item["raw"] is raw:
                        item["idle"] = raw.info.transaction_status == TransactionStatus.IDLE
                # Registered AFTER real_contend: it has already rolled back its
                # holder for this exact rejected callback before we acquire work.
                if (
                    len(state["owned"]) == 1
                    and state["owned"][0]["raw"] is raw
                    and "thread" not in state
                ):
                    state["thread"] = Thread(target=sibling, name="b3-independent-heartbeat")
                    state["thread"].start()
                    assert sibling_acquired.wait(3), "second real heartbeat never acquired gate"
                    assert not state["sibling_errors"]
                    if not hold_second_work:
                        sibling_release.set()
                        state["thread"].join(3)
                        assert not state["thread"].is_alive()

            listeners = [
                ("before_cursor_execute", before),
                ("rollback", rollback),
                ("checkin", checkin),
            ]
            for name, fn in listeners:
                event.listen(db_engine, name, fn)
            try:
                yield evidence
            finally:
                sibling_release.set()
                try:
                    if "thread" in state:
                        state["thread"].join(3)
                finally:
                    for name, fn in listeners:
                        event.remove(db_engine, name, fn)
                assert "thread" not in state or not state["thread"].is_alive()
                assert not state["sibling_errors"]

    async def run():
        with _watchdog_test_schedule(monkeypatch) as (Worker, read_started, committed, is_watcher):

            class ObservedWorker(Worker):
                def _claim(self, actor, job_id):
                    claimed = super()._claim(actor, job_id)
                    assert claimed is not None
                    state["token"] = claimed[0]
                    return claimed

                def _pulse(self, actor, job, token):
                    if not is_watcher():
                        return super()._pulse(actor, job, token)
                    state["watch_thread"] = get_ident()
                    started = monotonic()
                    try:
                        return super()._pulse(actor, job, token)
                    except QuotaUnavailable as exc:
                        elapsed = monotonic() - started
                        owned = state["owned"]
                        assert len(owned) == 3 and exc.code == "quota_gate_busy"
                        assert elapsed < 0.25, "deadline exhaustion, not the intended attempt cap"
                        assert all(item["rolled"] and item["idle"] for item in owned)
                        assert all(
                            item["connection"].closed and not item["connection"].in_transaction()
                            for item in owned
                        )
                        state["cap"] = {
                            "attempts": len(owned),
                            "elapsed_ms": round(elapsed * 1000, 3),
                            "deadline_ms": 250,
                            "own_rollback_idle_closed": True,
                        }
                        raise

            class Original:
                async def read(self, actor_id, scope) -> bytes:
                    assert not c["active"]
                    calls.append("read")
                    try:
                        read_started.set()
                        await committed.wait()
                        return b"hello"
                    finally:
                        closed.append(True)

            worker = ObservedWorker(
                c["actor_engine"], store_factory=lambda auth: Original(), heartbeat_seconds=0.03
            )
            with second_contend(c["actor_engine"], target=3, exhaust=False):
                return await asyncio.wait_for(
                    worker.run_one(actor_id=c["actor"], job_id=receipt.job.id), 5
                )

    result = asyncio.run(run())
    assert result == ("yielded" if hold_second_work else "succeeded")
    assert calls == ["read"] and closed == [True]
    assert ("cap" in state) == hold_second_work
    with c["actor_tx"](c["actor"]) as db:
        row = db.execute(
            text(
                "SELECT status,attempts,lease_token,lease_expires_at>clock_timestamp() FROM ingestion_jobs WHERE id=:j"
            ),
            {"j": receipt.job.id},
        ).one()
        assert row[:2] == ("running" if hold_second_work else "succeeded", 1)
        assert row[2] == (state["token"] if hold_second_work else None)
        assert row[3] is (True if hold_second_work else None)
        assert db.execute(
            text("SELECT count(*) FROM chunks WHERE document_version_id=:v"),
            {"v": receipt.document.version_id},
        ).scalar_one() == (0 if hold_second_work else 1)
    print(
        "B3_SYNC_GREEN "
        + json.dumps(
            {
                "hold_second_work": hold_second_work,
                "result": result,
                "cap": state.get("cap"),
                "io_calls": len(calls),
                "io_closed": bool(closed),
                "sibling_errors": state["sibling_errors"],
            }
        )
    )


@pytest.mark.parametrize(
    "code,message",
    [("P0001", "other_error"), ("42501", "quota_capacity"), ("55P03", "other_error")],
)
def test_worker_nearby_server_errors_propagate_once_without_attempt_or_io(
    upload_scene, code, message
):
    from app.db.quota import classify_quota_error
    from sqlalchemy.exc import DBAPIError

    c = upload_scene
    receipt = perform(c)
    invocations, calls = [], []

    def before(connection, cursor, statement, parameters, context, many):
        if "SELECT public.quota_gate()" in statement:
            invocations.append(True)
            with connection.connection.cursor() as probe:
                probe.execute(
                    "SELECT set_config('test.quota_code',%s,true),set_config('test.quota_message',%s,true)",
                    (code, message),
                )
                probe.execute(
                    "DO $$ BEGIN RAISE EXCEPTION USING MESSAGE=current_setting('test.quota_message'), ERRCODE=current_setting('test.quota_code'); END $$"
                )

    class Original:
        async def read(self, actor_id, scope):
            calls.append("read")
            return b"hello"

    event.listen(c["actor_engine"], "before_cursor_execute", before)
    try:
        with pytest.raises((psycopg.Error, DBAPIError)) as caught:
            asyncio.run(
                TextWorker(c["actor_engine"], store_factory=lambda auth: Original()).run_one(
                    actor_id=c["actor"], job_id=receipt.job.id
                )
            )
        assert classify_quota_error(caught.value) is None
    finally:
        event.remove(c["actor_engine"], "before_cursor_execute", before)
    assert invocations == [True] and calls == []
    with c["actor_tx"](c["actor"]) as db:
        assert db.execute(
            text("SELECT status,attempts,lease_token FROM ingestion_jobs WHERE id=:j"),
            {"j": receipt.job.id},
        ).one() == ("queued", 0, None)
