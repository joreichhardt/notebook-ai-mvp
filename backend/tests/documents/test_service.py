"""Real head0006/PostgreSQL roles + SYNTHETIC Storage, not Storage-policy proof."""

import asyncio
import os
import secrets
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest
from app.documents.local_storage import LocalStorage, LocalStorageConfig
from app.documents.service import DocumentError, UploadService
from app.documents.storage import StorageError
from app.ingestion.text import TextUpload
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL as DatabaseURL
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

URL = os.getenv("NOTEBOOK_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="isolated migrated PostgreSQL required")


@pytest.fixture
def upload_scene(tmp_path):
    actor, editor, viewer, workspace, notebook = [uuid4() for _ in range(5)]
    login = "b3_io_" + uuid4().hex
    material = secrets.token_hex(24)
    with psycopg.connect(URL) as admin:
        for uid in (actor, editor, viewer):
            admin.execute("INSERT INTO app_admissions VALUES (%s)", (uid,))
        admin.execute("INSERT INTO workspaces(id,name) VALUES (%s,'B3 upload')", (workspace,))
        for uid, role in ((actor, "owner"), (editor, "editor"), (viewer, "viewer")):
            admin.execute("INSERT INTO workspace_members VALUES (%s,%s,%s)", (workspace, uid, role))
        admin.execute("INSERT INTO notebooks VALUES (%s,%s,'B3')", (notebook, workspace))
        # Synthetic fixture login only, NOT production role provisioning.
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
                sql.Identifier(login), sql.Literal(material)
            )
        )
        admin.execute(sql.SQL("GRANT notebook_storage_io TO {}").format(sql.Identifier(login)))
    actor_engine = create_engine(
        make_url(URL).set(drivername="postgresql+psycopg", username="notebook_app"),
        hide_parameters=True,
    )
    base = make_url(URL)
    credentials = tmp_path / "io.pgpass"
    credentials.touch(mode=0o600)
    parts = [
        base.host or "localhost",
        str(base.port or 5432),
        base.database or "notebook_b3_test",
        login,
        material,
    ]
    credentials.write_text(
        ":".join(p.replace("\\", "\\\\").replace(":", "\\:") for p in parts) + "\n"
    )
    io_engine = create_engine(
        DatabaseURL.create(
            "postgresql+psycopg",
            username=login,
            host=base.host,
            port=base.port,
            database=base.database,
        ),
        connect_args={"passfile": str(credentials)},
        hide_parameters=True,
    )
    active = []

    @contextmanager
    def actor_tx(uid):
        active.append("actor")
        try:
            with Session(actor_engine) as db, db.begin():
                db.execute(
                    text("SELECT set_config('request.jwt.claim.sub',:u,true)"), {"u": str(uid)}
                )
                yield db
        finally:
            active.remove("actor")

    @contextmanager
    def io_tx():
        active.append("io")
        try:
            with Session(io_engine) as db, db.begin():
                db.execute(text("SET LOCAL ROLE notebook_storage_io"))
                yield db
        finally:
            active.remove("io")

    class SyntheticStore:
        def __init__(self):
            self.values = {}
            self.actions = []
            self.mode = "ok"

        async def put(self, actor_id, scope, data, media_type):
            assert not active, "no DB transaction may span external I/O"
            with psycopg.connect(URL) as check:
                assert check.execute(
                    "SELECT count(*) FROM documents WHERE id=%s", (scope.document_id,)
                ).fetchone() == (0,)
                assert check.execute(
                    "SELECT count(*) FROM ingestion_jobs WHERE document_version_id=%s",
                    (scope.version_id,),
                ).fetchone() == (0,)
            self.actions.append("put")
            assert scope.key not in self.values, "non-upsert only"
            self.values[scope.key] = data
            if self.mode == "ambiguous_put":
                raise StorageError()
            if self.mode == "revoke":
                with psycopg.connect(URL) as admin:
                    admin.execute("DELETE FROM workspace_members WHERE user_id=%s", (actor_id,))

        async def read(self, actor_id, scope):
            assert not active
            self.actions.append("read")
            if self.mode == "expired":
                await asyncio.sleep(1.05)  # Real short RPC lease expires; no clock rewrite.
            return b"wrong" if self.mode == "corrupt" else self.values[scope.key]

    store = SyntheticStore()
    service = UploadService(actor_tx=actor_tx, io_tx=io_tx, store_factory=lambda authorize: store)
    yield locals()
    actor_engine.dispose()
    io_engine.dispose()
    credentials.unlink()
    with psycopg.connect(URL) as admin:
        admin.execute("DELETE FROM workspaces WHERE id=%s", (workspace,))
        for uid in (actor, editor, viewer):
            admin.execute("DELETE FROM app_admissions WHERE user_id=%s", (uid,))
        admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(login)))


