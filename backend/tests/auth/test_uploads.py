"""A5 real PostgreSQL seams; setup is privileged, assertions use runtime roles."""

import os
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

URL = os.getenv("NOTEBOOK_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL URL not configured")


@pytest.fixture
def scope():
    actor, editor, viewer, foreign, workspace, notebook = [uuid4() for _ in range(6)]
    with psycopg.connect(URL) as db:
        for user in (actor, editor, viewer, foreign):
            db.execute("INSERT INTO app_admissions VALUES (%s)", (user,))
        db.execute("INSERT INTO workspaces(id,name) VALUES (%s,'A5')", (workspace,))
        for user, role in ((actor, "owner"), (editor, "editor"), (viewer, "viewer")):
            db.execute("INSERT INTO workspace_members VALUES (%s,%s,%s)", (workspace, user, role))
        db.execute("INSERT INTO notebooks VALUES (%s,%s,'A5')", (notebook, workspace))
    yield actor, editor, viewer, foreign, workspace, notebook
    with psycopg.connect(URL) as db:
        db.execute("DELETE FROM workspaces WHERE id=%s", (workspace,))
        db.execute(
            "DELETE FROM app_admissions WHERE user_id=ANY(%s)", ([actor, editor, viewer, foreign],)
        )
        # Ledger tombstones deliberately survive parent deletion; fixture disposal only.
        db.execute("DELETE FROM upload_intents WHERE workspace_id=%s", (workspace,))


@contextmanager
def app(actor=None):
    from sqlalchemy.engine import make_url

    url = make_url(URL).set(drivername="postgresql+psycopg", username="notebook_app")
    engine = create_engine(url)
    try:
        with Session(engine) as session, session.begin():
            if actor:
                session.execute(
                    text("SELECT set_config('request.jwt.claim.sub',:u,true)"), {"u": str(actor)}
                )
            yield session
    finally:
        engine.dispose()


def reserve(session, notebook, **changes):
    from app.db.uploads import reserve_upload

    return reserve_upload(
        session,
        notebook,
        **{
            "idempotency_key": "fixture-key",
            "filename": "sample.txt",
            "sha256": "a" * 64,
            "byte_count": 5,
            "media_type": "text/plain",
            **changes,
        },
    )


@contextmanager
def io():
    # Privileged fixture uses SET ROLE solely to provision a NOLOGIN capability.
    # App impersonation denial is tested separately using a direct runtime login.
    with psycopg.connect(URL) as db:
        db.execute("SET ROLE notebook_storage_io")
        yield db


def confirm(intent):
    with io() as db:
        row = db.execute("SELECT * FROM public.claim_upload_io(%s,60)", (intent.id,)).fetchone()
        assert row is not None
        token = row[1]
    with io() as db:
        assert db.execute(
            "SELECT public.confirm_upload_io(%s,%s,%s,5)", (intent.id, token, "a" * 64)
        ).fetchone() == (True,)
    return token


def test_confirm_then_finalize_atomically_creates_current_source_and_private_job(scope):
    from app.db.uploads import finalize_upload

    actor, editor, _, _, _, notebook = scope
    with app(actor) as session:
        intent = reserve(session, notebook)
    with app(actor) as session, pytest.raises(ValueError):
        finalize_upload(session, intent.id)
    confirm(intent)
    with app(actor) as session:
        result = finalize_upload(session, intent.id)
        assert result.document_id == intent.document_id
    with app(actor) as session:
        assert finalize_upload(session, intent.id) == result
        assert (
            session.execute(
                text("SELECT status FROM ingestion_jobs WHERE id=:j"), {"j": intent.job_id}
            ).scalar_one()
            == "queued"
        )
    with app(editor) as session:
        assert session.execute(
            text("SELECT processing_status,error_code FROM document_versions WHERE id=:v"),
            {"v": intent.version_id},
        ).one() == ("queued", None)
        assert (
            session.execute(
                text("SELECT id FROM ingestion_jobs WHERE id=:j"), {"j": intent.job_id}
            ).first()
            is None
        )


