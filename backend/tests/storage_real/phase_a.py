"""Explicit opt-in, disposable real Supabase PG17 Phase A; never a production tool."""

import contextlib
import json
import os
import secrets
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import ClientCursor, sql
from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool

if __package__:
    from .runner_diagnostics import RunnerTrace, run_reported
else:
    from runner_diagnostics import RunnerTrace, run_reported

SECRET_DIR = Path("/phase-a-secrets")
ACTORS = ("anon", "authenticated", "service_role")
MIGRATOR = "a5_phase_a_migrator"
IO_LOGIN = "a5_phase_a_io"
EXPECTED_EXEC = {
    "notebook_create_invite": {"notebook_app"},
    "notebook_accept_invite": {"notebook_app"},
    "notebook_members": {"notebook_app"},
    "notebook_change_member": {"notebook_app"},
    "notebook_delete_document": {"notebook_app"},
    "claim_upload_io": {"notebook_storage_io"},
    "confirm_upload_io": {"notebook_storage_io"},
    "finalize_upload": {"notebook_app"},
    "list_cleanup": {"notebook_storage_io"},
    "claim_cleanup": {"notebook_storage_io"},
    "finish_cleanup": {"notebook_storage_io"},
    "quota_gate": {
        "notebook_app",
        "notebook_membership",
        "notebook_document_lifecycle",
        "notebook_upload_finalize",
        "notebook_upload_io_owner",
    },
}


def passfile(role, password):
    path = SECRET_DIR / f"{role}.pgpass"
    with open(path, "x", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        stream.write(f"db:5432:postgres:{role}:{password}\n")
    assert path.stat().st_mode & 0o777 == 0o600
    return path


class Result:
    def __init__(self, cursor):
        self.cursor = cursor

    def fetchone(self):
        row = self.cursor.fetchone()
        assert row is not None, "expected_database_row"
        return row

    def fetchall(self):
        return self.cursor.fetchall()

    def __iter__(self):
        return iter(self.cursor)


class Database:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=None):
        return Result(self.connection.execute(statement, parameters))


@contextlib.contextmanager
def connect(role, path):
    with psycopg.connect(
        host="db",
        dbname="postgres",
        user=role,
        passfile=str(path),
        connect_timeout=3,
        autocommit=True,
        cursor_factory=ClientCursor,
        options="-c statement_timeout=3000 -c lock_timeout=1000",
    ) as connection:
        yield Database(connection)


def role_password(db, role, password):
    # ClientCursor renders DDL safely; no SQL/exception values are logged.
    db.execute(sql.SQL("ALTER ROLE {} PASSWORD %s").format(sql.Identifier(role)), (password,))


def role_scope(db, role):
    return db.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))


def catalog(db):
    tables = db.execute(
        "SELECT oid,relname,relkind FROM pg_class WHERE relnamespace='public'::regnamespace "
        "AND relkind IN ('r','p','S')"
    ).fetchall()
    functions = db.execute(
        "SELECT oid,proname FROM pg_proc WHERE pronamespace='public'::regnamespace"
    ).fetchall()
    return tables, functions


def deny_select(db, role, table):
    role_scope(db, role)
    try:
        try:
            db.execute(sql.SQL("SELECT * FROM public.{} LIMIT 0").format(sql.Identifier(table)))
        except psycopg.errors.InsufficientPrivilege:
            return
        raise AssertionError("unexpected_select_permission")
    finally:
        db.execute("RESET ROLE")


def deny_helper(db, role):
    role_scope(db, role)
    try:
        try:
            db.execute("SELECT public.quota_gate()")
        except psycopg.errors.InsufficientPrivilege:
            return
        raise AssertionError("unexpected_helper_permission")
    finally:
        db.execute("RESET ROLE")


def check_runtime(role, path, assumed=None):
    engine = create_engine(
        f"postgresql+psycopg://{role}@db:5432/postgres",
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        hide_parameters=True,
        connect_args={
            "passfile": str(path),
            "connect_timeout": 3,
            "options": "-c statement_timeout=3000 -c lock_timeout=1000",
        },
    )
    try:
        with engine.begin() as db:
            if assumed:
                # Fixed role literal, not request input.
                db.exec_driver_sql("SET LOCAL ROLE notebook_storage_io")
            actual = db.exec_driver_sql("SELECT session_user,current_user").one()
            assert actual == (role, assumed or role)
            assert db.exec_driver_sql("SHOW statement_timeout").scalar_one() == "3s"
            assert db.exec_driver_sql("SHOW lock_timeout").scalar_one() == "1s"
            assert db.exec_driver_sql(
                "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=session_user"
            ).scalar_one()
    finally:
        engine.dispose()


