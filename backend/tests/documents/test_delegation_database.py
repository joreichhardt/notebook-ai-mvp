"""0007 acceptance candidates. UNRUN locally; new isolated kube1 database only.

Privileged setup/inspection, direct SCRAM scheduler/app assertions, synthetic
Storage only. Never use these fixture-only LOGIN alterations on a shared DB.
"""

import asyncio
import secrets
from contextlib import contextmanager
from dataclasses import asdict, replace
from time import sleep
from uuid import uuid4

import psycopg
import pytest
from app.db.leases import claim_job, finish_job
from app.db.uploads import confirm_upload_io, finalize_upload, reserve_upload
from app.workers.delegation import (
    BoundedThreadRunner,
    DelegationRepository,
    DelegationUnavailable,
    ExactTextConsumer,
    SchedulerFactory,
)
from app.workers.text import TextWorker
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from test_service import URL, perform, sample

pytest_plugins = ["test_service"]
pytestmark = pytest.mark.skipif(not URL, reason="new isolated kube1 PostgreSQL0007 required")


@pytest.fixture
def delegation_scene(upload_scene, tmp_path):
    c = upload_scene
    material = secrets.token_hex(24)
    with psycopg.connect(URL) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0007",)
        assert db.execute(
            "SELECT rolcanlogin,rolsuper,rolbypassrls FROM pg_roles "
            "WHERE rolname='notebook_text_scheduler'"
        ).fetchone() == (False, False, False)
        db.execute(
            sql.SQL("ALTER ROLE notebook_text_scheduler LOGIN PASSWORD {}").format(
                sql.Literal(material)
            )
        )
    base = make_url(URL)
    passfile = tmp_path / "scheduler.pgpass"
    passfile.touch(mode=0o600)
    values = [
        base.host or "localhost",
        str(base.port or 5432),
        base.database or "",
        "notebook_text_scheduler",
        material,
    ]
    passfile.write_text(
        ":".join(v.replace("\\", "\\\\").replace(":", "\\:") for v in values) + "\n"
    )
    scheduler_engine = create_engine(
        base.set(
            drivername="postgresql+psycopg", username="notebook_text_scheduler", password=None
        ),
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        connect_args={
            "passfile": str(passfile),
            "connect_timeout": 3,
            "options": "-c statement_timeout=3000 -c lock_timeout=1000",
        },
    )
    app_engine = create_engine(
        base.set(drivername="postgresql+psycopg", username="notebook_app"),
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        connect_args={
            "connect_timeout": 3,
            "options": "-c statement_timeout=3000 -c lock_timeout=1000",
        },
    )

    @contextmanager
    def scheduler_tx():
        c["active"].append("scheduler")
        try:
            with Session(scheduler_engine) as db, db.begin():
                yield db
        finally:
            c["active"].remove("scheduler")

    runner = BoundedThreadRunner()
    repo = DelegationRepository(SchedulerFactory(scheduler_tx, 1, 3), runner)
    worker = TextWorker(app_engine, store_factory=lambda auth: c["store"], operation_runner=runner)
    c.update(
        repo=repo,
        runner=runner,
        worker=worker,
        scheduler_tx=scheduler_tx,
        scheduler_engine=scheduler_engine,
    )
    try:
        yield c
    finally:
        assert asyncio.run(runner.close())
        scheduler_engine.dispose()
        app_engine.dispose()
        passfile.unlink()
        with psycopg.connect(URL) as db:
            db.execute("ALTER ROLE notebook_text_scheduler NOLOGIN PASSWORD NULL")
            db.execute("DELETE FROM text_job_tickets WHERE workspace_id=%s", (c["workspace"],))


def stored_intent(c):
    upload = sample()
    with c["actor_tx"](c["actor"]) as db:
        intent = reserve_upload(
            db,
            c["notebook"],
            idempotency_key="atomic",
            filename=upload.filename,
            sha256=upload.sha256,
            byte_count=len(upload.data),
            media_type=upload.media_type,
        )
    with c["io_tx"]() as db:
        token = db.execute(
            text("SELECT token FROM public.claim_upload_io(:i,60)"), {"i": intent.id}
        ).scalar_one()
    with c["io_tx"]() as db:
        assert confirm_upload_io(
            db, intent.id, token, sha256=upload.sha256, byte_count=len(upload.data)
        )
    return intent