def test_reserve_is_durable_without_visible_source_and_compares_request(scope):
    from app.db.uploads import UploadConflict

    actor, _, _, _, workspace, notebook = scope
    with app(actor) as session:
        intent = reserve(session, notebook)
        assert intent.state == "reserved"
        assert (
            intent.storage_key
            == f"{workspace}/{notebook}/{intent.document_id}/{intent.version_id}/original"
        )
        assert (
            session.execute(
                text("SELECT count(*) FROM documents WHERE notebook_id=:n"), {"n": notebook}
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text("SELECT count(*) FROM ingestion_jobs WHERE notebook_id=:n"), {"n": notebook}
            ).scalar_one()
            == 0
        )
    with app(actor) as session:
        assert reserve(session, notebook).id == intent.id
        with pytest.raises(UploadConflict):
            reserve(session, notebook, sha256="b" * 64)


@pytest.mark.parametrize("who", [None, 2, 3])
def test_reservation_denies_no_context_viewer_foreign(scope, who):
    from app.db.uploads import UploadUnavailable

    with app(scope[who] if who is not None else None) as session, pytest.raises(UploadUnavailable):
        reserve(session, scope[5])


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE upload_intents SET state='stored' WHERE id=:i",
        "UPDATE upload_intents SET state='finalized' WHERE id=:i",
        "UPDATE upload_intents SET stored_at=clock_timestamp() WHERE id=:i",
        "UPDATE upload_intents SET write_token=gen_random_uuid() WHERE id=:i",
        "DELETE FROM upload_intents WHERE id=:i",
        "SELECT * FROM claim_upload_io(:i,60)",
        "SELECT * FROM claim_cleanup(:i,60)",
        "SELECT * FROM list_cleanup(1)",
        "SET ROLE notebook_storage_io",
        "SET ROLE notebook_upload_io_owner",
        "SET ROLE notebook_upload_finalize",
    ],
)
def test_runtime_cannot_spoof_lifecycle_or_assume_service_roles(scope, sql):
    from sqlalchemy.exc import DBAPIError

    with app(scope[0]) as session:
        intent = reserve(session, scope[5])
    with pytest.raises(DBAPIError), app(scope[0]) as session:
        session.execute(text(sql), {"i": intent.id})


@pytest.mark.parametrize(
    "column,value",
    [
        ("state", "'stored'"),
        ("id", "gen_random_uuid()"),
        ("actor_user_id", "gen_random_uuid()"),
        ("stored_at", "clock_timestamp()"),
    ],
)
def test_runtime_cannot_insert_preconfirmed_receipt_or_identity(scope, column, value):
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), app(scope[0]) as session:
        session.execute(
            text(
                f"INSERT INTO upload_intents(workspace_id,notebook_id,idempotency_key,filename,sha256,byte_count,media_type,{column}) VALUES(:w,:n,'spoof','x',:h,5,'text/plain',{value})"
            ),
            {"w": scope[4], "n": scope[5], "h": "a" * 64},
        )


def insert_bound_rows(session, intent, actor):
    session.execute(
        text(
            "INSERT INTO documents(id,workspace_id,notebook_id,created_by,filename,current_version_id) VALUES(:d,:w,:n,:a,'sample.txt',:v)"
        ),
        {
            "d": intent.document_id,
            "w": intent.workspace_id,
            "n": intent.notebook_id,
            "a": actor,
            "v": intent.version_id,
        },
    )
    session.execute(
        text(
            "INSERT INTO document_versions(id,document_id,workspace_id,notebook_id,version,media_type,byte_count,sha256,storage_key) VALUES(:v,:d,:w,:n,1,'text/plain',5,:h,:k)"
        ),
        {
            "v": intent.version_id,
            "d": intent.document_id,
            "w": intent.workspace_id,
            "n": intent.notebook_id,
            "h": intent.sha256,
            "k": intent.storage_key,
        },
    )
    session.execute(
        text(
            "INSERT INTO ingestion_jobs(id,document_version_id,workspace_id,notebook_id,actor_user_id,idempotency_key) VALUES(:j,:v,:w,:n,:a,'fixture-key')"
        ),
        {
            "j": intent.job_id,
            "v": intent.version_id,
            "w": intent.workspace_id,
            "n": intent.notebook_id,
            "a": actor,
        },
    )