def verify_phase_a(trace: RunnerTrace | None = None):
    trace = trace if trace is not None else RunnerTrace()
    trace.enter("preflight")
    assert os.environ.get("A5_STORAGE_PHASE_A") == "1"
    assert os.environ.get("A5_STORAGE_PHASE_A_URL") == "postgresql://db:5432/postgres"
    trace.enter("bootstrap_credentials")
    paths = []
    admin_path = passfile("supabase_admin", (SECRET_DIR / "bootstrap").read_text().strip())
    paths.append(admin_path)
    try:
        trace.enter("native_catalog")
        with connect("supabase_admin", admin_path) as db:
            assert db.execute("SHOW server_version_num").fetchone()[0].startswith("17")
            assert db.execute(
                "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
            ).fetchone()[0]
            assert db.execute("SELECT to_regprocedure('auth.uid()') IS NOT NULL").fetchone()[0]
            assert db.execute("SELECT to_regclass('auth.users') IS NOT NULL").fetchone()[0]
            assert db.execute("SELECT to_regnamespace('storage') IS NOT NULL").fetchone()[0]
            # Native dependency regression: never fabricate this role/schema in tests.
            assert db.execute(
                "SELECT EXISTS (SELECT FROM pg_roles WHERE rolname='supabase_functions_admin') "
                "AND EXISTS (SELECT FROM pg_extension WHERE extname='pg_net') "
                "AND to_regprocedure('supabase_functions.http_request()') IS NOT NULL"
            ).fetchone()[0], "native_webhook_dependency_missing"
            print("NATIVE_WEBHOOK_DEPENDENCY_PRESENT=1", flush=True)
            trace.enter("upstream_probe")
            # Genuine upstream-default exposure, only on disposable synthetic probe.
            role_scope(db, "postgres")
            db.execute("CREATE TABLE public.a5_upstream_acl_probe (value integer)")
            db.execute("INSERT INTO public.a5_upstream_acl_probe VALUES (42)")
            db.execute("RESET ROLE")
            for role in ACTORS:
                role_scope(db, role)
                assert db.execute("SELECT value FROM public.a5_upstream_acl_probe").fetchone() == (
                    42,
                )
                db.execute("RESET ROLE")
            print("RED_UPSTREAM_DEFAULTS_EXPOSE_SYNTHETIC_PROBE=3", flush=True)
            db.execute("DROP TABLE public.a5_upstream_acl_probe")
            trace.enter("migration_owner")
            before_tables, before_funcs = catalog(db)
            db.execute("CREATE ROLE a5_phase_a_migrator LOGIN SUPERUSER")
            # Defaults belong solely to this fresh role; no upstream defaults changed.
            db.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE a5_phase_a_migrator "
                "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
            )
            password = secrets.token_hex(32)
            role_password(db, MIGRATOR, password)
            migration_path = passfile(MIGRATOR, password)
            paths.append(migration_path)
            del password
            os.environ["NOTEBOOK_MIGRATION_DATABASE_URL"] = (
                f"postgresql+psycopg://{MIGRATOR}@db:5432/postgres"
                f"?passfile={migration_path}&connect_timeout=3"
            )
            trace.enter("alembic_upgrade")
            try:
                with (
                    open(os.devnull, "w") as sink,
                    contextlib.redirect_stdout(sink),
                    contextlib.redirect_stderr(sink),
                ):
                    command.upgrade(Config("backend/migrations/alembic.ini"), "0006")
            finally:
                with trace.cleanup("migrator_disable"):
                    os.environ.pop("NOTEBOOK_MIGRATION_DATABASE_URL", None)
                    migration_path.unlink(missing_ok=True)
                    db.execute(
                        "ALTER ROLE a5_phase_a_migrator NOLOGIN NOSUPERUSER NOCREATEROLE "
                        "NOCREATEDB NOREPLICATION NOBYPASSRLS PASSWORD NULL"
                    )
            trace.enter("migration_verify")
            assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0006",)
            flags = db.execute(
                "SELECT rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls "
                "FROM pg_roles WHERE rolname=%s",
                (MIGRATOR,),
            ).fetchone()
            assert flags == (False,) * 6
            assert db.execute(
                "SELECT rolpassword IS NULL FROM pg_authid WHERE rolname=%s", (MIGRATOR,)
            ).fetchone()[0]
            assert (
                db.execute(
                    "SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles "
                    "WHERE rolname=%s) OR roleid=(SELECT oid FROM pg_roles WHERE rolname=%s)",
                    (MIGRATOR, MIGRATOR),
                ).fetchone()[0]
                == 0
            )
            trace.enter("runtime_logins")
            db.execute(
                sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT").format(
                    sql.Identifier(IO_LOGIN)
                )
            )
            db.execute(sql.SQL("GRANT notebook_storage_io TO {}").format(sql.Identifier(IO_LOGIN)))
            for role in ("notebook_app", IO_LOGIN):
                password = secrets.token_hex(32)
                role_password(db, role, password)
                path = passfile(role, password)
                paths.append(path)
                del password
                check_runtime(role, path, "notebook_storage_io" if role == IO_LOGIN else None)
            trace.enter("app_catalog")
            tables, functions = catalog(db)
            old_tables = {r[0] for r in before_tables}
            old_funcs = {r[0] for r in before_funcs}
            tables = [r for r in tables if r[0] not in old_tables]
            functions = [r for r in functions if r[0] not in old_funcs]
            assert len(tables) >= 10 and functions
            protected = [
                r[0]
                for r in db.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname LIKE 'notebook_%' OR rolname=%s",
                    (MIGRATOR,),
                )
            ]
            trace.enter("actor_denials")
            for role in ACTORS:
                deny_helper(db, role)
                for target in protected:
                    assert not db.execute(
                        "SELECT pg_has_role(%s,%s,'MEMBER') OR pg_has_role(%s,%s,'SET')",
                        (role, target, role, target),
                    ).fetchone()[0]
                for oid, name, kind in tables:
                    if kind == "S":
                        assert not db.execute(
                            "SELECT has_sequence_privilege(%s,%s,'USAGE,SELECT,UPDATE')",
                            (role, oid),
                        ).fetchone()[0]
                    else:
                        assert not db.execute(
                            "SELECT has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,"
                            "TRUNCATE,REFERENCES,TRIGGER') OR "
                            "has_any_column_privilege(%s,%s,'SELECT,INSERT,UPDATE,REFERENCES')",
                            (role, oid, role, oid),
                        ).fetchone()[0]
                        deny_select(db, role, name)
                for oid, _ in functions:
                    assert not db.execute(
                        "SELECT has_function_privilege(%s,%s,'EXECUTE')", (role, oid)
                    ).fetchone()[0]
            trace.enter("helper_acl")
            for oid, name in functions:
                # Explicit non-owner execution ACLs must exactly match contract.
                grants = db.execute(
                    "SELECT CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) "
                    "END FROM pg_proc p, LATERAL aclexplode(coalesce(p.proacl,"
                    "acldefault('f',p.proowner))) x WHERE p.oid=%s "
                    "AND x.privilege_type='EXECUTE' AND x.grantee<>p.proowner",
                    (oid,),
                ).fetchall()
                assert {r[0] for r in grants} == EXPECTED_EXEC.get(name, set())
            trace.enter("runtime_memberships")
            for role in (IO_LOGIN, "notebook_app"):
                for target in protected + [
                    "supabase_storage_admin",
                    "authenticator",
                    "service_role",
                ]:
                    allowed = target == role or (
                        role == IO_LOGIN and target == "notebook_storage_io"
                    )
                    assert (
                        db.execute("SELECT pg_has_role(%s,%s,'SET')", (role, target)).fetchone()[0]
                        == allowed
                    )
            trace.enter("storage_graph")
            graph = db.execute(
                "SELECT r.rolname FROM pg_roles r WHERE "
                "pg_has_role('supabase_storage_admin',r.oid,'MEMBER') ORDER BY r.rolname"
            ).fetchall()
            assert {r[0] for r in graph} == {
                "supabase_storage_admin",
                "authenticator",
                "anon",
                "authenticated",
                "service_role",
            }
            assert db.execute(
                "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles "
                "WHERE rolname='supabase_storage_admin'"
            ).fetchone()[0]
            print("STORAGE_ADMIN_ROLE_GRAPH=" + json.dumps([r[0] for r in graph]), flush=True)
            print(
                f"GREEN_ACL_RELATIONS={len(tables)} FUNCTIONS={len(functions)} ACTORS=3", flush=True
            )
            print("MIGRATOR_DISABLED_AND_CREDENTIAL_REMOVED=1", flush=True)
    finally:
        with trace.cleanup("credential_cleanup"):
            for path in paths:
                path.unlink(missing_ok=True)
    trace.enter("complete")


if __name__ == "__main__":
    raise SystemExit(run_reported(verify_phase_a))
