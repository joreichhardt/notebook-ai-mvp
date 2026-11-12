"""Persistence contract, using privileged fixtures ONLY in an isolated test DB.

Runtime assertions connect directly as notebook_app (NOSUPERUSER/NOBYPASSRLS).
Run migrations separately before these tests; never point at a shared database.
"""

import os
import subprocess
import sys
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import LiteralString, cast
from uuid import uuid4

import psycopg
import pytest

URL = os.getenv("NOTEBOOK_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL URL not configured")


@pytest.mark.parametrize("upgrade_via", [None, "0003", "0004"])
def test_fresh_migrations_and_model_columns(upgrade_via):
    from app.db.models import (
        Chunk,
        Document,
        DocumentVersion,
        IndexProfile,
        IngestionJob,
        UploadIntentRecord,
    )
    from psycopg import sql
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.engine import make_url

    name = f"a4_migration_{uuid4().hex}"
    url = make_url(URL).set(database=name, drivername="postgresql+psycopg")
    root = Path(__file__).resolve().parents[3]
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(
                sql.Identifier(name)
            )
        )
    engine = create_engine(url)
    actor, workspace, notebook, profile = [uuid4() for _ in range(4)]
    job = None
    version = None
    try:
        if upgrade_via:
            result = subprocess.run(
                [
                    str(root / ".venv/bin/alembic"),
                    "-c",
                    "backend/migrations/alembic.ini",
                    "upgrade",
                    upgrade_via,
                ],
                cwd=root,
                env={
                    **os.environ,
                    "PYTHONPATH": str(root / "backend"),
                    "NOTEBOOK_MIGRATION_DATABASE_URL": url.render_as_string(hide_password=False),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, "0003 migration failed (output suppressed)"
            with psycopg.connect(URL, dbname=name, user="notebook_app") as db:
                assert db.execute(
                    "SELECT has_table_privilege(current_user,'public.index_profiles','DELETE')"
                ).fetchone() == (upgrade_via == "0003",)
            # Existing records must survive both privilege and lifecycle upgrades.
            with psycopg.connect(URL, dbname=name) as admin:
                admin.execute("INSERT INTO app_admissions VALUES (%s)", (actor,))
                admin.execute("INSERT INTO workspaces(id,name) VALUES (%s,'upgrade')", (workspace,))
                admin.execute(
                    "INSERT INTO workspace_members VALUES (%s,%s,'owner')", (workspace, actor)
                )
                admin.execute(
                    "INSERT INTO notebooks VALUES (%s,%s,'upgrade')", (notebook, workspace)
                )
                _, version = insert_document(admin, workspace, notebook, actor)
                connection = add_connection(admin, actor)
                admin.execute(
                    "INSERT INTO index_profiles(id,workspace_id,notebook_id,provider,model,"
                    "revision,dimensions) VALUES (%s,%s,%s,'fixture','original','v1',3)",
                    (profile, workspace, notebook),
                )
                job = insert_job(
                    admin, workspace, notebook, version, actor, connection, profile, status="queued"
                )
        result = subprocess.run(
            [
                str(root / ".venv/bin/alembic"),
                "-c",
                "backend/migrations/alembic.ini",
                "upgrade",
                "head",
            ],
            cwd=root,
            env={
                **os.environ,
                "PYTHONPATH": str(root / "backend"),
                "NOTEBOOK_MIGRATION_DATABASE_URL": url.render_as_string(hide_password=False),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        if upgrade_via:
            # A legacy queued job MUST block0006 without rewriting data/revision.
            assert result.returncode != 0
            with psycopg.connect(URL, dbname=name) as admin:
                assert admin.execute("SELECT version_num FROM alembic_version").fetchone() == (
                    upgrade_via,
                )
                assert admin.execute(
                    "SELECT status,attempts,lease_token FROM ingestion_jobs WHERE id=%s", (job,)
                ).fetchone() == ("queued", 0, None)
                # Explicit exact-ID retirement in this isolated fixture only.
                admin.execute(
                    "UPDATE ingestion_jobs SET status='failed' WHERE id=%s AND status='queued' AND attempts=0",
                    (job,),
                )
            result = subprocess.run(
                [
                    str(root / ".venv/bin/alembic"),
                    "-c",
                    "backend/migrations/alembic.ini",
                    "upgrade",
                    "head",
                ],
                cwd=root,
                env={
                    **os.environ,
                    "PYTHONPATH": str(root / "backend"),
                    "NOTEBOOK_MIGRATION_DATABASE_URL": url.render_as_string(hide_password=False),
                },
                capture_output=True,
                text=True,
                check=False,
            )
        assert result.returncode == 0, "fresh migration failed (output suppressed to protect URL)"
        with psycopg.connect(URL, dbname=name, user="notebook_app") as db:
            assert db.execute(
                "SELECT has_table_privilege(current_user,'public.index_profiles','DELETE'),"
                "has_table_privilege(current_user,'public.index_profiles','UPDATE'),"
                "has_table_privilege(current_user,'public.index_profiles','INSERT'),"
                "has_table_privilege(current_user,'public.index_profiles','SELECT')"
            ).fetchone() == (False, False, True, True)
            if upgrade_via:
                assert version is not None
                db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
                assert db.execute(
                    "SELECT j.id,p.id,p.model,p.revision,p.dimensions FROM ingestion_jobs j "
                    "JOIN index_profiles p ON p.id=j.index_profile_id WHERE j.id=%s",
                    (job,),
                ).fetchone() == (job, profile, "original", "v1", 3)
                assert db.execute(
                    "SELECT processing_status,error_code FROM document_versions WHERE id=%s",
                    (version,),
                ).fetchone() == ("failed", "not_processed")
        inspector = inspect(engine)
        for_model = (
            Document,
            DocumentVersion,
            IndexProfile,
            Chunk,
            IngestionJob,
            UploadIntentRecord,
        )
        for model in for_model:
            actual = {
                column["name"]: column["nullable"]
                for column in inspector.get_columns(model.__tablename__)
            }
            expected = {column.name: column.nullable for column in model.__table__.columns}
            assert actual == expected
    finally:
        engine.dispose()
        with psycopg.connect(URL, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def test_document_tables_are_rls_protected():
    with psycopg.connect(URL) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0006",)
        db.execute("SET ROLE notebook_app")
        assert db.execute(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone() == (False, False)
        for table in (
            "documents",
            "document_versions",
            "chunks",
            "index_profiles",
            "ingestion_jobs",
        ):
            assert db.execute(f"SELECT * FROM {table}").fetchall() == []
            assert db.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=%s",
                (table,),
            ).fetchone() == (True, True)


@contextmanager
def runtime(actor=None):
    with psycopg.connect(URL, user="notebook_app") as db:
        if actor:
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
        yield db


def insert_document(db, ws, note, actor):
    document, version = uuid4(), uuid4()
    db.execute(
        "INSERT INTO documents(id,workspace_id,notebook_id,created_by,filename,current_version_id) "
        "VALUES (%s,%s,%s,%s,'sample.txt',%s)",
        (document, ws, note, actor, version),
    )
    insert_version(db, ws, note, document, version)
    return document, version


def insert_version(db, ws, note, document, version, number=1):
    db.execute(
        "INSERT INTO document_versions(id,document_id,notebook_id,workspace_id,version,"
        "media_type,byte_count,sha256,storage_key) VALUES (%s,%s,%s,%s,%s,'text/plain',5,%s,%s)",
        (
            version,
            document,
            note,
            ws,
            number,
            "a" * 64,
            f"{ws}/{note}/{document}/{version}/original",
        ),
    )


def insert_chunk(db, ws, note, doc, version, ordinal=0):
    chunk = uuid4()
    db.execute(
        "INSERT INTO chunks(id,document_version_id,document_id,notebook_id,workspace_id,"
        "ordinal,text,sha256,start_offset,end_offset) VALUES (%s,%s,%s,%s,%s,%s,'hello',%s,0,5)",
        (chunk, version, doc, note, ws, ordinal, "b" * 64),
    )
    return chunk


def insert_job(
    db, ws, note, version, actor, connection=None, profile=None, key=None, *, status="failed"
):
    job = uuid4()
    db.execute(
        "INSERT INTO ingestion_jobs(id,document_version_id,notebook_id,workspace_id,"
        "actor_user_id,connection_id,index_profile_id,idempotency_key,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (job, version, note, ws, actor, connection, profile, key or str(job), status),
    )
    return job


@pytest.fixture(autouse=True)
def version_database(request, monkeypatch):
    # Keep the original0005 raw-job execution/fencing assertions in the default
    # suite, against a real0005 schema and frozen0005 helper implementation.
    legacy = {
        "test_lease_claim_recovery_and_fencing",
        "test_claim_rechecks_current_authorization_and_bounds",
        "test_publication_transaction_rolls_back_expired_lease",
    }
    if request.node.originalname not in legacy:
        yield
        return
    from database_versions import isolated_database

    with isolated_database(URL, "0005") as url:
        monkeypatch.setattr(sys.modules[__name__], "URL", url)
        monkeypatch.setattr(
            sys.modules[__name__], "insert_job", partial(insert_job, status="queued")
        )
        yield


@pytest.fixture
def corpus():
    owner, editor, viewer, outsider, unadmitted = [uuid4() for _ in range(5)]
    ws, other, note, other_note = [uuid4() for _ in range(4)]
    # Privileged fixture setup is never used by production/API/worker code.
    with psycopg.connect(URL) as admin:
        for actor in (owner, editor, viewer, outsider):
            admin.execute("INSERT INTO app_admissions VALUES (%s)", (actor,))
        for workspace in (ws, other):
            admin.execute("INSERT INTO workspaces(id,name) VALUES (%s,'fixture')", (workspace,))
        for actor, role in (
            (owner, "owner"),
            (editor, "editor"),
            (viewer, "viewer"),
            (unadmitted, "owner"),
        ):
            admin.execute("INSERT INTO workspace_members VALUES (%s,%s,%s)", (ws, actor, role))
        admin.execute("INSERT INTO workspace_members VALUES (%s,%s,'owner')", (other, outsider))
        for notebook, workspace in ((note, ws), (other_note, other)):
            admin.execute("INSERT INTO notebooks VALUES (%s,%s,'fixture')", (notebook, workspace))
        doc, version = insert_document(admin, ws, note, owner)
        other_doc, other_version = insert_document(admin, other, other_note, outsider)
        chunk = insert_chunk(admin, ws, note, doc, version)
        job = insert_job(admin, ws, note, version, owner)
        profile = uuid4()
        admin.execute(
            "INSERT INTO index_profiles(id,workspace_id,notebook_id,provider,model,revision,"
            "dimensions) VALUES (%s,%s,%s,'fixture','embed','v1',3)",
            (profile, ws, note),
        )
    yield locals()
    with psycopg.connect(URL) as admin:
        admin.execute("DELETE FROM workspaces WHERE id IN (%s,%s)", (ws, other))
        admin.execute(
            "DELETE FROM user_provider_connections WHERE user_id IN (%s,%s)", (owner, editor)
        )
        admin.execute(
            "DELETE FROM app_admissions WHERE user_id=ANY(%s)", ([owner, editor, viewer, outsider],)
        )


@pytest.mark.parametrize("actor_name", [None, "outsider", "unadmitted"])
def test_missing_context_foreign_workspace_and_admission_are_denied(corpus, actor_name):
    c = corpus
    with runtime(c.get(actor_name)) as db:
        for table in (
            "documents",
            "document_versions",
            "chunks",
            "index_profiles",
            "ingestion_jobs",
        ):
            assert (
                db.execute(f"SELECT id FROM {table} WHERE workspace_id=%s", (c["ws"],)).fetchall()
                == []
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
            insert_document(db, c["ws"], c["note"], c["owner"])


@pytest.mark.parametrize("actor_name", ["owner", "editor", "viewer"])
def test_member_reads_and_actor_private_jobs(corpus, actor_name):
    c = corpus
    with runtime(c[actor_name]) as db:
        for table in ("documents", "document_versions", "chunks", "index_profiles"):
            assert len(db.execute(f"SELECT id FROM {table}").fetchall()) == 1
        jobs = db.execute("SELECT id FROM ingestion_jobs").fetchall()
        assert jobs == ([(c["job"],)] if actor_name == "owner" else [])
        if actor_name == "viewer":
            for action in (
                lambda: insert_document(db, c["ws"], c["note"], c["viewer"]),
                lambda: insert_chunk(db, c["ws"], c["note"], c["doc"], c["version"], 1),
                lambda: insert_job(db, c["ws"], c["note"], c["version"], c["viewer"]),
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
                    action()
            assert db.execute("UPDATE documents SET filename='no' RETURNING id").fetchall() == []
            assert db.execute("DELETE FROM chunks RETURNING id").fetchall() == []
        else:
            insert_document(db, c["ws"], c["note"], c[actor_name])


def test_removed_membership_and_admission_take_effect(corpus):
    c = corpus
    with runtime(c["owner"]) as db:
        assert db.execute("SELECT id FROM chunks").fetchone()
        with psycopg.connect(URL) as admin:
            admin.execute(
                "DELETE FROM workspace_members WHERE workspace_id=%s AND user_id=%s",
                (c["ws"], c["owner"]),
            )
        for table in (
            "documents",
            "document_versions",
            "chunks",
            "index_profiles",
            "ingestion_jobs",
        ):
            assert db.execute(f"SELECT id FROM {table}").fetchall() == []
    with psycopg.connect(URL) as admin:
        admin.execute("DELETE FROM app_admissions WHERE user_id=%s", (c["editor"],))
    with runtime(c["editor"]) as db:
        assert db.execute("SELECT id FROM documents").fetchall() == []


def test_cross_scope_integrity_even_for_privileged_fixture(corpus):
    c = corpus
    with psycopg.connect(URL) as db:
        for action in (
            lambda: insert_document(db, c["other"], c["note"], c["owner"]),
            lambda: insert_version(db, c["other"], c["other_note"], c["doc"], uuid4(), 2),
            lambda: insert_chunk(db, c["ws"], c["note"], c["other_doc"], c["version"], 2),
            lambda: insert_job(db, c["other"], c["other_note"], c["version"], c["owner"]),
            lambda: insert_job(
                db,
                c["other"],
                c["other_note"],
                c["other_version"],
                c["outsider"],
                uuid4(),
                c["profile"],
            ),
        ):
            with pytest.raises(psycopg.errors.ForeignKeyViolation), db.transaction():
                action()
                db.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with pytest.raises(psycopg.errors.ForeignKeyViolation), db.transaction():
            db.execute(
                "UPDATE documents SET current_version_id=%s WHERE id=%s",
                (c["other_version"], c["doc"]),
            )
            db.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_current_version_and_deleted_source_visibility(corpus):
    c = corpus
    with runtime(c["owner"]) as db:
        version2 = uuid4()
        db.execute("UPDATE documents SET current_version_id=%s WHERE id=%s", (version2, c["doc"]))
        insert_version(db, c["ws"], c["note"], c["doc"], version2, 2)
        assert db.execute("SELECT id FROM document_versions").fetchall() == [(version2,)]
        assert db.execute("SELECT id FROM chunks").fetchall() == []
        assert (
            db.execute("UPDATE ingestion_jobs SET status='cancelled' RETURNING id").fetchall() == []
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
            insert_chunk(db, c["ws"], c["note"], c["doc"], c["version"], 1)
        assert db.execute("SELECT notebook_delete_document(%s)", (c["doc"],)).fetchone() == (True,)
        assert db.execute("SELECT id FROM documents").fetchall() == []
        assert db.execute("SELECT id FROM document_versions").fetchall() == []
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
            insert_job(db, c["ws"], c["note"], version2, c["owner"])
        # Raw history is retained, but remains actor-private and non-writable.
        assert db.execute("SELECT id FROM ingestion_jobs").fetchall() == [(c["job"],)]


@pytest.mark.parametrize("actor_name", [None, "viewer", "outsider", "unadmitted"])
def test_delete_rpc_is_narrow_and_does_not_escalate(corpus, actor_name):
    c = corpus
    with runtime(c.get(actor_name)) as db:
        assert db.execute("SELECT notebook_delete_document(%s)", (c["doc"],)).fetchone() == (False,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
            db.execute("SET ROLE notebook_document_lifecycle")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
            db.execute("INSERT INTO workspace_members VALUES (%s,%s,'owner')", (c["ws"], uuid4()))


@pytest.mark.parametrize("actor_name", ["owner", "editor", "viewer", "outsider"])
def test_profiles_cannot_be_deleted_or_replaced(corpus, actor_name):
    c = corpus
    with runtime(c["owner"]) as db:
        connection = add_connection(db, c["owner"])
        job = insert_job(db, c["ws"], c["note"], c["version"], c["owner"], connection, c["profile"])
    with runtime(c[actor_name]) as db:
        if actor_name != "owner":
            assert db.execute("SELECT id FROM ingestion_jobs").fetchall() == []
            assert db.execute("SELECT id FROM user_provider_connections").fetchall() == []
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
            db.execute("DELETE FROM index_profiles WHERE id=%s", (c["profile"],))
            db.execute(
                "INSERT INTO index_profiles(id,workspace_id,notebook_id,provider,model,"
                "revision,dimensions) VALUES (%s,%s,%s,'fixture','replacement','v2',7)",
                (c["profile"], c["ws"], c["note"]),
            )
            db.execute("SET CONSTRAINTS ALL IMMEDIATE")
    with runtime(c["owner"]) as db:
        assert db.execute(
            "SELECT j.id,p.id,p.model,p.revision,p.dimensions FROM ingestion_jobs j "
            "JOIN index_profiles p ON p.id=j.index_profile_id WHERE j.id=%s",
            (job,),
        ).fetchone() == (job, c["profile"], "embed", "v1", 3)


@pytest.mark.parametrize("actor_name", ["owner", "editor", "viewer", "outsider"])
def test_profile_cleanup_only_through_authorized_notebook(corpus, actor_name):
    c = corpus
    with runtime(c["owner"]) as db:
        connection = add_connection(db, c["owner"])
        job = insert_job(db, c["ws"], c["note"], c["version"], c["owner"], connection, c["profile"])
    allowed = actor_name in ("owner", "editor")
    with runtime(c[actor_name]) as db:
        deleted = db.execute(
            "DELETE FROM notebooks WHERE id=%s RETURNING id", (c["note"],)
        ).fetchall()
        assert deleted == ([(c["note"],)] if allowed else [])
        db.execute("SET CONSTRAINTS ALL IMMEDIATE")
    # Inspect as the original actor after commit, not a privileged cleanup bypass.
    with runtime(c["owner"]) as db:
        for table, identity in (
            ("notebooks", c["note"]),
            ("index_profiles", c["profile"]),
            ("documents", c["doc"]),
            ("document_versions", c["version"]),
            ("chunks", c["chunk"]),
            ("ingestion_jobs", job),
        ):
            assert db.execute(
                cast(LiteralString, f"SELECT id FROM {table} WHERE id=%s"), (identity,)
            ).fetchall() == ([] if allowed else [(identity,)])
        assert db.execute(
            "SELECT id FROM user_provider_connections WHERE id=%s", (connection,)
        ).fetchone() == (connection,)


def test_new_profile_identity_remains_supported(corpus):
    c = corpus
    new_profile = uuid4()
    with runtime(c["editor"]) as db:
        db.execute(
            "INSERT INTO index_profiles(id,workspace_id,notebook_id,provider,model,"
            "revision,dimensions) VALUES (%s,%s,%s,'fixture','replacement','v2',7)",
            (new_profile, c["ws"], c["note"]),
        )
        assert db.execute("SELECT count(*) FROM index_profiles").fetchone() == (2,)
        # Even an unreferenced identity cannot be recycled by ordinary runtime SQL.
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
            db.execute("DELETE FROM index_profiles WHERE id=%s", (new_profile,))


def test_metadata_is_immutable(corpus):
    c = corpus
    with runtime(c["owner"]) as db:
        for table, assignment in (
            ("document_versions", "byte_count=6"),
            ("index_profiles", "dimensions=4"),
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
                db.execute(cast(LiteralString, f"UPDATE {table} SET {assignment}"))
        with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
            db.execute("UPDATE documents SET created_by=%s", (c["editor"],))
        with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
            db.execute("UPDATE ingestion_jobs SET idempotency_key='changed'")
    with psycopg.connect(URL) as admin:
        with pytest.raises(psycopg.errors.CheckViolation), admin.transaction():
            admin.execute("UPDATE document_versions SET byte_count=6")
        with pytest.raises(psycopg.errors.CheckViolation), admin.transaction():
            admin.execute("UPDATE index_profiles SET dimensions=4")


@pytest.mark.parametrize(
    "assignment",
    [
        "attempts=-1",
        "attempts=6",
        "status='unknown'",
        "status='running'",
        "lease_token=gen_random_uuid()",
        "error_code='raw provider error with secret'",
    ],
)
def test_job_state_constraints(corpus, assignment):
    with (
        runtime(corpus["owner"]) as db,
        pytest.raises(psycopg.errors.CheckViolation),
        db.transaction(),
    ):
        db.execute(cast(LiteralString, f"UPDATE ingestion_jobs SET {assignment}"))


@pytest.mark.parametrize(
    "assignment",
    [
        "ordinal=-1",
        "page=0",
        "page=501",
        "start_offset=-1",
        "end_offset=0",
        "end_offset=2000001",
        "sha256='bad'",
        "text=''",
    ],
)
def test_chunk_bounds(corpus, assignment):
    with (
        runtime(corpus["owner"]) as db,
        pytest.raises(psycopg.errors.CheckViolation),
        db.transaction(),
    ):
        db.execute(cast(LiteralString, f"UPDATE chunks SET {assignment}"))


def add_connection(db, actor, provider="fixture", status="active"):
    connection = uuid4()
    db.execute(
        "INSERT INTO user_provider_connections(id,user_id,provider_id,label,secret_id,status) "
        "VALUES (%s,%s,%s,'fixture',%s,%s)",
        (connection, actor, provider, uuid4(), status),
    )
    return connection


def test_job_connection_profile_pairing_ownership_revocation_and_history(corpus):
    c = corpus
    with runtime(c["editor"]) as db:
        foreign = add_connection(db, c["editor"])
    with runtime(c["owner"]) as db:
        own = add_connection(db, c["owner"])
        inactive = add_connection(db, c["owner"], status="inactive")
        different = add_connection(db, c["owner"], provider="different")
        for connection, profile in ((own, None), (None, c["profile"])):
            with pytest.raises(psycopg.Error), db.transaction():
                insert_job(db, c["ws"], c["note"], c["version"], c["owner"], connection, profile)
        for connection in (foreign, inactive, different, uuid4()):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
                insert_job(
                    db, c["ws"], c["note"], c["version"], c["owner"], connection, c["profile"]
                )
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
            insert_job(db, c["ws"], c["note"], c["version"], c["editor"])
        job = insert_job(db, c["ws"], c["note"], c["version"], c["owner"], own, c["profile"])
        db.execute("DELETE FROM user_provider_connections WHERE id=%s", (own,))
        assert db.execute(
            "SELECT connection_id FROM ingestion_jobs WHERE id=%s", (job,)
        ).fetchone() == (own,)
        assert (
            db.execute(
                "UPDATE ingestion_jobs SET status='failed' WHERE id=%s RETURNING id", (job,)
            ).fetchall()
            == []
        )


def test_lease_claim_recovery_and_fencing(corpus):
    from leases_0005 import claim_job, finish_job, heartbeat_job
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    c = corpus
    engine = create_engine(
        URL.replace("postgresql://", "postgresql+psycopg://"), connect_args={"user": "notebook_app"}
    )
    try:
        with Session(engine) as first, Session(engine) as second:
            for session in (first, second):
                session.execute(
                    text("SELECT set_config('request.jwt.claim.sub',:uid,true)"),
                    {"uid": str(c["owner"])},
                )
            token = claim_job(first, c["job"])
            assert token is not None
            # A concurrent caller does not block or receive the already claimed row.
            assert claim_job(second, c["job"]) is None
            first.commit()
            assert claim_job(second, c["job"]) is None
            assert heartbeat_job(second, c["job"], uuid4()) is False
            assert heartbeat_job(second, c["job"], token) is True
            second.commit()
        with psycopg.connect(URL) as admin:
            admin.execute(
                "UPDATE ingestion_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
                (c["job"],),
            )
        with Session(engine) as session:
            session.execute(
                text("SELECT set_config('request.jwt.claim.sub',:uid,true)"),
                {"uid": str(c["owner"])},
            )
            replacement = claim_job(session, c["job"])
            assert replacement and replacement != token
            assert heartbeat_job(session, c["job"], token) is False
            assert finish_job(session, c["job"], token, status="succeeded") is False
            assert finish_job(session, c["job"], replacement, status="succeeded") is True
            session.commit()
        with runtime(c["owner"]) as db:
            assert db.execute(
                "SELECT status,attempts,lease_token FROM ingestion_jobs WHERE id=%s", (c["job"],)
            ).fetchone() == ("succeeded", 2, None)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "blocker", ["membership", "admission", "viewer", "deleted", "version", "attempts", "connection"]
)
def test_claim_rechecks_current_authorization_and_bounds(corpus, blocker):
    from leases_0005 import claim_job
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    c = corpus
    job = c["job"]
    with psycopg.connect(URL) as admin:
        if blocker == "membership":
            admin.execute(
                "DELETE FROM workspace_members WHERE workspace_id=%s AND user_id=%s",
                (c["ws"], c["owner"]),
            )
        elif blocker == "admission":
            admin.execute("DELETE FROM app_admissions WHERE user_id=%s", (c["owner"],))
        elif blocker == "viewer":
            admin.execute(
                "UPDATE workspace_members SET role='viewer' WHERE workspace_id=%s AND user_id=%s",
                (c["ws"], c["owner"]),
            )
        elif blocker == "deleted":
            admin.execute("UPDATE documents SET deleted_at=now() WHERE id=%s", (c["doc"],))
        elif blocker == "version":
            version = uuid4()
            admin.execute(
                "UPDATE documents SET current_version_id=%s WHERE id=%s", (version, c["doc"])
            )
            insert_version(admin, c["ws"], c["note"], c["doc"], version, 2)
        elif blocker == "attempts":
            admin.execute("UPDATE ingestion_jobs SET attempts=5 WHERE id=%s", (job,))
        else:
            connection = add_connection(admin, c["owner"], status="inactive")
            job = insert_job(
                admin, c["ws"], c["note"], c["version"], c["owner"], connection, c["profile"]
            )
    engine = create_engine(
        URL.replace("postgresql://", "postgresql+psycopg://"), connect_args={"user": "notebook_app"}
    )
    try:
        with Session(engine) as session:
            session.execute(
                text("SELECT set_config('request.jwt.claim.sub',:uid,true)"),
                {"uid": str(c["owner"])},
            )
            assert claim_job(session, job) is None
    finally:
        engine.dispose()


def test_publication_transaction_rolls_back_expired_lease(corpus):
    from app.db.models import Chunk
    from leases_0005 import claim_job, finish_job, lock_job_for_publish
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    c = corpus
    engine = create_engine(
        URL.replace("postgresql://", "postgresql+psycopg://"), connect_args={"user": "notebook_app"}
    )
    try:
        with Session(engine) as session:
            session.execute(
                text("SELECT set_config('request.jwt.claim.sub',:uid,true)"),
                {"uid": str(c["owner"])},
            )
            token = claim_job(session, c["job"])
            assert token is not None
            session.commit()
            session.execute(
                text("SELECT set_config('request.jwt.claim.sub',:uid,true)"),
                {"uid": str(c["owner"])},
            )
            assert not lock_job_for_publish(session, c["job"], uuid4())
            assert lock_job_for_publish(session, c["job"], token)
            chunk = Chunk(
                document_version_id=c["version"],
                document_id=c["doc"],
                notebook_id=c["note"],
                workspace_id=c["ws"],
                ordinal=1,
                text="world",
                sha256="c" * 64,
                start_offset=5,
                end_offset=10,
            )
            session.add(chunk)
            session.flush()
            # Deterministic expiry, not a slow/flaky sleep or a wall-clock mock.
            session.execute(
                text(
                    "UPDATE ingestion_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"
                ),
                {"id": c["job"]},
            )
            assert not finish_job(session, c["job"], token, status="succeeded")
            session.rollback()
        with runtime(c["owner"]) as db:
            assert db.execute("SELECT ordinal FROM chunks ORDER BY ordinal").fetchall() == [(0,)]
    finally:
        engine.dispose()


def test_uniqueness_and_pairing_constraints_even_without_rls(corpus):
    c = corpus
    with psycopg.connect(URL) as db:
        with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
            insert_job(db, c["ws"], c["note"], c["version"], c["owner"], uuid4())
        with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
            insert_job(db, c["ws"], c["note"], c["version"], c["owner"], profile=c["profile"])
        with pytest.raises(psycopg.errors.UniqueViolation), db.transaction():
            insert_chunk(db, c["ws"], c["note"], c["doc"], c["version"])
        with pytest.raises(psycopg.errors.UniqueViolation), db.transaction():
            insert_job(db, c["ws"], c["note"], c["version"], c["owner"], key=str(c["job"]))