@pytest.mark.parametrize("receipt,invoke", [(False, False), (False, True), (True, False)])
def test_commit_guard_rejects_bound_rows_even_ignored_false_or_missing_rpc(scope, receipt, invoke):
    from sqlalchemy.exc import IntegrityError

    with app(scope[0]) as session:
        intent = reserve(session, scope[5])
    if receipt:
        confirm(intent)
    with pytest.raises(IntegrityError, match="upload_not_finalized"), app(scope[0]) as session:
        insert_bound_rows(session, intent, scope[0])
        if invoke:
            assert (
                session.execute(text("SELECT finalize_upload(:i)"), {"i": intent.id}).scalar_one()
                is False
            )
    with app(scope[0]) as session:
        assert (
            session.execute(
                text("SELECT id FROM documents WHERE id=:d"), {"d": intent.document_id}
            ).first()
            is None
        )


@pytest.mark.parametrize(
    "revocation", ["admission", "membership", "viewer", "notebook", "workspace"]
)
def test_revocation_and_parent_cascade_retain_reconcilable_keys(scope, revocation):
    from app.db.uploads import UploadUnavailable, finalize_upload

    with app(scope[0]) as session:
        intent = reserve(session, scope[5])
    confirm(intent)
    with psycopg.connect(URL) as db:
        if revocation == "admission":
            db.execute("DELETE FROM app_admissions WHERE user_id=%s", (scope[0],))
        elif revocation == "membership":
            # Avoid last-owner constraint: fixture adds another owner first.
            db.execute(
                "UPDATE workspace_members SET role='owner' WHERE user_id=%s AND workspace_id=%s",
                (scope[1], scope[4]),
            )
            db.execute(
                "DELETE FROM workspace_members WHERE user_id=%s AND workspace_id=%s",
                (scope[0], scope[4]),
            )
        elif revocation == "viewer":
            db.execute(
                "UPDATE workspace_members SET role='owner' WHERE user_id=%s AND workspace_id=%s",
                (scope[1], scope[4]),
            )
            db.execute(
                "UPDATE workspace_members SET role='viewer' WHERE user_id=%s AND workspace_id=%s",
                (scope[0], scope[4]),
            )
        elif revocation == "notebook":
            db.execute("DELETE FROM notebooks WHERE id=%s", (scope[5],))
        else:
            db.execute("DELETE FROM workspaces WHERE id=%s", (scope[4],))
        assert db.execute(
            "SELECT state,storage_key FROM upload_intents WHERE id=%s", (intent.id,)
        ).fetchone() == ("cleanup_pending", intent.storage_key)
        db.execute(
            "UPDATE upload_intents SET reconcile_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (intent.id,),
        )
    with app(scope[0]) as session, pytest.raises(UploadUnavailable):
        finalize_upload(session, intent.id)
    with io() as db:
        ids = db.execute("SELECT * FROM list_cleanup(100)").fetchall()
        assert (intent.id,) in ids
        cleanup = db.execute("SELECT * FROM claim_cleanup(%s,60)", (intent.id,)).fetchone()
        assert cleanup is not None and cleanup[2] == intent.storage_key
    with io() as db:
        assert db.execute("SELECT finish_cleanup(%s,%s)", (intent.id, uuid4())).fetchone() == (
            False,
        )
        assert db.execute("SELECT finish_cleanup(%s,%s)", (intent.id, cleanup[1])).fetchone() == (
            True,
        )
    with psycopg.connect(URL) as db:
        db.execute(
            "UPDATE upload_intents SET reconcile_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (intent.id,),
        )
    with io() as db:
        assert (intent.id,) in db.execute("SELECT * FROM list_cleanup(100)").fetchall()
        assert db.execute("SELECT * FROM claim_upload_io(%s,60)", (intent.id,)).fetchone() is None


def test_write_claim_never_reassigned_and_expired_confirmation_fails(scope):
    with app(scope[0]) as session:
        intent = reserve(session, scope[5])
    with io() as db:
        claim = db.execute("SELECT * FROM claim_upload_io(%s,1)", (intent.id,)).fetchone()
        assert claim is not None
        assert db.execute(
            "SELECT confirm_upload_io(%s,%s,%s,5)", (intent.id, claim[1], "b" * 64)
        ).fetchone() == (False,)
    with psycopg.connect(URL) as db:
        db.execute(
            "UPDATE upload_intents SET write_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (intent.id,),
        )
    with io() as db:
        assert db.execute("SELECT * FROM claim_upload_io(%s,60)", (intent.id,)).fetchone() is None
        assert db.execute(
            "SELECT confirm_upload_io(%s,%s,%s,5)", (intent.id, claim[1], "a" * 64)
        ).fetchone() == (False,)


