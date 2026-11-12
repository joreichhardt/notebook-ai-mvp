"""Real isolated PostgreSQL + explicitly synthetic originals, never Storage acceptance."""

import asyncio
import os
from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest
from app.db.models import Chunk, Document, DocumentVersion, IngestionJob
from app.documents.storage import StorageError
from app.workers.text import TextWorker
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

URL = os.getenv("NOTEBOOK_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL required")


@pytest.fixture
def scene():
    actor, editor, viewer, workspace, notebook = [uuid4() for _ in range(5)]
    with psycopg.connect(URL) as admin:
        for uid in (actor, editor, viewer):
            admin.execute("INSERT INTO app_admissions VALUES (%s)", (uid,))
        admin.execute("INSERT INTO workspaces(id,name) VALUES (%s,'B3 fixture')", (workspace,))
        for uid, role in ((actor, "owner"), (editor, "editor"), (viewer, "viewer")):
            admin.execute("INSERT INTO workspace_members VALUES (%s,%s,%s)", (workspace, uid, role))
        admin.execute("INSERT INTO notebooks VALUES (%s,%s,'text')", (notebook, workspace))
    engine = create_engine(
        make_url(URL).set(
            drivername="postgresql+psycopg", username="notebook_app",
            password=os.getenv("NOTEBOOK_TEST_RUNTIME_PASSWORD") or make_url(URL).password,
        ),
        hide_parameters=True,
    )
    worker = TextWorker(engine)
    yield actor, editor, viewer, workspace, notebook, worker
    engine.dispose()
    with psycopg.connect(URL) as admin:
        admin.execute("DELETE FROM workspaces WHERE id=%s", (workspace,))
        for uid in (actor, editor, viewer):
            admin.execute("DELETE FROM app_admissions WHERE user_id=%s", (uid,))


def persisted_job(scene, data=b"hello", *, key="initial", media="text/plain"):
    actor, _, _, workspace, notebook, worker = scene
    document, version, job = uuid4(), uuid4(), uuid4()
    with worker._session(actor) as db:
        db.add(
            Document(
                id=document,
                workspace_id=workspace,
                notebook_id=notebook,
                created_by=actor,
                filename="example.txt",
                current_version_id=version,
            )
        )
        db.flush()
        db.add(
            DocumentVersion(
                id=version,
                document_id=document,
                notebook_id=notebook,
                workspace_id=workspace,
                version=1,
                media_type=media,
                byte_count=len(data),
                sha256=sha256(data).hexdigest(),
                storage_key=f"{workspace}/{notebook}/{document}/{version}/original",
            )
        )
        db.flush()
        db.add(
            IngestionJob(
                id=job,
                document_version_id=version,
                notebook_id=notebook,
                workspace_id=workspace,
                actor_user_id=actor,
                idempotency_key=key,
            )
        )
    return document, version, job


class SyntheticStore:
    def __init__(self, data):
        self.data = data
        self.calls = 0

    async def read(self, actor_id, scope):
        self.calls += 1
        return self.data


def test_real_job_claim_chunks_and_finish_commit_atomically(scene):
    actor, _, _, _, _, worker = scene
    data = ("ä" * 2200).encode()
    _, version, job = persisted_job(scene, data)
    store = SyntheticStore(data)
    worker.store_factory = lambda authorize: store
    assert asyncio.run(worker.run_one(actor_id=actor, job_id=job)) == "succeeded"
    assert asyncio.run(worker.run_one(actor_id=actor, job_id=job)) == "not_claimed"
    with worker._session(actor) as db:
        rows = db.scalars(
            select(Chunk).where(Chunk.document_version_id == version).order_by(Chunk.ordinal)
        ).all()
        assert [(c.start_offset, c.end_offset) for c in rows] == [(0, 1200), (1000, 2200)]
        assert db.get(IngestionJob, job).status == "succeeded"
        assert db.get(IngestionJob, job).attempts == 1
    assert store.calls == 1


@pytest.mark.parametrize("mode", ["viewer", "other_member", "removed", "tombstone", "new_version"])
def test_current_authorization_and_source_gate_before_storage(scene, mode):
    actor, editor, viewer, workspace, notebook, worker = scene
    document, _, job = persisted_job(scene)
    store = SyntheticStore(b"hello")
    worker.store_factory = lambda auth: store
    invocation_actor = actor
    if mode == "viewer":
        invocation_actor = viewer
    elif mode == "other_member":
        invocation_actor = editor
    elif mode == "removed":
        with psycopg.connect(URL) as admin:
            admin.execute("DELETE FROM workspace_members WHERE user_id=%s", (actor,))
    elif mode == "tombstone":
        with worker._session(actor) as db:
            db.execute(text("SELECT notebook_delete_document(:d)"), {"d": document})
    elif mode == "new_version":
        replacement = uuid4()
        with worker._session(actor) as db:
            db.execute(
                text("UPDATE documents SET current_version_id=:v WHERE id=:d"),
                {"v": replacement, "d": document},
            )
            db.add(
                DocumentVersion(
                    id=replacement,
                    document_id=document,
                    notebook_id=notebook,
                    workspace_id=workspace,
                    version=2,
                    media_type="text/plain",
                    byte_count=5,
                    sha256=sha256(b"hello").hexdigest(),
                    storage_key=f"{workspace}/{notebook}/{document}/{replacement}/original",
                )
            )
    assert asyncio.run(worker.run_one(actor_id=invocation_actor, job_id=job)) == "not_claimed"
    assert store.calls == 0


