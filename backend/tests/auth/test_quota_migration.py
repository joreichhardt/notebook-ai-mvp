"""0005 ->0006 refusal is transactional, with no fabricated legacy work."""

import os
from uuid import uuid4

import psycopg
import pytest
from database_versions import isolated_database, migrate
from test_documents import insert_document, insert_job

URL = os.getenv("NOTEBOOK_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL URL required")


def seed_scope(url):
    actor, workspace, notebook = uuid4(), uuid4(), uuid4()
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO app_admissions VALUES (%s)", (actor,))
        db.execute("INSERT INTO workspaces(id,name) VALUES (%s,'quota migration')", (workspace,))
        db.execute("INSERT INTO workspace_members VALUES (%s,%s,'owner')", (workspace, actor))
        db.execute("INSERT INTO notebooks VALUES (%s,%s,'quota migration')", (notebook, workspace))
    return actor, workspace, notebook


def snapshot(url):
    with psycopg.connect(url) as db:
        # Entire fixture tables and revision, not just a row count.
        return tuple(
            db.execute(query).fetchall()
            for query in (
                "SELECT * FROM alembic_version",
                "SELECT to_jsonb(i) FROM upload_intents i ORDER BY id",
                "SELECT to_jsonb(j) FROM ingestion_jobs j ORDER BY id",
                "SELECT to_jsonb(d) FROM documents d ORDER BY id",
                "SELECT to_jsonb(v) FROM document_versions v ORDER BY id",
                "SELECT column_name FROM information_schema.columns WHERE table_name='upload_intents' ORDER BY ordinal_position",
            )
        )


@pytest.mark.parametrize("status", ["queued", "running"])
def test_unbound_nonterminal_including_expired_running_blocks_without_writes(status):
    with isolated_database(URL, "0005") as url:
        actor, workspace, notebook = seed_scope(url)
        with psycopg.connect(url) as db:
            _, version = insert_document(db, workspace, notebook, actor)
            job = insert_job(db, workspace, notebook, version, actor, status="queued")
            if status == "running":
                db.execute(
                    """
                    UPDATE ingestion_jobs SET status='running',attempts=1,lease_token=gen_random_uuid(),
                    lease_expires_at=clock_timestamp()-interval '1 second',heartbeat_at=clock_timestamp()
                    WHERE id=%s
                """,
                    (job,),
                )
        before = snapshot(url)
        result = migrate(url, "head")
        assert result.returncode != 0
        assert "quota_preflight_required" in result.stderr
        assert snapshot(url) == before
        # No automatic retirement: only explicitly approved exact-ID fixture
        # acknowledgement, outside the refused migration transaction.
        with psycopg.connect(url) as db:
            db.execute(
                """
                UPDATE ingestion_jobs SET status='cancelled',lease_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL
                WHERE id=%s AND status=%s
            """,
                (job, status),
            )
        assert migrate(url, "head").returncode == 0
        with psycopg.connect(url, user="notebook_app") as db:
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
            assert db.execute(
                "SELECT status FROM ingestion_jobs WHERE id=%s", (job,)
            ).fetchone() == ("cancelled",)
        with (
            pytest.raises(psycopg.errors.RaiseException, match="quota_legacy_unsupported"),
            psycopg.connect(url, user="notebook_app") as db,
        ):
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
            db.execute("UPDATE ingestion_jobs SET status='queued' WHERE id=%s", (job,))


def test_overcapacity_reserved_preflight_is_atomic():
    with isolated_database(URL, "0005") as url:
        actor, workspace, notebook = seed_scope(url)
        with psycopg.connect(url, user="notebook_app") as db:
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
            for n in range(11):
                db.execute(
                    """
                    INSERT INTO upload_intents(workspace_id,notebook_id,idempotency_key,filename,sha256,byte_count,media_type)
                    VALUES (%s,%s,%s,'x.txt',%s,5,'text/plain')
                """,
                    (workspace, notebook, str(n), "a" * 64),
                )
        before = snapshot(url)
        result = migrate(url, "head")
        assert result.returncode != 0 and "quota_preflight_required" in result.stderr
        assert snapshot(url) == before