def test_active_finalized_object_is_never_cleanup_claimable(scope):
    from app.db.uploads import finalize_upload

    with app(scope[0]) as session:
        intent = reserve(session, scope[5])
    confirm(intent)
    with app(scope[0]) as session:
        finalize_upload(session, intent.id)
    with io() as db:
        assert db.execute("SELECT * FROM claim_cleanup(%s,60)", (intent.id,)).fetchone() is None
        assert (intent.id,) not in db.execute("SELECT * FROM list_cleanup(100)").fetchall()
    # Uploader admission loss does NOT delete shared finalized originals.
    with psycopg.connect(URL) as db:
        db.execute("DELETE FROM app_admissions WHERE user_id=%s", (scope[0],))
        assert db.execute(
            "SELECT state FROM upload_intents WHERE id=%s", (intent.id,)
        ).fetchone() == ("finalized",)


def finalized(scope):
    from app.db.uploads import finalize_upload

    with app(scope[0]) as session:
        intent = reserve(session, scope[5])
    confirm(intent)
    with app(scope[0]) as session:
        finalize_upload(session, intent.id)
    return intent


@pytest.mark.parametrize("operation", ["reserve", "finalize"])
def test_concurrent_identical_requests_return_same_ids(scope, operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.db.uploads import finalize_upload
    from sqlalchemy import event

    intent = None
    if operation == "finalize":
        with app(scope[0]) as session:
            intent = reserve(session, scope[5])
        confirm(intent)
    barrier = Barrier(2)

    def run():
        with app(scope[0]) as session:

            def before(_conn, _cursor, statement, _parameters, _context, _many):
                target = (
                    "SELECT id FROM public.notebooks"
                    if operation == "finalize"
                    else "SELECT n.workspace_id"
                )
                if target in statement:
                    barrier.wait(timeout=10)

            event.listen(session.get_bind(), "before_cursor_execute", before)
            if operation == "finalize":
                assert intent is not None
                result = finalize_upload(session, intent.id)
                return result.document_id, result.version_id, result.job_id
            reserved = reserve(session, scope[5])
            return reserved.document_id, reserved.version_id, reserved.job_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: run(), range(2)))
    assert a == b
    if operation == "finalize":
        with app(scope[0]) as session:
            assert (
                session.execute(
                    text("SELECT count(*) FROM ingestion_jobs WHERE notebook_id=:n"),
                    {"n": scope[5]},
                ).scalar_one()
                == 1
            )


def test_job_not_claimable_until_finalization_commit(scope):
    from app.db.leases import claim_job
    from app.db.uploads import finalize_upload

    with app(scope[0]) as session:
        intent = reserve(session, scope[5])
    confirm(intent)
    with app(scope[0]) as session:
        finalize_upload(session, intent.id)
        with app(scope[0]) as other:
            assert claim_job(other, intent.job_id) is None
    with app(scope[0]) as session:
        assert claim_job(session, intent.job_id) is not None
    with app(scope[1]) as session:
        assert (
            session.execute(
                text("SELECT processing_status FROM document_versions WHERE id=:v"),
                {"v": intent.version_id},
            ).scalar_one()
            == "processing"
        )


def test_chunks_status_finish_are_atomic_and_stale_token_cannot_override(scope):
    from app.db.leases import claim_job, finish_job, lock_job_for_publish

    intent = finalized(scope)
    with app(scope[0]) as session:
        token = claim_job(session, intent.job_id)
        assert token is not None
    with app(scope[0]) as session:
        assert not finish_job(session, intent.job_id, uuid4(), status="succeeded")
        assert lock_job_for_publish(session, intent.job_id, token)
        session.execute(
            text(
                "INSERT INTO chunks(id,document_version_id,document_id,notebook_id,workspace_id,ordinal,text,sha256,start_offset,end_offset) VALUES(:i,:v,:d,:n,:w,0,'hello',:h,0,5)"
            ),
            {
                "i": uuid4(),
                "v": intent.version_id,
                "d": intent.document_id,
                "n": intent.notebook_id,
                "w": intent.workspace_id,
                "h": "b" * 64,
            },
        )
        assert finish_job(session, intent.job_id, token, status="succeeded")
    with app(scope[1]) as session:
        assert session.execute(
            text("SELECT processing_status,error_code FROM document_versions WHERE id=:v"),
            {"v": intent.version_id},
        ).one() == ("ready", None)
        assert (
            session.execute(
                text("SELECT count(*) FROM chunks WHERE document_version_id=:v"),
                {"v": intent.version_id},
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text("SELECT id FROM ingestion_jobs WHERE id=:j"), {"j": intent.job_id}
            ).first()
            is None
        )


