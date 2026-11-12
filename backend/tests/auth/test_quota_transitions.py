"""Direct SQL and lifecycle regressions against quota head (no guard bypass)."""

from time import sleep

import psycopg
import pytest
from app.db.leases import claim_job, finish_job, heartbeat_job
from app.db.quota import classify_quota_error, quota_gate
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_quota import finalized
from test_uploads import URL, app, reserve

pytest_plugins = ["test_uploads"]
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL URL required")


@pytest.mark.parametrize(
    "change",
    [
        "attempts=attempts+1",
        "lease_token=gen_random_uuid()",
        "lease_expires_at=clock_timestamp()+interval '1 second'",
        "lease_expires_at=clock_timestamp()+interval '301 seconds'",
        "available_at=clock_timestamp()+interval '1 day'",
        "status='queued',lease_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL",
    ],
)
def test_direct_running_rewrites_refused_without_mirror_or_projection_change(scope, change):
    actor, _, _, _, _, notebook = scope
    intent = finalized(actor, notebook, "rewrite")
    with app(actor) as db:
        assert claim_job(db, intent.job_id)
    with psycopg.connect(URL) as db:
        before = db.execute(
            "SELECT to_jsonb(j),to_jsonb(i),to_jsonb(v) FROM ingestion_jobs j "
            "JOIN upload_intents i ON i.job_id=j.id "
            "JOIN document_versions v ON v.id=i.version_id WHERE j.id=%s",
            (intent.job_id,),
        ).fetchone()
    with pytest.raises(DBAPIError) as caught, app(actor) as db:
        statements = {
            "attempts=attempts+1": text(
                "UPDATE ingestion_jobs SET attempts=attempts+1 WHERE id=:j"
            ),
            "lease_token=gen_random_uuid()": text(
                "UPDATE ingestion_jobs SET lease_token=gen_random_uuid() WHERE id=:j"
            ),
            "lease_expires_at=clock_timestamp()+interval '1 second'": text(
                "UPDATE ingestion_jobs SET lease_expires_at=clock_timestamp()+interval '1 second' WHERE id=:j"
            ),
            "lease_expires_at=clock_timestamp()+interval '301 seconds'": text(
                "UPDATE ingestion_jobs SET lease_expires_at=clock_timestamp()+interval '301 seconds' WHERE id=:j"
            ),
            "available_at=clock_timestamp()+interval '1 day'": text(
                "UPDATE ingestion_jobs SET available_at=clock_timestamp()+interval '1 day' WHERE id=:j"
            ),
            "status='queued',lease_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL": text(
                "UPDATE ingestion_jobs SET status='queued',lease_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL WHERE id=:j"
            ),
        }
        db.execute(statements[change], {"j": intent.job_id})
    assert classify_quota_error(caught.value) == "quota_legacy_unsupported"
    with psycopg.connect(URL) as db:
        assert (
            db.execute(
                "SELECT to_jsonb(j),to_jsonb(i),to_jsonb(v) FROM ingestion_jobs j "
                "JOIN upload_intents i ON i.job_id=j.id "
                "JOIN document_versions v ON v.id=i.version_id WHERE j.id=%s",
                (intent.job_id,),
            ).fetchone()
            == before
        )


def test_real_expiry_reclaim_fences_old_heartbeat_and_publication(scope):
    actor, _, _, _, _, notebook = scope
    intent = finalized(actor, notebook, "expiry")
    with app(actor) as db:
        first = claim_job(db, intent.job_id, lease_seconds=1)
        assert first
    sleep(1.05)  # Outside transaction; do not privileged-edit the protected clock.
    with app(actor) as db:
        assert not heartbeat_job(db, intent.job_id, first)
        second = claim_job(db, intent.job_id)
        assert second and second != first
    with app(actor) as db:
        assert not heartbeat_job(db, intent.job_id, first)
        assert not finish_job(db, intent.job_id, first, status="succeeded")
    with app(actor) as db:
        assert finish_job(db, intent.job_id, second, status="succeeded")
    with app(actor) as db:
        assert db.execute(
            text("SELECT status,attempts FROM ingestion_jobs WHERE id=:j"), {"j": intent.job_id}
        ).one() == ("succeeded", 2)
    with pytest.raises(DBAPIError) as caught, app(actor) as db:
        db.execute(
            text("UPDATE ingestion_jobs SET status='queued' WHERE id=:j"), {"j": intent.job_id}
        )
    assert classify_quota_error(caught.value) == "quota_legacy_unsupported"