def sample():
    return TextUpload("sample.txt", "text/plain", b"hello", "hello", sha256(b"hello").hexdigest())


def perform(c, *, actor=None, key="upload-1", upload=None, **options):
    return asyncio.run(
        c["service"].upload(
            actor_id=actor or c["actor"],
            notebook_id=c["notebook"],
            idempotency_key=key,
            upload=upload or sample(),
            **options,
        )
    )


def test_local_original_persists_through_durable_upload_and_replay(upload_scene, tmp_path):
    c = upload_scene
    root = tmp_path / "originals"
    root.mkdir(mode=0o700)
    config = LocalStorageConfig(root)
    c["service"].store_factory = lambda authorize: LocalStorage(config, authorize=authorize)
    result = perform(c)
    assert result.document.status == "queued" and result.job.status == "queued"
    assert result == perform(c)
    originals = list(root.glob("*/*/*/*/original"))
    assert len(originals) == 1 and originals[0].read_bytes() == sample().data
    assert originals[0].stat().st_mode & 0o777 == 0o600
    assert not list(root.glob("*/*/*/*/.*.upload"))
    assert c["service"].list_documents(actor_id=c["viewer"], notebook_id=c["notebook"]) == [
        result.document
    ]
    with pytest.raises(DocumentError):
        perform(c, actor=c["viewer"], key="viewer-denied")
    assert list(root.glob("*/*/*/*/original")) == originals


def test_local_original_revocation_before_publication_leaves_no_visible_upload(
    upload_scene, tmp_path
):
    c = upload_scene
    root = tmp_path / "originals"
    root.mkdir(mode=0o700)

    def factory(authorize):
        writes = 0

        async def revoke(actor, scope, action):
            nonlocal writes
            if action == "write":
                writes += 1
                if writes == 2:
                    with psycopg.connect(URL) as admin:
                        admin.execute(
                            "DELETE FROM workspace_members WHERE user_id=%s", (c["actor"],)
                        )
            await authorize(actor, scope, action)

        return LocalStorage(LocalStorageConfig(root), authorize=revoke)

    c["service"].store_factory = factory
    with pytest.raises(DocumentError):
        perform(c)
    assert not list(root.glob("*/*/*/*/original"))
    assert not list(root.glob("*/*/*/*/.*.upload"))
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT count(*) FROM documents WHERE notebook_id=%s", (c["notebook"],)
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT count(*) FROM ingestion_jobs WHERE notebook_id=%s", (c["notebook"],)
        ).fetchone() == (0,)


def test_extracted_source_text_is_bounded_current_and_revocable(upload_scene):
    # Privileged text fixtures test reads/RLS, NOT extraction or a running parser.
    c = upload_scene
    receipt = perform(c)
    arguments = {
        "actor_id": c["viewer"],
        "notebook_id": c["notebook"],
        "document_id": receipt.document.id,
    }
    queued = c["service"].source_text(**arguments)
    assert queued.document.status == "queued" and queued.items == []
    # Establish the guarded ready projection through the accepted worker, rather
    # than bypassing version_processing_guard. HostedCI uses committed code only;
    # this case must not execute the parked local S2b worker.
    from app.workers.text import TextWorker

    worker = TextWorker(c["actor_engine"], store_factory=lambda authorize: c["store"])
    assert asyncio.run(worker.run_one(actor_id=c["actor"], job_id=receipt.job.id)) == "succeeded"
    content = "x" * 4005
    with psycopg.connect(URL) as admin:
        admin.execute(
            "DELETE FROM chunks WHERE document_version_id=%s", (receipt.document.version_id,)
        )
        for ordinal in range(21):
            admin.execute(
                "INSERT INTO chunks(id,document_version_id,document_id,notebook_id,workspace_id,"
                "ordinal,text,sha256,page,start_offset,end_offset) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s)",
                (
                    uuid4(),
                    receipt.document.version_id,
                    receipt.document.id,
                    c["notebook"],
                    c["workspace"],
                    ordinal,
                    content,
                    sha256(content.encode()).hexdigest(),
                    ordinal * len(content),
                    (ordinal + 1) * len(content),
                ),
            )
    first = c["service"].source_text(**arguments)
    assert len(first.items) == 20 and first.next_ordinal == 20
    assert all(len(item.text) == 4000 and item.truncated for item in first.items)
    last = c["service"].source_text(**arguments, after=20)
    assert len(last.items) == 1 and last.next_ordinal is None
    with psycopg.connect(URL) as admin:
        admin.execute(
            "UPDATE documents SET current_version_id=NULL WHERE id=%s", (receipt.document.id,)
        )
    with pytest.raises(DocumentError, match="document_not_found"):
        c["service"].source_text(**arguments)
    with psycopg.connect(URL) as admin:
        admin.execute(
            "UPDATE documents SET current_version_id=%s WHERE id=%s",
            (receipt.document.version_id, receipt.document.id),
        )
    with pytest.raises(DocumentError):
        c["service"].source_text(**{**arguments, "notebook_id": uuid4()})
    with pytest.raises(DocumentError):
        c["service"].source_text(**{**arguments, "actor_id": uuid4()})
    with psycopg.connect(URL) as admin:
        admin.execute("DELETE FROM workspace_members WHERE user_id=%s", (c["viewer"],))
    with pytest.raises(DocumentError):
        c["service"].source_text(**arguments)
    # Even existing stored chunks cannot be read through a deleted source.
    with psycopg.connect(URL) as admin:
        admin.execute(
            "UPDATE documents SET deleted_at=clock_timestamp() WHERE id=%s", (receipt.document.id,)
        )
    with pytest.raises(DocumentError, match="document_not_found"):
        c["service"].source_text(**{**arguments, "actor_id": c["actor"]})