@pytest.mark.parametrize("action", ["soft_delete", "hard_delete", "replacement"])
def test_source_changes_queue_only_original_key_and_fence_stale_finish(scope, action):
    from app.db.leases import claim_job, finish_job

    intent = finalized(scope)
    with app(scope[0]) as session:
        token = claim_job(session, intent.job_id)
        assert token is not None
    with app(scope[0]) as session:
        if action == "soft_delete":
            assert session.execute(
                text("SELECT notebook_delete_document(:d)"), {"d": intent.document_id}
            ).scalar_one()
        elif action == "hard_delete":
            session.execute(text("DELETE FROM documents WHERE id=:d"), {"d": intent.document_id})
        else:
            version = uuid4()
            session.execute(
                text("UPDATE documents SET current_version_id=:v WHERE id=:d"),
                {"v": version, "d": intent.document_id},
            )
            session.execute(
                text(
                    "INSERT INTO document_versions(id,document_id,workspace_id,notebook_id,version,media_type,byte_count,sha256,storage_key) VALUES(:v,:d,:w,:n,2,'text/plain',5,:h,:k)"
                ),
                {
                    "v": version,
                    "d": intent.document_id,
                    "w": intent.workspace_id,
                    "n": intent.notebook_id,
                    "h": "c" * 64,
                    "k": f"{intent.workspace_id}/{intent.notebook_id}/{intent.document_id}/{version}/original",
                },
            )
    with app(scope[0]) as session:
        assert not finish_job(session, intent.job_id, token, status="succeeded")
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT state,storage_key FROM upload_intents WHERE id=%s", (intent.id,)
        ).fetchone() == ("cleanup_pending", intent.storage_key)
        db.execute(
            "UPDATE upload_intents SET reconcile_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (intent.id,),
        )
    with io() as db:
        row = db.execute("SELECT * FROM claim_cleanup(%s,60)", (intent.id,)).fetchone()
        assert row is not None and row[2] == intent.storage_key


def test_expired_publish_rolls_back_chunks_and_status(scope):
    from app.db.leases import claim_job, finish_job, lock_job_for_publish

    intent = finalized(scope)
    with app(scope[0]) as session:
        token = claim_job(session, intent.job_id, lease_seconds=1)
        assert token is not None

    class LostLease(Exception):
        pass

    with pytest.raises(LostLease), app(scope[0]) as session:
        assert lock_job_for_publish(session, intent.job_id, token)
        session.execute(
            text(
                "INSERT INTO chunks(id,document_version_id,document_id,notebook_id,workspace_id,ordinal,text,sha256,start_offset,end_offset) VALUES(:i,:v,:d,:n,:w,0,'hello',:h,0,5)"
            ),
            {
                "i": uuid4(),
                "v": intent.version_id,
                "d": intent.document_id,
                "n": intent.notebook_id,
                "w": intent.workspace_id,
                "h": "b" * 64,
            },
        )
        session.execute(text("SELECT pg_sleep(1.1)"))
        if not finish_job(session, intent.job_id, token, status="succeeded"):
            raise LostLease()
    with app(scope[1]) as session:
        assert (
            session.execute(
                text("SELECT processing_status FROM document_versions WHERE id=:v"),
                {"v": intent.version_id},
            ).scalar_one()
            == "processing"
        )
        assert (
            session.execute(
                text("SELECT count(*) FROM chunks WHERE document_version_id=:v"),
                {"v": intent.version_id},
            ).scalar_one()
            == 0
        )