def tickets(c):
    with psycopg.connect(URL) as db:
        return db.execute(
            "SELECT job_id,actor_user_id,purpose FROM text_job_tickets WHERE workspace_id=%s",
            (c["workspace"],),
        ).fetchall()


def test_ticket_issuance_rolls_back_with_job_and_replay_is_exactly_once(delegation_scene):
    c = delegation_scene
    intent = stored_intent(c)
    assert tickets(c) == []
    with pytest.raises(RuntimeError, match="abort"), c["actor_tx"](c["actor"]) as db:
        finalize_upload(db, intent.id)
        raise RuntimeError("abort")
    assert tickets(c) == []
    with psycopg.connect(URL) as db:
        assert (
            db.execute("SELECT id FROM ingestion_jobs WHERE id=%s", (intent.job_id,)).fetchone()
            is None
        )
    for _ in range(2):
        with c["actor_tx"](c["actor"]) as db:
            finalize_upload(db, intent.id)
    assert tickets(c) == [(intent.job_id, c["actor"], "text_extract_v1")]


def test_failing_ticket_insert_aborts_entire_finalization(delegation_scene):
    c = delegation_scene
    intent = stored_intent(c)
    # Duplicate private ticket is privileged fault injection, not a runtime API.
    with psycopg.connect(URL) as db:
        db.execute(
            "INSERT INTO text_job_tickets(job_id,intent_id,actor_user_id,workspace_id,"
            "notebook_id,document_version_id,purpose) VALUES (%s,%s,%s,%s,%s,%s,'text_extract_v1')",
            (
                intent.job_id,
                intent.id,
                c["actor"],
                c["workspace"],
                c["notebook"],
                intent.version_id,
            ),
        )
    with pytest.raises(IntegrityError), c["actor_tx"](c["actor"]) as db:
        finalize_upload(db, intent.id)
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT state FROM upload_intents WHERE id=%s", (intent.id,)
        ).fetchone() == ("stored",)
        assert (
            db.execute("SELECT id FROM ingestion_jobs WHERE id=%s", (intent.job_id,)).fetchone()
            is None
        )


def test_acl_isolation_fixed_definer_and_no_raw_job_visibility_widening(delegation_scene):
    c = delegation_scene
    receipt = perform(c)
    with c["scheduler_tx"]() as db:
        assert db.execute(text("SELECT current_user=session_user")).scalar_one() is True
        for table in ("text_job_tickets", "ingestion_jobs", "upload_intents", "documents"):
            assert not db.execute(
                text("SELECT has_table_privilege(current_user,:t,'SELECT')"),
                {"t": "public." + table},
            ).scalar_one()
        assert not db.execute(
            text("SELECT pg_has_role(current_user,'notebook_text_dispatch_owner','MEMBER')")
        ).scalar_one()
    with c["actor_tx"](c["editor"]) as db:
        assert (
            db.execute(
                text("SELECT id FROM ingestion_jobs WHERE id=:j"), {"j": receipt.job.id}
            ).first()
            is None
        )
    signatures = [
        "claim_text_job(integer)",
        "validate_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid)",
        "renew_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid,integer)",
        "ack_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid)",
    ]
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT rolcanlogin,rolsuper,rolbypassrls FROM pg_roles "
            "WHERE rolname='notebook_text_dispatch_owner'"
        ).fetchone() == (False, False, False)
        for signature in signatures:
            assert db.execute(
                "SELECT prosecdef,proconfig FROM pg_proc WHERE oid=%s::regprocedure",
                ("public." + signature,),
            ).fetchone() == (True, ["search_path=pg_catalog"])
            for role in ("notebook_app", "notebook_storage_io"):
                assert db.execute(
                    "SELECT has_function_privilege(%s,%s,'EXECUTE')", (role, "public." + signature)
                ).fetchone() == (False,)
        assert db.execute(
            "SELECT has_function_privilege('notebook_text_scheduler',"
            "'public.text_dispatch_eligible()','EXECUTE')"
        ).fetchone() == (False,)
    with c["actor_tx"](c["actor"]) as db:
        assert not db.execute(
            text("SELECT pg_has_role(current_user,'notebook_text_scheduler','MEMBER')")
        ).scalar_one()


