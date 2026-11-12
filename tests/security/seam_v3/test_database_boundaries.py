"""F3a independent checks on a fresh, isolated PostgreSQL 16 database.

Never point NOTEBOOK_TEST_POSTGRES_URL at a shared/live database.
F3a-01 is retained as an ordinary regression after independent fix verification.
"""

import asyncio
import os
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from app.connections import execution
from app.db.leases import claim_job, finish_job, lock_job_for_publish
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

URL = os.getenv("NOTEBOOK_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL URL required")


@contextmanager
def runtime(actor):
    with psycopg.connect(URL, user="notebook_app") as db:
        db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
        yield db


@pytest.fixture
def corpus():
    c = {
        name: uuid4()
        for name in (
            "owner",
            "editor",
            "ws",
            "note",
            "doc",
            "version",
            "profile",
            "connection",
            "job",
        )
    }
    with psycopg.connect(URL) as db:
        for actor in (c["owner"], c["editor"]):
            db.execute("INSERT INTO app_admissions VALUES (%s)", (actor,))
        db.execute("INSERT INTO workspaces(id,name) VALUES (%s,'F3a synthetic')", (c["ws"],))
        for name in ("owner", "editor"):
            db.execute("INSERT INTO workspace_members VALUES (%s,%s,%s)", (c["ws"], c[name], name))
        db.execute("INSERT INTO notebooks VALUES (%s,%s,'F3a synthetic')", (c["note"], c["ws"]))
        db.execute(
            "INSERT INTO documents(id,workspace_id,notebook_id,created_by,filename,current_version_id) "
            "VALUES (%s,%s,%s,%s,'synthetic.txt',%s)",
            (c["doc"], c["ws"], c["note"], c["owner"], c["version"]),
        )
        db.execute(
            "INSERT INTO document_versions(id,document_id,notebook_id,workspace_id,version,"
            "media_type,byte_count,sha256,storage_key) VALUES (%s,%s,%s,%s,1,'text/plain',5,%s,%s)",
            (
                c["version"],
                c["doc"],
                c["note"],
                c["ws"],
                "a" * 64,
                f"{c['ws']}/{c['note']}/{c['doc']}/{c['version']}/original",
            ),
        )
        db.execute(
            "INSERT INTO index_profiles(id,workspace_id,notebook_id,provider,model,revision,dimensions) "
            "VALUES (%s,%s,%s,'fixture','original','v1',3)",
            (c["profile"], c["ws"], c["note"]),
        )
        db.execute(
            "INSERT INTO user_provider_connections(id,user_id,provider_id,label,secret_id,status) "
            "VALUES (%s,%s,'fixture','Synthetic',%s,'active')",
            (c["connection"], c["owner"], uuid4()),
        )
        db.execute(
            "INSERT INTO ingestion_jobs(id,document_version_id,notebook_id,workspace_id,"
            "actor_user_id,connection_id,index_profile_id,idempotency_key) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'synthetic')",
            (c["job"], c["version"], c["note"], c["ws"], c["owner"], c["connection"], c["profile"]),
        )
    try:
        yield c
    finally:
        with psycopg.connect(URL) as db:
            db.execute("DELETE FROM workspaces WHERE id=%s", (c["ws"],))
            db.execute("DELETE FROM user_provider_connections WHERE id=%s", (c["connection"],))
            db.execute(
                "DELETE FROM app_admissions WHERE user_id IN (%s,%s)", (c["owner"], c["editor"])
            )


def test_referenced_profile_identity_cannot_be_replaced(corpus):
    c = corpus
    # An editor cannot even read this other member's job or connection.
    with runtime(c["editor"]) as db:
        assert db.execute("SELECT id FROM ingestion_jobs").fetchall() == []
        assert db.execute("SELECT id FROM user_provider_connections").fetchall() == []
        try:
            with db.transaction():
                db.execute("DELETE FROM index_profiles WHERE id=%s", (c["profile"],))
                db.execute(
                    "INSERT INTO index_profiles(id,workspace_id,notebook_id,provider,model,"
                    "revision,dimensions) VALUES (%s,%s,%s,'fixture','replacement','v2',7)",
                    (c["profile"], c["ws"], c["note"]),
                )
                db.execute("SET CONSTRAINTS ALL IMMEDIATE")
        except psycopg.Error:
            pass  # A corrected schema must refuse deletion/replacement while referenced.
    with runtime(c["owner"]) as db:
        row = db.execute(
            "SELECT p.model,p.revision,p.dimensions FROM ingestion_jobs j "
            "JOIN index_profiles p ON p.id=j.index_profile_id WHERE j.id=%s",
            (c["job"],),
        ).fetchone()
        assert row == ("original", "v1", 3), (
            "Existing private job now resolves changed shared metadata"
        )