def test_http_upload_and_list_use_local_runtime_with_real_database(
    upload_scene, tmp_path, monkeypatch
):
    # Real HTTP/parser/CSRF/service/DB/filesystem; verified identity is synthetic.
    from app.auth.settings import Settings
    from app.auth.supabase import Identity
    from app.capabilities import router as capabilities_router
    from app.documents import router as reader
    from app.documents import runtime, upload_router
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    c = upload_scene
    root = tmp_path / "http-originals"
    root.mkdir(mode=0o700)
    secret = tmp_path / "io-url"
    secret.touch(mode=0o600)
    secret.write_text(
        c["io_engine"].url.set(password=c["material"]).render_as_string(hide_password=False)
    )
    settings = runtime.UploadSettings(
        local_uploads_enabled=True,
        local_storage_root=str(root),
        storage_io_database_url_file=str(secret),
    )
    monkeypatch.setattr(runtime, "upload_settings", lambda: settings)
    monkeypatch.setattr(
        upload_router, "get_settings", lambda: Settings(public_origin="https://test")
    )
    user = Identity(id=c["actor"], email="synthetic@example.test")

    async def verified(request):
        return user

    def sessions(original):
        with c["actor_tx"](original.id) as db:
            yield db

    monkeypatch.setattr(upload_router, "identity", verified)
    monkeypatch.setattr(reader, "identity", verified)
    monkeypatch.setattr(upload_router, "authenticated_session", sessions)
    monkeypatch.setattr(reader, "authenticated_session", sessions)
    app = FastAPI()
    app.include_router(capabilities_router)
    app.include_router(upload_router.router)
    app.include_router(reader.router)

    async def requests():
        nonlocal user
        async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
            assert (await client.get("/api/capabilities")).json()["documents"] is True
            url = f"/api/notebooks/{c['notebook']}/documents"
            headers = {
                "Origin": "https://test",
                "Cookie": "__Host-notebook_csrf=test",
                "X-CSRF-Token": "test",
                "Idempotency-Key": "local-http-upload",
            }
            first = await client.post(
                url, headers=headers, files={"file": ("note.txt", b"hello", "text/plain")}
            )
            assert first.status_code == 202
            replay = await client.post(
                url, headers=headers, files={"file": ("note.txt", b"hello", "text/plain")}
            )
            assert replay.status_code == 202 and replay.json() == first.json()
            listing = await client.get(url)
            assert listing.status_code == 200
            assert listing.json()["items"] == [first.json()["document"]]
            user = Identity(id=c["viewer"], email="viewer@example.test")
            denied = await client.post(
                url, headers=headers, files={"file": ("note.txt", b"hello", "text/plain")}
            )
            assert denied.status_code == 403

    runtime.local_upload_runtime.cache_clear()
    configured = None
    try:
        configured = runtime.local_upload_runtime()
        asyncio.run(requests())
        originals = list(root.glob("*/*/*/*/original"))
        assert len(originals) == 1 and originals[0].read_bytes() == b"hello"
    finally:
        if configured is not None:
            configured.engine.dispose()
        runtime.local_upload_runtime.cache_clear()