@pytest.mark.parametrize("event", ["admission", "demotion", "soft_delete", "job_delete"])
def test_cancellation_preserves_live_occupancy_and_never_fabricates_cleanup(scope, event):
    actor, editor, _, _, workspace, notebook = scope
    intents = [finalized(actor, notebook, str(n)) for n in range(3)]
    with app(actor) as db:
        token = claim_job(db, intents[0].job_id)
        assert token
    with psycopg.connect(URL) as db:
        if event == "admission":
            db.execute("DELETE FROM app_admissions WHERE user_id=%s", (actor,))
        elif event == "demotion":
            db.execute(
                "UPDATE workspace_members SET role='viewer' WHERE workspace_id=%s AND user_id=%s",
                (workspace, actor),
            )
        elif event == "soft_delete":
            db.execute(
                "UPDATE documents SET deleted_at=clock_timestamp() WHERE id=%s",
                (intents[0].document_id,),
            )
        else:
            db.execute("DELETE FROM ingestion_jobs WHERE id=%s", (intents[0].job_id,))
    with psycopg.connect(URL) as db:
        state = db.execute(
            "SELECT state,work_cancelled,job_state,job_lease_token,job_lease_expires_at>clock_timestamp() "
            "FROM upload_intents WHERE id=%s",
            (intents[0].id,),
        ).fetchone()
        assert state is not None
        assert state[1:] == (True, "running", token, True)
        assert state[0] == ("cleanup_pending" if event == "soft_delete" else "finalized")
        if event == "admission":
            db.execute("INSERT INTO app_admissions VALUES (%s)", (actor,))
        elif event == "demotion":
            db.execute(
                "UPDATE workspace_members SET role='editor' WHERE workspace_id=%s AND user_id=%s",
                (workspace, actor),
            )
    if event in ("admission", "demotion"):
        with pytest.raises(DBAPIError) as caught, app(actor) as db:
            claim_job(db, intents[1].job_id)
        assert classify_quota_error(caught.value) == "quota_legacy_unsupported"
    # Cancellation/deletion MUST NOT free a still-live logical execution lease.
    replacements = [finalized(actor, notebook, f"replacement-{n}") for n in range(2)]
    with app(actor) as db:
        assert claim_job(db, replacements[0].job_id)
    with pytest.raises(DBAPIError) as caught, app(actor) as db:
        claim_job(db, replacements[1].job_id)
    assert classify_quota_error(caught.value) == "quota_capacity"
    # Another member still reads the finalized original on uploader revocation.
    if event in ("admission", "demotion"):
        with app(editor) as db:
            assert (
                db.execute(
                    text("SELECT id FROM documents WHERE id=:d"), {"d": intents[0].document_id}
                ).scalar_one()
                == intents[0].document_id
            )


def test_direct_dml_busy_fallback_rolls_back_unrelated_transaction_write(scope):
    actor, _, _, _, _, notebook = scope
    with app(actor) as holder:
        quota_gate(holder)
        with pytest.raises(DBAPIError) as caught, app(actor) as contender:
            # No helper gate: BEFORE trigger itself must reject the mutation.
            contender.execute(
                text("UPDATE notebooks SET title='must rollback' WHERE id=:n"), {"n": notebook}
            )
        assert classify_quota_error(caught.value) == "quota_gate_busy"
        # Contender's BEFORE-trigger failure released its target-row lock:
        # the gate holder can take that same row without inversion/hang.
        holder.execute(text("SET LOCAL lock_timeout='500ms'"))
        holder.execute(text("UPDATE notebooks SET title=title WHERE id=:n"), {"n": notebook})
    with app(actor) as db:
        assert (
            db.execute(
                text("SELECT title FROM notebooks WHERE id=:n"), {"n": notebook}
            ).scalar_one()
            == "A5"
        )
        assert reserve(db, notebook, idempotency_key="retry-after-busy").id