@pytest.mark.parametrize(
    "field",
    [
        "actor_user_id",
        "workspace_id",
        "notebook_id",
        "document_version_id",
        "job_id",
        "delivery_token",
        "purpose",
    ],
)
def test_forged_scope_fence_and_purpose_rejected_by_sql(delegation_scene, field):
    c = delegation_scene
    perform(c)

    async def run():
        issued = await c["repo"].claim()
        assert issued is not None
        fake = replace(issued, **{field: "embedding" if field == "purpose" else uuid4()})
        # Direct SQL checks even bypass Python's shape/purpose guard.
        with c["scheduler_tx"]() as db:
            params = asdict(fake)
            for function, expected in (("validate_text_job", None), ("ack_text_job", False)):
                query = {
                    "validate_text_job": text(
                        "SELECT public.validate_text_job(:job_id,:actor_user_id,"
                        ":workspace_id,:notebook_id,:document_version_id,:purpose,:delivery_token)"
                    ),
                    "ack_text_job": text(
                        "SELECT public.ack_text_job(:job_id,:actor_user_id,"
                        ":workspace_id,:notebook_id,:document_version_id,:purpose,:delivery_token)"
                    ),
                }
                assert db.execute(query[function], params).scalar_one() is expected
            assert (
                db.execute(
                    text(
                        "SELECT public.renew_text_job(:job_id,:actor_user_id,"
                        ":workspace_id,:notebook_id,:document_version_id,:purpose,:delivery_token,60)"
                    ),
                    params,
                ).scalar_one()
                is False
            )
        assert await c["repo"].validate(issued) == "executable"
        assert await c["repo"].renew(issued)
        assert not await c["repo"].acknowledge(issued)  # queued is not terminal
        assert (
            await c["worker"].run_one(actor_id=issued.actor_user_id, job_id=issued.job_id)
            == "succeeded"
        )
        with c["scheduler_tx"]() as db:
            assert db.execute(query["ack_text_job"], params).scalar_one() is False
        assert await c["repo"].acknowledge(issued)

    asyncio.run(run())


@pytest.mark.parametrize("event", ["admission", "member", "current", "delete"])
def test_revoked_restored_or_stale_work_never_blocks_next_eligible_ticket(delegation_scene, event):
    c = delegation_scene
    old = perform(c, key="old")
    issued = asyncio.run(c["repo"].claim())
    assert issued is not None
    with psycopg.connect(URL) as db:
        if event == "admission":
            db.execute("DELETE FROM app_admissions WHERE user_id=%s", (c["actor"],))
            db.execute("INSERT INTO app_admissions VALUES (%s)", (c["actor"],))
        elif event == "member":
            db.execute("DELETE FROM workspace_members WHERE user_id=%s", (c["actor"],))
            db.execute(
                "INSERT INTO workspace_members VALUES (%s,%s,'owner')", (c["workspace"], c["actor"])
            )
        elif event == "current":
            db.execute(
                "UPDATE documents SET current_version_id=NULL WHERE id=%s", (old.document.id,)
            )
        else:
            db.execute("DELETE FROM documents WHERE id=%s", (old.document.id,))
    new = perform(c, key="new")

    async def run():
        assert await c["repo"].validate(issued) is None
        assert not await c["repo"].renew(issued)
        assert not await c["repo"].acknowledge(issued)
        selected = await c["repo"].claim()
        assert selected is not None and selected.job_id == new.job.id

    asyncio.run(run())


def test_duplicate_delivery_expiry_and_ingestion_expiry_are_separate(delegation_scene):
    c = delegation_scene
    receipt = perform(c)

    async def run():
        first = await c["repo"].claim(seconds=1)
        assert first is not None
        assert await c["repo"].claim() is None
        with c["actor_tx"](c["actor"]) as db:
            assert claim_job(db, receipt.job.id, lease_seconds=8)
        await asyncio.sleep(6.1)  # real delivery expiry + backoff, no protected clock edits
        assert await c["repo"].validate(first) is None
        assert not await c["repo"].renew(first)
        assert not await c["repo"].acknowledge(first)
        assert await c["repo"].claim() is None  # ingestion still live
        await asyncio.sleep(2.1)
        second = await c["repo"].claim()
        assert second is not None and second.job_id == first.job_id
        assert second.delivery_token != first.delivery_token
        assert await c["repo"].validate(second) == "executable"
        assert not await c["repo"].acknowledge(first)

    asyncio.run(run())