def test_complete_storage_then_durable_finalize_replay_and_member_projection(upload_scene):
    c = upload_scene
    result = perform(c)
    assert result.document.status == "queued" and result.job.status == "queued"
    assert result == perform(c)
    assert c["store"].actions == ["put", "read"]
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT status,actor_user_id FROM ingestion_jobs WHERE id=%s", (result.job.id,)
        ).fetchone() == ("queued", c["actor"])
    members = c["service"].list_documents(actor_id=c["viewer"], notebook_id=c["notebook"])
    assert members == [result.document]
    assert set(members[0].model_dump()) == {
        "id",
        "notebook_id",
        "filename",
        "version_id",
        "status",
        "error_code",
    }
    with c["actor_tx"](c["viewer"]) as db:
        assert (
            db.execute(
                text("SELECT id FROM ingestion_jobs WHERE id=:j"), {"j": result.job.id}
            ).all()
            == []
        )


def test_idempotency_mismatch_rejected_without_second_external_write(upload_scene):
    c = upload_scene
    first = perform(c)
    with pytest.raises(DocumentError) as error:
        perform(c, upload=replace(sample(), filename="different.txt"))
    assert (error.value.status_code, error.value.code) == (409, "upload_idempotency_conflict")
    assert perform(c) == first and c["store"].actions == ["put", "read"]


@pytest.mark.parametrize("mode", ["ambiguous_put", "corrupt", "revoke", "expired"])
def test_partial_external_failure_never_creates_visible_document_or_job(
    upload_scene, mode, monkeypatch
):
    c = upload_scene
    if mode == "expired":
        from app.db.uploads import claim_upload_io

        monkeypatch.setattr(
            "app.documents.service.claim_upload_io",
            lambda db, intent_id, **kwargs: claim_upload_io(db, intent_id, lease_seconds=1),
        )
    c["store"].mode = mode
    with pytest.raises(DocumentError):
        perform(c)
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT count(*) FROM documents WHERE notebook_id=%s", (c["notebook"],)
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT count(*) FROM ingestion_jobs WHERE notebook_id=%s", (c["notebook"],)
        ).fetchone() == (0,)
        # Retained exact key for durable reconciliation, never best-effort erased.
        row = db.execute(
            "SELECT state,storage_key FROM upload_intents WHERE notebook_id=%s", (c["notebook"],)
        ).fetchone()
        assert row is not None and row[1] in c["store"].values
    if mode != "revoke":
        with pytest.raises(DocumentError):
            perform(c)  # Single-use claim: ambiguous/incomplete replay cannot re-PUT.
        assert c["store"].actions.count("put") == 1


def test_viewer_and_unsupported_options_have_no_external_effect(upload_scene):
    c = upload_scene
    with pytest.raises(DocumentError) as error:
        perform(c, actor=c["viewer"])
    assert error.value.status_code == 403
    with pytest.raises(DocumentError, match="unsupported_embedding"):
        perform(c, connection_id=uuid4(), index_profile_id=uuid4())
    with pytest.raises(DocumentError, match="unsupported_replacement"):
        perform(c, document_id=uuid4())
    assert c["store"].actions == []


def test_committed_upload_runs_to_member_safe_ready_without_foreign_job_reads(upload_scene):
    from app.workers.text import TextWorker

    c = upload_scene
    receipt = perform(c)
    worker = TextWorker(c["actor_engine"], store_factory=lambda auth: c["store"])
    assert asyncio.run(worker.run_one(actor_id=c["actor"], job_id=receipt.job.id)) == "succeeded"
    rows = c["service"].list_documents(actor_id=c["editor"], notebook_id=c["notebook"])
    assert len(rows) == 1 and rows[0].status == "ready" and rows[0].error_code is None
    with c["actor_tx"](c["editor"]) as db:
        assert (
            db.execute(
                text("SELECT id FROM ingestion_jobs WHERE id=:j"), {"j": receipt.job.id}
            ).all()
            == []
        )


