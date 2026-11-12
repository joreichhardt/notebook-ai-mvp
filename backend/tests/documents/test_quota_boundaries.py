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
    from time import sleep

    def checked_sleep(delay):
        assert not upload_scene["active"]
        assert upload_scene["actor_engine"].pool.checkedout() == 0
        assert upload_scene["io_engine"].pool.checkedout() == 0
        sleep(delay)

    monkeypatch.setattr("app.documents.transactions.sleep", checked_sleep)


@contextmanager
def contend(engine, *, target=1, marker="SELECT public.quota_gate()", exhaust=False):
    """Acquire real competing gate immediately before a chosen statement.

    Release transient contention only after the failed transaction rolls back.
    Observe actual XIDs, not manufactured driver exceptions or protected writes.
    """
    holder = psycopg.connect(URL)
    seen, xids, rollbacks = [], [], []
    acquired = False
    released = False

    def before(connection, cursor, statement, parameters, context, many):
        nonlocal acquired
        if marker not in statement:
            return
        seen.append(statement)
        if len(seen) < target or released:
            return
        with connection.connection.cursor() as probe:
            probe.execute("SELECT txid_current()")
            xids.append(probe.fetchone()[0])
        if not acquired:
            holder.execute("SELECT public.quota_gate()")
            acquired = True

    def rollback(connection):
        if acquired and not released:
            rollbacks.append(True)

    def checked_in(connection, record):
        nonlocal released
        if acquired and rollbacks and not released and not exhaust:
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


@pytest.mark.parametrize("exhaust", [False, True])
def test_watchdog_busy_closes_unfinished_io_but_keeps_live_lease(upload_scene, exhaust):
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

    worker = TextWorker(
        c["actor_engine"], store_factory=lambda auth: Original(), heartbeat_seconds=0.03
    )
    with contend(c["actor_engine"], target=3, exhaust=exhaust):
        result = asyncio.run(worker.run_one(actor_id=c["actor"], job_id=receipt.job.id))
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