@pytest.mark.parametrize(
    "code,message",
    [("P0001", "other_error"), ("42501", "quota_capacity"), ("55P03", "other_error")],
)
def test_classifier_does_not_map_nearby_server_errors(scope, code, message):
    with pytest.raises(psycopg.Error) as caught, psycopg.connect(URL) as db:
        db.execute(
            "SELECT set_config('test.quota_message',%s,true),set_config('test.quota_code',%s,true)",
            (message, code),
        )
        db.execute(
            "DO $$ BEGIN RAISE EXCEPTION USING MESSAGE=current_setting('test.quota_message'), ERRCODE=current_setting('test.quota_code'); END $$"
        )
    assert classify_quota_error(caught.value) is None


def test_already_started_direct_claim_sees_committed_active_capacity(scope):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    actor, _, _, _, _, notebook = scope
    intents = [finalized(actor, notebook, str(n)) for n in range(3)]
    with app(actor) as db:
        assert claim_job(db, intents[0].job_id)
    started = Event()
    pids = []

    def delayed_claim():
        try:
            with psycopg.connect(URL, user="notebook_app") as db:
                db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
                pids.append(db.info.backend_pid)
                started.set()
                db.execute(
                    "WITH delayed AS MATERIALIZED (SELECT pg_sleep(1)) "
                    "UPDATE ingestion_jobs SET status='running',attempts=attempts+1,"
                    "lease_token=gen_random_uuid(),heartbeat_at=clock_timestamp(),"
                    "lease_expires_at=clock_timestamp()+interval '60 seconds' "
                    "FROM delayed WHERE id=%s",
                    (intents[2].job_id,),
                )
        except psycopg.Error as error:
            return classify_quota_error(error)
        return "incorrectly_admitted"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with app(actor) as holder:
            assert claim_job(holder, intents[1].job_id)
            future = pool.submit(delayed_claim)
            assert started.wait(3)
            with psycopg.connect(URL, autocommit=True) as observer:
                for _ in range(100):
                    if observer.execute(
                        "SELECT wait_event FROM pg_stat_activity WHERE pid=%s", (pids[0],)
                    ).fetchone() == ("PgSleep",):
                        break
                    sleep(0.005)
                else:
                    pytest.fail("direct claim never reached pre-gate sleep")
        assert future.result(timeout=5) == "quota_capacity"
    with app(actor) as db:
        assert db.execute(
            text("SELECT status,attempts FROM ingestion_jobs WHERE id=:j"), {"j": intents[2].job_id}
        ).one() == ("queued", 0)


@pytest.mark.parametrize(
    "column",
    ["job_state", "job_attempts", "job_lease_token", "job_lease_expires_at", "work_cancelled"],
)
def test_app_cannot_read_or_write_protected_quota_mirror(scope, column):
    from psycopg import sql

    actor, _, _, _, _, notebook = scope
    with app(actor) as db:
        intent = reserve(db, notebook)
    for statement in (
        sql.SQL("SELECT {} FROM public.upload_intents WHERE id=%s").format(sql.Identifier(column)),
        sql.SQL("UPDATE public.upload_intents SET {}=NULL WHERE id=%s").format(
            sql.Identifier(column)
        ),
    ):
        with (
            pytest.raises(psycopg.errors.InsufficientPrivilege),
            psycopg.connect(URL, user="notebook_app") as db,
        ):
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
            db.execute(statement, (intent.id,))