def test_finalize_commit_failure_is_not_accepted_and_stored_replay_never_reputs(upload_scene):
    from sqlalchemy.exc import SQLAlchemyError

    c = upload_scene

    @contextmanager
    def fail_final_commit(uid):
        with c["actor_tx"](uid) as db:
            yield db
            state = db.execute(
                text("SELECT state FROM upload_intents WHERE notebook_id=:n"), {"n": c["notebook"]}
            ).scalar_one_or_none()
            if state == "finalized":
                raise SQLAlchemyError("synthetic pre-commit failure")

    broken = UploadService(
        actor_tx=fail_final_commit, io_tx=c["io_tx"], store_factory=lambda auth: c["store"]
    )
    with pytest.raises(DocumentError, match="database_unavailable"):
        asyncio.run(
            broken.upload(
                actor_id=c["actor"],
                notebook_id=c["notebook"],
                idempotency_key="upload-1",
                upload=sample(),
            )
        )
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT count(*) FROM documents WHERE notebook_id=%s", (c["notebook"],)
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT state FROM upload_intents WHERE notebook_id=%s", (c["notebook"],)
        ).fetchone() == ("stored",)
    assert perform(c).document.status == "queued"
    assert c["store"].actions == ["put", "read"]


def test_concurrent_incomplete_replay_and_cancellation_do_not_reassign_key(upload_scene):
    c = upload_scene

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class Blocked:
            async def put(self, actor_id, scope, data, media_type):
                await c["store"].put(actor_id, scope, data, media_type)
                entered.set()
                await release.wait()

            async def read(self, actor_id, scope):
                return await c["store"].read(actor_id, scope)

        service = UploadService(
            actor_tx=c["actor_tx"], io_tx=c["io_tx"], store_factory=lambda auth: Blocked()
        )

        async def attempt(key="concurrent"):
            return await service.upload(
                actor_id=c["actor"], notebook_id=c["notebook"], idempotency_key=key, upload=sample()
            )

        first = asyncio.create_task(attempt())
        await entered.wait()
        with pytest.raises(DocumentError, match="upload_in_progress"):
            await attempt()
        release.set()
        receipt = await first
        assert await attempt() == receipt
        assert c["store"].actions == ["put", "read"]

        entered.clear()
        release.clear()
        second = asyncio.create_task(attempt("cancelled"))
        await entered.wait()
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        with psycopg.connect(URL) as db:
            assert db.execute(
                "SELECT count(*) FROM ingestion_jobs WHERE notebook_id=%s", (c["notebook"],)
            ).fetchone() == (1,)
            assert db.execute(
                "SELECT state FROM upload_intents WHERE notebook_id=%s AND idempotency_key='cancelled'",
                (c["notebook"],),
            ).fetchone() == ("reserved",)

    asyncio.run(run())


def test_member_failure_dto_does_not_expose_private_job_error(upload_scene):
    from app.workers.text import TextWorker

    c = upload_scene
    receipt = perform(c)
    c["store"].mode = "corrupt"
    worker = TextWorker(c["actor_engine"], store_factory=lambda auth: c["store"])
    assert asyncio.run(worker.run_one(actor_id=c["actor"], job_id=receipt.job.id)) == "failed"
    dto = c["service"].list_documents(actor_id=c["viewer"], notebook_id=c["notebook"])[0]
    assert (dto.status, dto.error_code) == ("failed", "processing_failed")
    assert "original_integrity_failed" not in dto.model_dump_json()
    with c["actor_tx"](c["actor"]) as db:
        assert (
            db.execute(
                text("SELECT error_code FROM ingestion_jobs WHERE id=:j"), {"j": receipt.job.id}
            ).scalar_one()
            == "original_integrity_failed"
        )


def test_unconfigured_or_incorrect_transaction_boundaries_fail_closed(upload_scene):
    c = upload_scene
    args = {
        "actor_id": c["actor"],
        "notebook_id": c["notebook"],
        "idempotency_key": "guards",
        "upload": sample(),
    }
    unconfigured = UploadService(actor_tx=c["actor_tx"])
    with pytest.raises(DocumentError, match="storage_unconfigured"):
        asyncio.run(unconfigured.upload(**args))
    wrong_actor = UploadService(
        actor_tx=lambda actor: c["actor_tx"](c["editor"]),
        io_tx=c["io_tx"],
        store_factory=lambda auth: c["store"],
    )
    with pytest.raises(DocumentError, match="unsafe_actor_context"):
        asyncio.run(wrong_actor.upload(**args))
    wrong_io = UploadService(
        actor_tx=c["actor_tx"],
        io_tx=lambda: c["actor_tx"](c["actor"]),
        store_factory=lambda auth: c["store"],
    )
    with pytest.raises(DocumentError, match="unsafe_database_role"):
        asyncio.run(wrong_io.upload(**args))
    assert c["store"].actions == []