def test_catalog_role_and_grant_boundary(scope):
    with psycopg.connect(URL, user="notebook_app") as db:
        for role in ("notebook_storage_io", "notebook_upload_io_owner", "notebook_upload_finalize"):
            assert db.execute(
                "SELECT rolcanlogin,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=%s", (role,)
            ).fetchone() == (False, False, False)
            assert db.execute(
                "SELECT pg_has_role(current_user,%s,'MEMBER')", (role,)
            ).fetchone() == (False,)
        for signature in (
            "claim_upload_io(uuid,integer)",
            "confirm_upload_io(uuid,uuid,text,integer)",
            "list_cleanup(integer)",
            "claim_cleanup(uuid,integer)",
            "finish_cleanup(uuid,uuid)",
        ):
            assert db.execute(
                "SELECT has_function_privilege(current_user,%s,'EXECUTE')", (signature,)
            ).fetchone() == (False,)
        assert db.execute("SELECT finalize_upload(%s)", (uuid4(),)).fetchone() == (False,)
        rows = db.execute(
            "SELECT p.proname,p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname IN ('finalize_upload','claim_upload_io','confirm_upload_io','list_cleanup','claim_cleanup','finish_cleanup')"
        ).fetchall()
        assert len(rows) == 6 and all(row[1] == ["search_path=pg_catalog"] for row in rows)
    with io() as db:
        for table in (
            "documents",
            "document_versions",
            "ingestion_jobs",
            "workspace_members",
            "app_admissions",
            "user_provider_connections",
            "upload_intents",
        ):
            assert db.execute(
                "SELECT has_table_privilege(current_user,%s,'SELECT')", (table,)
            ).fetchone() == (False,)
        assert db.execute(
            "SELECT has_schema_privilege(current_user,'public','CREATE')"
        ).fetchone() == (False,)
    with psycopg.connect(URL) as db:
        for table in (
            "documents",
            "document_versions",
            "ingestion_jobs",
            "workspace_members",
            "app_admissions",
            "notebooks",
        ):
            assert db.execute(
                "SELECT has_table_privilege('notebook_upload_finalize',%s,'INSERT,UPDATE,DELETE')",
                (table,),
            ).fetchone() == (False,)
        assert db.execute(
            "SELECT has_table_privilege('notebook_upload_io_owner','documents','SELECT')"
        ).fetchone() == (False,)
        assert db.execute(
            "SELECT has_table_privilege('notebook_upload_finalize','user_provider_connections','SELECT')"
        ).fetchone() == (False,)
        assert db.execute(
            "SELECT count(*) FROM pg_proc p,aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE p.proname IN ('finalize_upload','claim_upload_io','confirm_upload_io','list_cleanup','claim_cleanup','finish_cleanup') AND a.grantee=0 AND a.privilege_type='EXECUTE'"
        ).fetchone() == (0,)


@pytest.mark.parametrize("who", [None, 1, 3])
def test_foreign_actor_cannot_finalize_or_inspect_another_intent(scope, who):
    from app.db.uploads import UploadUnavailable, finalize_upload

    with app(scope[0]) as session:
        intent = reserve(session, scope[5])
    confirm(intent)
    with app(scope[who] if who is not None else None) as session:
        assert (
            session.execute(text("SELECT finalize_upload(:i)"), {"i": intent.id}).scalar_one()
            is False
        )
        assert (
            session.execute(
                text("SELECT id FROM upload_intents WHERE id=:i"), {"i": intent.id}
            ).first()
            is None
        )
        with pytest.raises(UploadUnavailable):
            finalize_upload(session, intent.id)


def test_timeout_crash_before_write_retains_key_and_cannot_be_reused(scope):
    with app(scope[0]) as session:
        intent = reserve(session, scope[5])
    with psycopg.connect(URL) as db:
        db.execute(
            "UPDATE upload_intents SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (intent.id,),
        )
    with io() as db:
        db.execute("SELECT * FROM list_cleanup(100)")
        assert db.execute("SELECT * FROM claim_upload_io(%s,60)", (intent.id,)).fetchone() is None
    with app(scope[0]) as session:
        replay = reserve(session, scope[5])
        assert replay.id == intent.id and replay.state == "cleanup_pending"
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT storage_key FROM upload_intents WHERE id=%s", (intent.id,)
        ).fetchone() == (intent.storage_key,)