def canonical_0005(url, actor, workspace, notebook, key="canonical"):
    from test_documents import insert_version

    with psycopg.connect(url, user="notebook_app") as db:
        db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
        row = db.execute(
            """
            INSERT INTO upload_intents(workspace_id,notebook_id,idempotency_key,filename,sha256,byte_count,media_type)
            VALUES (%s,%s,%s,'sample.txt',%s,5,'text/plain') RETURNING id,document_id,version_id,job_id
        """,
            (workspace, notebook, key, "a" * 64),
        ).fetchone()
        assert row is not None
    intent, document, version, job = row
    with psycopg.connect(url) as db:
        db.execute("SET ROLE notebook_storage_io")
        claim = db.execute("SELECT * FROM claim_upload_io(%s,300)", (intent,)).fetchone()
        assert claim is not None
    with psycopg.connect(url) as db:
        db.execute("SET ROLE notebook_storage_io")
        assert db.execute(
            "SELECT confirm_upload_io(%s,%s,%s,5)", (intent, claim[1], "a" * 64)
        ).fetchone() == (True,)
    with psycopg.connect(url, user="notebook_app") as db:
        db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
        db.execute(
            """
            INSERT INTO documents(id,workspace_id,notebook_id,created_by,filename,current_version_id)
            VALUES (%s,%s,%s,%s,'sample.txt',%s)
        """,
            (document, workspace, notebook, actor, version),
        )
        insert_version(db, workspace, notebook, document, version)
        db.execute(
            """
            INSERT INTO ingestion_jobs(id,document_version_id,notebook_id,workspace_id,actor_user_id,idempotency_key)
            VALUES (%s,%s,%s,%s,%s,%s)
        """,
            (job, version, notebook, workspace, actor, key),
        )
        assert db.execute("SELECT finalize_upload(%s)", (intent,)).fetchone() == (True,)
    return intent, document, version, job


@pytest.mark.parametrize(
    "mode", ["queued", "running", "cleanup_live", "mismatch", "active_overcapacity"]
)
def test_canonical_seed_exact_and_mismatch_or_active_overcapacity_refuses(mode):
    with isolated_database(URL, "0005") as url:
        actor, workspace, notebook = seed_scope(url)
        intent, document, _, job = canonical_0005(url, actor, workspace, notebook)
        if mode in ("running", "cleanup_live", "active_overcapacity"):
            with psycopg.connect(url, user="notebook_app") as db:
                db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
                db.execute(
                    """
                    UPDATE ingestion_jobs SET status='running',attempts=1,lease_token=gen_random_uuid(),
                    lease_expires_at=clock_timestamp()+interval '200 seconds',heartbeat_at=clock_timestamp()
                    WHERE id=%s
                """,
                    (job,),
                )
        if mode == "cleanup_live":
            with psycopg.connect(url, user="notebook_app") as db:
                db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
                assert db.execute(
                    "SELECT notebook_delete_document(%s)", (document,)
                ).fetchone() == (True,)
        if mode == "mismatch":
            with psycopg.connect(url) as db:
                db.execute("UPDATE upload_intents SET sha256=%s WHERE id=%s", ("b" * 64, intent))
        if mode == "active_overcapacity":
            for n in range(2):
                _, _, _, extra = canonical_0005(url, actor, workspace, notebook, str(n))
                with psycopg.connect(url, user="notebook_app") as db:
                    db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
                    db.execute(
                        """
                        UPDATE ingestion_jobs SET status='running',attempts=1,lease_token=gen_random_uuid(),
                        lease_expires_at=clock_timestamp()+interval '200 seconds',heartbeat_at=clock_timestamp()
                        WHERE id=%s
                    """,
                        (extra,),
                    )
        before = snapshot(url)
        result = migrate(url, "head")
        if mode in ("mismatch", "active_overcapacity"):
            assert result.returncode != 0 and "quota_preflight_required" in result.stderr
            assert snapshot(url) == before
        else:
            assert result.returncode == 0
            with psycopg.connect(url) as db:
                assert db.execute(
                    """
                    SELECT i.job_state=j.status AND i.job_attempts=j.attempts
                     AND i.job_lease_token IS NOT DISTINCT FROM j.lease_token
                     AND i.job_lease_expires_at IS NOT DISTINCT FROM j.lease_expires_at
                    FROM upload_intents i JOIN ingestion_jobs j ON j.id=i.job_id WHERE i.id=%s
                """,
                    (intent,),
                ).fetchone() == (True,)
                assert (
                    db.execute("SELECT to_jsonb(j) FROM ingestion_jobs j ORDER BY id").fetchall()
                    == before[2]
                )


def test_terminal_history_preserved_byte_for_byte_and_new_queue_refused():
    with isolated_database(URL, "0005") as url:
        actor, workspace, notebook = seed_scope(url)
        with psycopg.connect(url) as db:
            _, version = insert_document(db, workspace, notebook, actor)
            job = insert_job(db, workspace, notebook, version, actor, status="succeeded")
            before = db.execute(
                "SELECT to_jsonb(j) FROM ingestion_jobs j WHERE id=%s", (job,)
            ).fetchone()
        assert migrate(url, "head").returncode == 0
        with psycopg.connect(url) as db:
            assert (
                db.execute(
                    "SELECT to_jsonb(j) FROM ingestion_jobs j WHERE id=%s", (job,)
                ).fetchone()
                == before
            )
            assert db.execute("SELECT count(*) FROM upload_intents").fetchone() == (0,)
        with (
            pytest.raises(psycopg.errors.RaiseException, match="quota_legacy_unsupported"),
            psycopg.connect(url, user="notebook_app") as db,
        ):
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
            insert_job(db, workspace, notebook, version, actor, status="queued")