def test_tombstone_definer_grants_do_not_confer_generic_privileges(corpus):
    with runtime(corpus["editor"]) as db:
        assert db.execute(
            "SELECT rolsuper,rolbypassrls,rolcreaterole FROM pg_roles WHERE rolname=current_user"
        ).fetchone() == (False, False, False)
        assert db.execute(
            "SELECT p.prosecdef,p.proconfig,r.rolname FROM pg_proc p "
            "JOIN pg_roles r ON r.oid=p.proowner "
            "WHERE p.oid='public.notebook_delete_document(uuid)'::regprocedure"
        ).fetchone() == (True, ["search_path=pg_catalog"], "notebook_document_lifecycle")
        assert db.execute(
            "SELECT count(*) FROM pg_proc p, LATERAL aclexplode(p.proacl) a "
            "WHERE p.oid='public.notebook_delete_document(uuid)'::regprocedure "
            "AND a.grantee=0 AND a.privilege_type='EXECUTE'"
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT has_schema_privilege('notebook_document_lifecycle','public','CREATE'),"
            "has_table_privilege('notebook_document_lifecycle','documents','INSERT'),"
            "has_column_privilege('notebook_document_lifecycle','documents','filename','UPDATE')"
        ).fetchone() == (False, False, False)
        for role in ("notebook_document_lifecycle", "notebook_membership"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
                db.execute(f"SET ROLE {role}")


@pytest.mark.parametrize("revoke", ["admission", "connection"])
def test_inflight_execution_rechecks_real_database_and_cancels(corpus, monkeypatch, revoke):
    c = corpus
    engine = create_engine(
        make_url(URL).set(drivername="postgresql+psycopg", username="notebook_app"),
        pool_size=1,
        max_overflow=0,
        hide_parameters=True,
    )
    monkeypatch.setattr(execution, "engine", lambda: engine)

    async def exercise():
        started, closed = asyncio.Event(), asyncio.Event()

        async def secret(state):
            assert state.user_id == c["owner"]
            return "synthetic"

        class Source:
            async def embed(self, **kwargs):
                try:
                    started.set()
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        service = execution.ExecutionService(
            read_secret=secret,
            resolve_provider=lambda p: p,
            adapter_factory=lambda p, k: Source(),
            poll_interval=0.01,
        )
        task = asyncio.create_task(
            service.embed(
                actor_id=c["owner"], connection_id=c["connection"], model="fixture", texts=["hi"]
            )
        )
        try:
            await asyncio.wait_for(started.wait(), 2)
            with psycopg.connect(URL) as db:
                if revoke == "admission":
                    db.execute("DELETE FROM app_admissions WHERE user_id=%s", (c["owner"],))
                else:
                    db.execute(
                        "UPDATE user_provider_connections SET status='disconnected' WHERE id=%s",
                        (c["connection"],),
                    )
            with pytest.raises(HTTPException) as error:
                await asyncio.wait_for(task, 2)
            assert error.value.status_code == (403 if revoke == "admission" else 409)
            assert closed.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(exercise())
    finally:
        engine.dispose()


def test_publish_document_lock_serializes_tombstone_and_stale_publication(corpus):
    c = corpus
    engine = create_engine(
        make_url(URL).set(drivername="postgresql+psycopg", username="notebook_app")
    )
    try:
        with Session(engine) as session:

            def identity():
                session.execute(
                    text("SELECT set_config('request.jwt.claim.sub',:uid,true)"),
                    {"uid": str(c["owner"])},
                )

            identity()
            token = claim_job(session, c["job"])
            assert token is not None
            session.commit()
            identity()
            assert lock_job_for_publish(session, c["job"], token)
            # A genuine separate connection cannot tombstone the locked document.
            with runtime(c["editor"]) as db:
                db.execute("SET LOCAL lock_timeout='100ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable), db.transaction():
                    db.execute("SELECT notebook_delete_document(%s)", (c["doc"],))
            session.rollback()
            with runtime(c["editor"]) as db:
                assert db.execute(
                    "SELECT notebook_delete_document(%s)", (c["doc"],)
                ).fetchone() == (True,)
            identity()
            assert not lock_job_for_publish(session, c["job"], token)
            assert not finish_job(session, c["job"], token, status="succeeded")
            session.rollback()
    finally:
        engine.dispose()