def test_processing_error_is_sanitized_not_private_job_error(scope):
    from app.db.leases import claim_job, finish_job

    intent = finalized(scope)
    with app(scope[0]) as session:
        token = claim_job(session, intent.job_id)
        assert token is not None
    with app(scope[0]) as session:
        assert finish_job(
            session, intent.job_id, token, status="failed", error_code="private_provider_failure"
        )
    with app(scope[1]) as session:
        assert session.execute(
            text("SELECT processing_status,error_code FROM document_versions WHERE id=:v"),
            {"v": intent.version_id},
        ).one() == ("failed", "processing_failed")


def test_fresh_admission_check_and_python_storage_helpers(scope):
    from app.db.uploads import check_upload_admission, claim_upload_io, confirm_upload_io
    from sqlalchemy.engine import make_url

    with app(scope[0]) as session:
        intent = reserve(session, scope[5])
    # Dedicated test-only login proves capability without superuser SET ROLE.
    with psycopg.connect(URL, autocommit=True) as db:
        db.execute("CREATE ROLE a5_storage_fixture LOGIN INHERIT NOSUPERUSER NOBYPASSRLS")
        db.execute("GRANT notebook_storage_io TO a5_storage_fixture")
    engine = create_engine(
        make_url(URL).set(drivername="postgresql+psycopg", username="a5_storage_fixture")
    )
    try:
        with Session(engine) as session, session.begin():
            claim = claim_upload_io(session, intent.id)
            assert claim is not None
            assert claim.storage_key == intent.storage_key
        with app(scope[0]) as session:
            assert check_upload_admission(session, intent.id, claim.token)
        with app(scope[1]) as session:
            assert not check_upload_admission(session, intent.id, claim.token)
        with psycopg.connect(URL) as db:
            db.execute("DELETE FROM app_admissions WHERE user_id=%s", (scope[0],))
        with app(scope[0]) as session:
            assert not check_upload_admission(session, intent.id, claim.token)
        with Session(engine) as session, session.begin():
            assert not confirm_upload_io(
                session, intent.id, claim.token, sha256="a" * 64, byte_count=5
            )
    finally:
        engine.dispose()
        with psycopg.connect(URL, autocommit=True) as db:
            db.execute("DROP ROLE a5_storage_fixture")


@pytest.mark.parametrize("limit", [None, 0, 101])
def test_cleanup_listing_is_strictly_bounded(limit):
    with pytest.raises(psycopg.Error, match="invalid_limit"), io() as db:
        db.execute("SELECT * FROM list_cleanup(%s)", (limit,))


def test_crash_during_finalize_rolls_back_all_rows_and_receipt_can_be_retried(scope):
    from app.db.uploads import finalize_upload

    with app(scope[0]) as session:
        intent = reserve(session, scope[5])
    confirm(intent)
    with pytest.raises(RuntimeError, match="crash"), app(scope[0]) as session:
        finalize_upload(session, intent.id)
        raise RuntimeError("crash")
    with app(scope[0]) as session:
        assert (
            session.execute(
                text("SELECT state FROM upload_intents WHERE id=:i"), {"i": intent.id}
            ).scalar_one()
            == "stored"
        )
        assert (
            session.execute(
                text("SELECT id FROM documents WHERE id=:d"), {"d": intent.document_id}
            ).first()
            is None
        )
        assert finalize_upload(session, intent.id).job_id == intent.job_id


def test_cleanup_reclaim_fences_old_token_and_preserves_tombstone(scope):
    from app.db.uploads import claim_cleanup, finish_cleanup, list_cleanup
    from sqlalchemy.engine import make_url

    intent = finalized(scope)
    with app(scope[0]) as session:
        session.execute(text("DELETE FROM documents WHERE id=:d"), {"d": intent.document_id})
    with psycopg.connect(URL) as db:
        db.execute(
            "UPDATE upload_intents SET reconcile_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (intent.id,),
        )
    with io() as db:
        first = db.execute("SELECT * FROM claim_cleanup(%s,60)", (intent.id,)).fetchone()
        assert first is not None
    with psycopg.connect(URL) as db:
        db.execute(
            "UPDATE upload_intents SET cleanup_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (intent.id,),
        )
    # Fixed-role runtime session; no application-table grants.
    engine = create_engine(make_url(URL).set(drivername="postgresql+psycopg"))
    try:
        with Session(engine) as session, session.begin():
            session.execute(text("SET ROLE notebook_storage_io"))
            assert intent.id in list_cleanup(session, limit=100)
            second = claim_cleanup(session, intent.id)
            assert second is not None and second.token != first[1]
            assert not finish_cleanup(session, intent.id, first[1])
            assert finish_cleanup(session, intent.id, second.token)
    finally:
        engine.dispose()
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT state,storage_key FROM upload_intents WHERE id=%s", (intent.id,)
        ).fetchone() == ("cleaned", intent.storage_key)