@pytest.mark.parametrize("mode", ["membership", "delete", "reclaim"])
def test_inflight_revocation_or_reclaimed_lease_cannot_publish(scene, mode):
    actor, _, _, workspace, _, worker = scene
    document, version, job = persisted_job(scene)
    worker.heartbeat_seconds = 0.03

    async def run():
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        class StalledStore:
            async def read(self, who, scope):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        worker.store_factory = lambda auth: StalledStore()
        task = asyncio.create_task(worker.run_one(actor_id=actor, job_id=job))
        await entered.wait()
        # Separate DB connection mutates durable state while I/O is in flight.
        if mode == "membership":
            with psycopg.connect(URL) as admin:
                admin.execute(
                    "DELETE FROM workspace_members WHERE workspace_id=%s AND user_id=%s",
                    (workspace, actor),
                )
        elif mode == "delete":
            with worker._session(actor) as db:
                db.execute(text("SELECT notebook_delete_document(:d)"), {"d": document})
        else:
            with psycopg.connect(URL) as admin:
                admin.execute(
                    "UPDATE ingestion_jobs SET lease_token=%s WHERE id=%s", (uuid4(), job)
                )
        assert await asyncio.wait_for(task, timeout=3) == "lease_lost"
        assert cancelled.is_set()

    asyncio.run(run())
    with psycopg.connect(URL) as admin:
        assert admin.execute(
            "SELECT count(*) FROM chunks WHERE document_version_id=%s", (version,)
        ).fetchone() == (0,)


def test_integrity_failure_is_durable_and_unconfigured_storage_fails_closed(scene):
    actor, _, _, _, _, worker = scene
    _, _, job = persisted_job(scene)
    worker.store_factory = lambda auth: SyntheticStore(b"wrong")
    assert asyncio.run(worker.run_one(actor_id=actor, job_id=job)) == "failed"
    with worker._session(actor) as db:
        row = db.get(IngestionJob, job)
        assert (row.status, row.error_code) == ("failed", "original_integrity_failed")
    _, _, other_job = persisted_job(scene, key="unconfigured")

    class Unconfigured:
        async def read(self, actor, scope):
            raise StorageError("storage_unconfigured")

    worker.store_factory = lambda auth: Unconfigured()
    assert asyncio.run(worker.run_one(actor_id=actor, job_id=other_job)) == "failed"


def test_false_finish_rolls_back_every_chunk(scene, monkeypatch):
    actor, _, _, _, _, worker = scene
    _, version, job = persisted_job(scene, b"x" * 2200)
    worker.store_factory = lambda auth: SyntheticStore(b"x" * 2200)
    monkeypatch.setattr("app.workers.text.finish_job", lambda *a, **kw: False)
    assert asyncio.run(worker.run_one(actor_id=actor, job_id=job)) == "lease_lost"
    with worker._session(actor) as db:
        assert db.scalars(select(Chunk).where(Chunk.document_version_id == version)).all() == []
        assert db.get(IngestionJob, job).status == "running"


def test_actor_concurrency_limit_is_serialized_and_expired_leases_do_not_consume_slot(scene):
    actor, _, _, _, _, worker = scene
    jobs = [persisted_job(scene, key=f"limit-{i}")[2] for i in range(3)]
    assert worker._claim(actor, jobs[0]) is not None
    assert worker._claim(actor, jobs[1]) is not None
    assert worker._claim(actor, jobs[2]) is None
    with psycopg.connect(URL) as admin:
        admin.execute(
            "UPDATE ingestion_jobs SET lease_expires_at=clock_timestamp()-interval '1s' "
            "WHERE id=%s",
            (jobs[0],),
        )
    assert worker._claim(actor, jobs[2]) is not None


def test_external_cancellation_closes_io_and_commits_no_chunks(scene):
    actor, _, _, _, _, worker = scene
    _, version, job = persisted_job(scene)

    async def run():
        entered, closed = asyncio.Event(), asyncio.Event()

        class Stalled:
            async def read(self, who, scope):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        worker.store_factory = lambda auth: Stalled()
        task = asyncio.create_task(worker.run_one(actor_id=actor, job_id=job))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()

    asyncio.run(run())
    with worker._session(actor) as db:
        row = db.get(IngestionJob, job)
        assert row is not None and row.status == "cancelled"
        assert db.scalars(select(Chunk).where(Chunk.document_version_id == version)).all() == []


