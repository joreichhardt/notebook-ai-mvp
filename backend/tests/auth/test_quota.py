"""Quota head: direct runtime SQL, not privileged quota assertions."""

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_uploads import URL, app, confirm, reserve

pytest_plugins = ["test_uploads"]
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL URL not configured")


def test_actor_notebook_capacity_rolls_back_statement(scope):
    actor, _, _, _, _, notebook = scope
    for n in range(10):
        with app(actor) as db:
            reserve(db, notebook, idempotency_key=f"quota-{n}")
    with pytest.raises(DBAPIError) as caught, app(actor) as db:
        reserve(db, notebook, idempotency_key="eleventh")
    assert isinstance(caught.value.orig, psycopg.Error)
    assert caught.value.orig.sqlstate == "P0001"
    assert caught.value.orig.diag.message_primary == "quota_capacity"
    with app(actor) as db:
        assert (
            db.execute(
                text("SELECT count(*) FROM upload_intents WHERE notebook_id=:n"), {"n": notebook}
            ).scalar_one()
            == 10
        )
        assert reserve(db, notebook, idempotency_key="quota-0").id


def test_classifier_matches_only_exact_driver_errors():
    from app.db.quota import classify_quota_error

    assert classify_quota_error(ValueError("quota_capacity")) is None
    assert classify_quota_error(psycopg.errors.RaiseException("quota_capacity")) is None


def finalized(actor, notebook, key):
    from app.db.uploads import finalize_upload

    with app(actor) as db:
        intent = reserve(db, notebook, idempotency_key=key)
    confirm(intent)
    with app(actor) as db:
        finalize_upload(db, intent.id)
    return intent


def test_active_capacity_direct_sql_rolls_back_job_projection_and_attempt(scope):
    from app.db.leases import claim_job
    from app.db.quota import classify_quota_error

    actor, _, _, _, _, notebook = scope
    intents = [finalized(actor, notebook, str(n)) for n in range(3)]
    for intent in intents[:2]:
        with app(actor) as db:
            assert claim_job(db, intent.job_id)
    with pytest.raises(DBAPIError) as caught, app(actor) as db:
        db.execute(
            text("""
            UPDATE ingestion_jobs SET status='running',attempts=attempts+1,
            lease_token=gen_random_uuid(),heartbeat_at=clock_timestamp(),
            lease_expires_at=clock_timestamp()+interval '60 seconds' WHERE id=:j
        """),
            {"j": intents[2].job_id},
        )
    assert classify_quota_error(caught.value) == "quota_capacity"
    with app(actor) as db:
        assert db.execute(
            text("SELECT status,attempts,lease_token FROM ingestion_jobs WHERE id=:j"),
            {"j": intents[2].job_id},
        ).one() == ("queued", 0, None)
        assert (
            db.execute(
                text("SELECT processing_status FROM document_versions WHERE id=:v"),
                {"v": intents[2].version_id},
            ).scalar_one()
            == "queued"
        )


@pytest.mark.parametrize("who", [None, 2, 3])
def test_foreign_or_unadmitted_actor_has_no_capacity_oracle(scope, who):
    actor, _, _, _, workspace, notebook = scope
    for n in range(10):
        with app(actor) as db:
            reserve(db, notebook, idempotency_key=str(n))
    with pytest.raises(DBAPIError) as caught, app(scope[who] if who is not None else None) as db:
        db.execute(
            text("""
            INSERT INTO upload_intents(workspace_id,notebook_id,idempotency_key,filename,sha256,byte_count,media_type)
            VALUES (:w,:n,'foreign','x.txt',:h,5,'text/plain')
        """),
            {"w": workspace, "n": notebook, "h": "a" * 64},
        )
    assert isinstance(caught.value.orig, psycopg.Error)
    assert caught.value.orig.sqlstate == "42501"
    assert "quota_capacity" not in str(caught.value.orig)


def test_busy_aborts_whole_transaction_and_preserves_attempt(scope):
    from app.db.leases import claim_job
    from app.db.quota import classify_quota_error, quota_gate

    actor, _, _, _, _, notebook = scope
    intent = finalized(actor, notebook, "busy")
    with app(actor) as holder:
        quota_gate(holder)
        with pytest.raises(DBAPIError) as caught, app(actor) as contender:
            claim_job(contender, intent.job_id)
        assert classify_quota_error(caught.value) == "quota_gate_busy"
    with app(actor) as db:
        assert (
            db.execute(
                text("SELECT attempts FROM ingestion_jobs WHERE id=:j"), {"j": intent.job_id}
            ).scalar_one()
            == 0
        )
        assert claim_job(db, intent.job_id)


def test_gate_and_mirror_privileges_are_exact(scope):
    actor = scope[0]
    with psycopg.connect(URL) as db:
        roles = db.execute("""
            SELECT r.rolname FROM pg_roles r
            WHERE NOT r.rolsuper AND has_function_privilege(r.oid,'public.quota_gate()','EXECUTE')
            ORDER BY r.rolname
        """).fetchall()
        assert roles == [
            (r,)
            for r in sorted(
                [
                    "notebook_app",
                    "notebook_membership",
                    "notebook_document_lifecycle",
                    "notebook_upload_finalize",
                    "notebook_upload_io_owner",
                ]
            )
        ]
        assert db.execute("""
            SELECT has_table_privilege('notebook_upload_io_owner','public.ingestion_jobs','SELECT'),
             has_table_privilege('notebook_upload_io_owner','public.documents','UPDATE'),
             has_function_privilege('notebook_app','public.quota_before_gate()','EXECUTE'),
             has_function_privilege('notebook_storage_io','public.quota_gate()','EXECUTE')
        """).fetchone() == (False, False, False, False)
    for column in (
        "job_state",
        "job_attempts",
        "job_lease_token",
        "job_lease_expires_at",
        "work_cancelled",
    ):
        with app(actor) as db:
            assert not db.execute(
                text(
                    "SELECT has_column_privilege(current_user,'public.upload_intents',:c,'SELECT')"
                ),
                {"c": column},
            ).scalar_one()


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_unsupported_isolation_fails_closed(scope, isolation):
    from app.db.quota import classify_quota_error

    with pytest.raises(psycopg.Error) as caught, psycopg.connect(URL, user="notebook_app") as db:
        if isolation == "REPEATABLE READ":
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        else:
            db.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        db.execute("SELECT public.quota_gate()")
    assert classify_quota_error(caught.value) == "quota_isolation_unsupported"
