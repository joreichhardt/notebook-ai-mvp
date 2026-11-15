"""Quota head: direct runtime SQL, not privileged quota assertions."""

from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_uploads import URL, app, confirm, reserve

pytest_plugins = ["test_uploads"]
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL URL not configured")


@pytest.fixture
def many_scopes():
    actors = [uuid4() for _ in range(6)]
    workspaces, notebooks = [uuid4(), uuid4()], [uuid4(), uuid4()]
    with psycopg.connect(URL) as db:
        for actor in actors:
            db.execute("INSERT INTO app_admissions VALUES (%s)", (actor,))
        for workspace, notebook in zip(workspaces, notebooks, strict=True):
            db.execute("INSERT INTO workspaces(id,name) VALUES (%s,'quota multi')", (workspace,))
            db.execute("INSERT INTO notebooks VALUES (%s,%s,'quota multi')", (notebook, workspace))
            for actor in actors:
                db.execute(
                    "INSERT INTO workspace_members VALUES (%s,%s,'editor')", (workspace, actor)
                )
    try:
        yield actors, notebooks
    finally:
        with psycopg.connect(URL) as db:
            db.execute("DELETE FROM workspaces WHERE id=ANY(%s)", (workspaces,))
            db.execute("DELETE FROM app_admissions WHERE user_id=ANY(%s)", (actors,))
            db.execute("DELETE FROM upload_intents WHERE workspace_id=ANY(%s)", (workspaces,))


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


def test_notebook_outstanding_fifty_and_replay(many_scopes):
    from app.db.quota import classify_quota_error

    actors, notebooks = many_scopes
    for n in range(50):
        with app(actors[n // 10]) as db:
            reserve(db, notebooks[0], idempotency_key=str(n))
    with app(actors[0]) as db:
        assert reserve(db, notebooks[0], idempotency_key="0").id
    with pytest.raises(DBAPIError) as caught, app(actors[5]) as db:
        reserve(db, notebooks[0], idempotency_key="fifty-first")
    assert classify_quota_error(caught.value) == "quota_capacity"
    with app(actors[5]) as db:
        assert reserve(db, notebooks[1], idempotency_key="other-notebook").id


def test_actor_active_is_global_and_notebook_active_four(many_scopes):
    from app.db.leases import claim_job
    from app.db.quota import classify_quota_error

    actors, notebooks = many_scopes
    first = [finalized(actors[0], notebooks[n % 2], str(n)) for n in range(3)]
    for intent in first[:2]:
        with app(actors[0]) as db:
            assert claim_job(db, intent.job_id)
    with pytest.raises(DBAPIError) as caught, app(actors[0]) as db:
        claim_job(db, first[2].job_id)
    assert classify_quota_error(caught.value) == "quota_capacity"
    # First actor uses one slot in this notebook; three other actors fill four.
    for actor in actors[1:4]:
        intent = finalized(actor, notebooks[0], str(actor))
        with app(actor) as db:
            assert claim_job(db, intent.job_id)
    fifth = finalized(actors[4], notebooks[0], "fifth")
    with pytest.raises(DBAPIError) as caught, app(actors[4]) as db:
        claim_job(db, fifth.job_id)
    assert classify_quota_error(caught.value) == "quota_capacity"
    independent = finalized(actors[4], notebooks[1], "independent")
    with app(actors[4]) as db:
        assert claim_job(db, independent.job_id)


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
                    "notebook_text_dispatch_owner",
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


def test_already_started_insert_uses_fresh_snapshot_after_gate(scope):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import sleep

    from app.db.quota import classify_quota_error

    actor, _, _, _, workspace, notebook = scope
    for n in range(9):
        with app(actor) as db:
            reserve(db, notebook, idempotency_key=str(n))
    started = Event()
    backend_pids: list[int] = []

    def delayed_insert():
        try:
            with psycopg.connect(URL, user="notebook_app") as db:
                db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
                pid = db.info.backend_pid
                backend_pids.append(pid)
                started.set()
                # Snapshot is taken before the other transaction commits, gate
                # is deliberately reached afterward in this SAME SQL statement.
                db.execute(
                    """
                    WITH delayed AS MATERIALIZED (SELECT pg_sleep(1))
                    INSERT INTO upload_intents(workspace_id,notebook_id,idempotency_key,filename,sha256,byte_count,media_type)
                    SELECT %s,%s,'stale-snapshot','x.txt',%s,5,'text/plain' FROM delayed
                """,
                    (workspace, notebook, "a" * 64),
                )
        except psycopg.Error as error:
            return classify_quota_error(error)
        return "incorrectly_admitted"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with app(actor) as holder:
            reserve(holder, notebook, idempotency_key="tenth")
            future = pool.submit(delayed_insert)
            assert started.wait(3)
            with psycopg.connect(URL, autocommit=True) as observer:
                for _ in range(100):
                    if observer.execute(
                        "SELECT wait_event FROM pg_stat_activity WHERE pid=%s", (backend_pids[0],)
                    ).fetchone() == ("PgSleep",):
                        break
                    sleep(0.005)
                else:
                    pytest.fail("delayed statement never reached its pre-gate sleep")
        # The late gate winner MUST see the committed tenth row, not its initial snapshot.
        assert future.result(timeout=5) == "quota_capacity"
    with app(actor) as db:
        assert (
            db.execute(
                text("SELECT count(*) FROM upload_intents WHERE notebook_id=:n"), {"n": notebook}
            ).scalar_one()
            == 10
        )