def test_terminal_after_publication_crash_acks_without_second_storage_read(delegation_scene):
    c = delegation_scene
    perform(c)
    c["store"].actions.clear()

    async def run():
        issued = await c["repo"].claim(seconds=1)
        assert issued is not None
        assert await c["repo"].validate(issued) == "executable"
        assert (
            await c["worker"].run_one(actor_id=issued.actor_user_id, job_id=issued.job_id)
            == "succeeded"
        )
        # Simulate process death before ack; existing worker publication is real SQL.
        await asyncio.sleep(6.1)
        one = ExactTextConsumer(c["repo"], c["worker"], pool_timeout=1, connect_timeout=3)
        assert await one.run_one() == "succeeded"
        assert await one.run_one() == "idle"
        assert not await c["repo"].acknowledge(issued)

    asyncio.run(run())
    assert c["store"].actions == ["read"]


def test_execution_attempts_stay_bounded_and_terminal_ack_cannot_replay(delegation_scene):
    c = delegation_scene
    receipt = perform(c)
    for _ in range(5):
        with c["actor_tx"](c["actor"]) as db:
            assert claim_job(db, receipt.job.id, lease_seconds=1)
        sleep(1.05)
    assert asyncio.run(c["repo"].claim()) is None
    # Exhausted ingestion cannot starve a newer job in the same notebook.
    other = perform(c, key="other")

    async def run():
        issued = await c["repo"].claim()
        assert issued is not None and issued.job_id == other.job.id
        with c["actor_tx"](c["actor"]) as db:
            token = claim_job(db, other.job.id)
            assert token and finish_job(
                db, other.job.id, token, status="failed", error_code="test_failed"
            )
        assert await c["repo"].validate(issued) == "failed"
        assert await c["repo"].acknowledge(issued)
        assert not await c["repo"].acknowledge(issued)
        assert await c["repo"].validate(issued) is None

    asyncio.run(run())


def test_repository_rejects_privileged_set_role_not_direct_scheduler(delegation_scene):
    c = delegation_scene
    perform(c)
    root = create_engine(
        make_url(URL).set(drivername="postgresql+psycopg"),
        hide_parameters=True,
        pool_timeout=1,
        connect_args={"connect_timeout": 3},
    )

    @contextmanager
    def assumed():
        with Session(root) as db, db.begin():
            db.execute(text("SET LOCAL ROLE notebook_text_scheduler"))
            yield db

    async def run():
        repo = DelegationRepository(SchedulerFactory(assumed, 1, 3), c["runner"])
        with pytest.raises(DelegationUnavailable, match="unsafe_scheduler_role"):
            await repo.claim()

    try:
        asyncio.run(run())
    finally:
        root.dispose()


def test_quota_saturated_actor_delivery_does_not_starve_other_actor(delegation_scene):
    c = delegation_scene
    running = [perform(c, key=f"running-{n}") for n in range(2)]
    blocked = perform(c, key="quota-blocked")
    other = perform(c, actor=c["editor"], key="other-actor")
    for receipt in running:
        with c["actor_tx"](c["actor"]) as db:
            assert claim_job(db, receipt.job.id, lease_seconds=60)
    c["store"].actions.clear()

    async def run():
        one = ExactTextConsumer(c["repo"], c["worker"], pool_timeout=1, connect_timeout=3)
        assert await one.run_one() == "not_claimed"
        assert await one.run_one() == "succeeded"
        assert await one.run_one() == "idle"  # blocked ticket still under delivery lease

    asyncio.run(run())
    assert c["store"].actions == ["read"]
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT status,attempts FROM ingestion_jobs WHERE id=%s", (blocked.job.id,)
        ).fetchone() == ("queued", 0)
        assert db.execute(
            "SELECT status,attempts FROM ingestion_jobs WHERE id=%s", (other.job.id,)
        ).fetchone() == ("succeeded", 1)


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM public.text_job_tickets",
        "SELECT * FROM public.claim_text_job(60)",
        "SET LOCAL ROLE notebook_text_scheduler",
        "SET LOCAL ROLE notebook_text_dispatch_owner",
    ],
)
def test_app_cannot_read_tickets_schedule_or_assume_privilege(delegation_scene, statement):
    c = delegation_scene
    perform(c)
    with pytest.raises(DBAPIError) as caught, c["actor_tx"](c["actor"]) as db:
        # Fixed test allowlist, never application-controlled SQL.
        db.execute(text(statement))
    assert isinstance(caught.value.orig, psycopg.errors.InsufficientPrivilege)
