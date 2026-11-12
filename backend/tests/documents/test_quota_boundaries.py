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
        nonlocal released
        if acquired and not released:
            rollbacks.append(True)
            if not exhaust:
                holder.rollback()
                released = True

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "rollback", rollback)
    try:
        yield seen, xids, rollbacks
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "rollback", rollback)
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