def test_real_lease_expiry_at_finish_rolls_back_flushed_chunks(scene, monkeypatch):
    from app.db.leases import finish_job

    actor, _, _, _, _, worker = scene
    _, version, job = persisted_job(scene, b"x" * 2200)
    worker.lease_seconds, worker.heartbeat_seconds = 1, 0.1
    worker.store_factory = lambda auth: SyntheticStore(b"x" * 2200)

    def expire_then_finish(db, *args, **kwargs):
        # Chunk INSERTs have already flushed in the real publication transaction.
        db.execute(text("SELECT pg_sleep(1.05)"))
        return finish_job(db, *args, **kwargs)

    monkeypatch.setattr("app.workers.text.finish_job", expire_then_finish)
    assert asyncio.run(worker.run_one(actor_id=actor, job_id=job)) == "lease_lost"
    with worker._session(actor) as db:
        assert db.scalars(select(Chunk).where(Chunk.document_version_id == version)).all() == []
        row = db.get(IngestionJob, job)
        assert row is not None and row.status == "running"


def test_crash_reclaim_is_bounded_and_old_worker_cannot_publish(scene):
    from app.ingestion.text import chunks
    from app.workers.text import LeaseLost

    actor, _, _, _, _, worker = scene
    _, version, job = persisted_job(scene)
    initial = worker._claim(actor, job)
    assert initial is not None
    old_token, source = initial
    with psycopg.connect(URL) as admin:
        admin.execute(
            "UPDATE ingestion_jobs SET lease_expires_at=clock_timestamp()-interval '1s' "
            "WHERE id=%s",
            (job,),
        )
    worker.store_factory = lambda auth: SyntheticStore(b"hello")
    assert asyncio.run(worker.run_one(actor_id=actor, job_id=job)) == "succeeded"
    with pytest.raises(LeaseLost):
        worker._publish(actor, job, old_token, source, chunks("hello"))
    with worker._session(actor) as db:
        row = db.get(IngestionJob, job)
        assert row is not None and row.attempts == 2
        assert len(db.scalars(select(Chunk).where(Chunk.document_version_id == version)).all()) == 1

    _, _, exhausted = persisted_job(scene, key="exhausted")
    with psycopg.connect(URL) as admin:
        admin.execute("UPDATE ingestion_jobs SET attempts=5 WHERE id=%s", (exhausted,))
    assert asyncio.run(worker.run_one(actor_id=actor, job_id=exhausted)) == "not_claimed"


def test_pdf_job_has_explicit_unsupported_failure_without_io(scene):
    actor, _, _, _, _, worker = scene
    _, _, job = persisted_job(scene, b"%PDF-1.7", media="application/pdf")
    store = SyntheticStore(b"%PDF-1.7")
    worker.store_factory = lambda auth: store
    assert asyncio.run(worker.run_one(actor_id=actor, job_id=job)) == "failed"
    assert store.calls == 0
    with worker._session(actor) as db:
        row = db.get(IngestionJob, job)
        assert row is not None and row.error_code == "unsupported_media"


def test_runtime_refuses_superuser_engine_before_claim(scene):
    actor, _, _, _, _, _ = scene
    engine = create_engine(make_url(URL).set(drivername="postgresql+psycopg"), hide_parameters=True)
    try:
        with pytest.raises(RuntimeError, match="unsafe_database_role"):
            asyncio.run(TextWorker(engine).run_one(actor_id=actor, job_id=uuid4()))
    finally:
        engine.dispose()


def test_postcommit_watchdog_loss_does_not_misreport_success(scene):
    """Real separate-connection watchdog reads the terminal row after publication.

    Test-only barriers make both asyncio tasks complete before wait() resumes.
    No lease helper, publication result or DB authorization is mocked.
    """
    import threading

    from app.workers.text import LeaseLost

    actor, _, _, _, _, base = scene
    _, version, job = persisted_job(scene)
    watch_started, published, watch_failed = (threading.Event() for _ in range(3))
    counter_lock = threading.Lock()

    class TimingWorker(TextWorker):
        pulses = 0

        def _pulse(self, actor, job, token):
            with counter_lock:
                self.pulses += 1
                number = self.pulses
            super()._pulse(actor, job, token)
            if number == 2:  # watchdog, after first authorization before Storage read
                watch_started.set()
                assert published.wait(3)
                try:
                    super()._pulse(actor, job, token)
                except LeaseLost:
                    watch_failed.set()
                    raise
                raise AssertionError("terminal job unexpectedly retained running lease")

        def _publish(self, *args, **kwargs):
            super()._publish(*args, **kwargs)
            published.set()
            assert watch_failed.wait(3)

    class TimedOriginal:
        async def read(self, actor_id, scope):
            assert await asyncio.to_thread(watch_started.wait, 3)
            return b"hello"

    worker = TimingWorker(base.engine, store_factory=lambda auth: TimedOriginal(),
                          heartbeat_seconds=0.01)
    assert asyncio.run(worker.run_one(actor_id=actor, job_id=job)) == "succeeded"
    with base._session(actor) as db:
        row = db.get(IngestionJob, job)
        assert row is not None and row.status == "succeeded"
        assert len(db.scalars(select(Chunk).where(Chunk.document_version_id == version)).all()) == 1
    assert watch_failed.is_set()