def test_timeout_reconciler_cannot_turn_committing_finalized_source_into_cleanup(scope):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.db.uploads import finalize_upload

    with app(scope[0]) as session:
        intent = reserve(session, scope[5])
    confirm(intent)
    with psycopg.connect(URL) as db:
        db.execute(
            "UPDATE upload_intents SET expires_at=clock_timestamp()+interval '1 second' WHERE id=%s",
            (intent.id,),
        )
    started = Event()

    def reconcile():
        with io() as db:
            started.set()
            return db.execute("SELECT * FROM list_cleanup(100)").fetchall()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with app(scope[0]) as session:
            finalize_upload(session, intent.id)
            session.execute(text("SELECT pg_sleep(1.1)"))
            future = pool.submit(reconcile)
            assert started.wait(3)
            # Expiry discovery uses SKIP LOCKED: the locked precommit stored
            # row is skipped, NOT converted to cleanup or remotely deleted.
            assert (intent.id,) not in future.result(timeout=3)
        assert (intent.id,) not in future.result(timeout=5)
    with io() as db:
        assert (intent.id,) not in db.execute("SELECT * FROM list_cleanup(100)").fetchall()
        assert db.execute("SELECT * FROM claim_cleanup(%s,60)", (intent.id,)).fetchone() is None
    with app(scope[0]) as session:
        assert (
            session.execute(
                text("SELECT state FROM upload_intents WHERE id=:i"), {"i": intent.id}
            ).scalar_one()
            == "finalized"
        )


def test_skipped_expired_intent_is_reconciled_after_lock_release(scope):
    with app(scope[0]) as session:
        intent = reserve(session, scope[5])
    with psycopg.connect(URL) as db:
        db.execute(
            "UPDATE upload_intents SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (intent.id,),
        )
    with psycopg.connect(URL) as locker:
        locker.execute("SELECT id FROM upload_intents WHERE id=%s FOR UPDATE", (intent.id,))
        with io() as db:
            assert (intent.id,) not in db.execute("SELECT * FROM list_cleanup(100)").fetchall()
    with io() as db:
        db.execute("SELECT * FROM list_cleanup(100)")
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT state FROM upload_intents WHERE id=%s", (intent.id,)
        ).fetchone() == ("cleanup_pending",)
        db.execute(
            "UPDATE upload_intents SET reconcile_at=clock_timestamp()-interval '1 second' WHERE id=%s",
            (intent.id,),
        )
    with io() as db:
        assert (intent.id,) in db.execute("SELECT * FROM list_cleanup(100)").fetchall()
        claim = db.execute("SELECT * FROM claim_cleanup(%s,60)", (intent.id,)).fetchone()
        assert claim is not None and claim[2] == intent.storage_key


def test_finalized_version_content_cannot_be_recycled_under_same_bound_id(scope):
    from sqlalchemy.exc import IntegrityError

    intent = finalized(scope)
    with pytest.raises(IntegrityError, match="upload_identity_mismatch"), app(scope[0]) as session:
        session.execute(text("DELETE FROM document_versions WHERE id=:v"), {"v": intent.version_id})
        session.execute(
            text(
                "INSERT INTO document_versions(id,document_id,notebook_id,workspace_id,version,media_type,byte_count,sha256,storage_key) VALUES(:v,:d,:n,:w,1,'text/plain',5,:h,:k)"
            ),
            {
                "v": intent.version_id,
                "d": intent.document_id,
                "n": intent.notebook_id,
                "w": intent.workspace_id,
                "h": "b" * 64,
                "k": intent.storage_key,
            },
        )
