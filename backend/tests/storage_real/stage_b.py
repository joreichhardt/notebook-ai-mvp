"""Explicit isolated Stage B preparation/verification, never production wiring."""

import asyncio
import contextlib
import json
import os
import secrets
import sys
import time
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from app.db.uploads import claim_cleanup, finish_cleanup, list_cleanup
from app.documents.service import DocumentError, UploadService
from app.documents.storage import ObjectScope, StorageConfig
from app.ingestion.text import TextUpload
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

if __package__:
    from . import phase_a
    from .runner_diagnostics import RunnerTrace
    from .stage_b_contract import delete_claimed, request
else:
    import phase_a
    from runner_diagnostics import RunnerTrace
    from stage_b_contract import delete_claimed, request

ROOT = Path("/stage-b-secrets")
CASES = (
    "prepare",
    "post_storage_acl",
    "private_http",
    "owner_upload",
    "editor_upload",
    "idempotency",
    "application_denials",
    "jwt_denials",
    "cleanup",
    "complete",
)
ACL_CHECKPOINTS = (
    "catalog_relation_ids",
    "catalog_function_ids",
    "relation_owners",
    "function_owners",
    "actor_owner_membership",
    "actor_helper_denial",
    "actor_sequence_denial",
    "actor_table_denial",
    "actor_select_denial",
    "actor_function_denial",
    "helper_exact_grants",
    "storage_membership_graph",
    "storage_role_flags",
    "storage_table_owner_rls",
    "storage_migration_count",
    "storage_migration_fingerprint",
    "storage_schema_owner",
    "migrator_disabled_flags",
    "migrator_password_null",
    "migrator_membership_none",
    "runtime_set_graph",
)
CURRENT = "prepare"
BUCKET = "a5-private"
ENDPOINT = "http://gateway:10000/storage/v1"


def case(name):
    global CURRENT
    assert name in CASES
    CURRENT = name
    print("STAGE_B_BEGIN=" + name, flush=True)


def passed():
    assert CURRENT in CASES
    print("STAGE_B_CASE=" + CURRENT, flush=True)


def prepare():
    phase_a.SECRET_DIR = ROOT
    phase_a.verify_phase_a()
    admin = phase_a.passfile("supabase_admin", (ROOT / "bootstrap").read_text())
    try:
        with phase_a.connect("supabase_admin", admin) as db:
            for role in ("notebook_app", phase_a.IO_LOGIN):
                password = secrets.token_hex(32)
                phase_a.role_password(db, role, password)
                phase_a.passfile(role, password)
            phase_a.role_password(
                db, "supabase_storage_admin", (ROOT / "owner-password").read_text()
            )
            fixture = json.loads((ROOT / "fixture.json").read_text())
            for name in ("owner", "editor", "viewer", "foreign", "revoked"):
                db.execute(
                    "INSERT INTO public.app_admissions(user_id) VALUES (%s)", (UUID(fixture[name]),)
                )
            for workspace, notebook, members in (
                (
                    "workspace",
                    "notebook",
                    (
                        ("owner", "owner"),
                        ("editor", "editor"),
                        ("viewer", "viewer"),
                        ("revoked", "editor"),
                    ),
                ),
                ("foreign_workspace", "foreign_notebook", (("foreign", "owner"),)),
            ):
                db.execute(
                    "INSERT INTO public.workspaces(id,name) VALUES (%s,'StageB synthetic')",
                    (UUID(fixture[workspace]),),
                )
                db.execute(
                    "INSERT INTO public.notebooks(id,workspace_id,title) VALUES (%s,%s,'StageB synthetic')",
                    (UUID(fixture[notebook]), UUID(fixture[workspace])),
                )
                for user, role in members:
                    db.execute(
                        "INSERT INTO public.workspace_members(workspace_id,user_id,role) VALUES (%s,%s,%s)",
                        (UUID(fixture[workspace]), UUID(fixture[user]), role),
                    )
            owners = [
                r[0]
                for r in db.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname LIKE 'notebook_%%' OR rolname=%s",
                    (phase_a.MIGRATOR,),
                )
            ]
            tables = db.execute(
                "SELECT oid,relname,relkind,pg_get_userbyid(relowner) FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind IN ('r','p','S') AND pg_get_userbyid(relowner)=ANY(%s)",
                (owners,),
            ).fetchall()
            functions = db.execute(
                "SELECT oid,proname,pg_get_userbyid(proowner) FROM pg_proc WHERE pronamespace='public'::regnamespace AND pg_get_userbyid(proowner)=ANY(%s)",
                (owners,),
            ).fetchall()
            assert len(tables) == 14 and len(functions) == 26
            with (ROOT / "catalog.json").open("x") as stream:
                json.dump({"tables": tables, "functions": functions, "owners": owners}, stream)
    finally:
        (ROOT / "bootstrap").unlink(missing_ok=True)
        (ROOT / "owner-password").unlink(missing_ok=True)
    passed()


def acl_checkpoint(name):
    assert type(name) is str and name in ACL_CHECKPOINTS
    print("STAGE_B_ACL_CHECKPOINT=" + name, flush=True)


def migration_inventory(rows, source):
    def fail(status):
        print(
            "STAGE_B_MIGRATION_INVENTORY=" + json.dumps({"status": status}, separators=(",", ":")),
            flush=True,
        )
        raise AssertionError

    if type(rows) is list and not rows:
        fail("empty")
    expected_count = source.get("migration_count") if type(source) is dict else None
    expected_fingerprint = (
        source.get("migration_ids_names_sha256") if type(source) is dict else None
    )
    if not (
        type(rows) is list
        and 1 <= len(rows) <= 1000
        and type(expected_count) is int
        and 0 <= expected_count <= 1000
        and type(expected_fingerprint) is str
        and len(expected_fingerprint) == 64
        and all(char in "0123456789abcdef" for char in expected_fingerprint)
        and all(
            type(row) in (tuple, list)
            and len(row) == 2
            and type(row[0]) is int
            and 0 <= row[0] <= 10000
            and type(row[1]) is str
            and len(row[1]) <= 512
            for row in rows
        )
    ):
        fail("malformed")
    pairs = sorted([[row[0], row[1]] for row in rows])
    ids = [pair[0] for pair in pairs]
    print(
        "STAGE_B_MIGRATION_INVENTORY="
        + json.dumps(
            {
                "status": "observed",
                "actual_count": len(rows),
                "expected_count": expected_count,
                "min_id": min(ids),
                "max_id": max(ids),
                "actual_fingerprint": sha256(
                    json.dumps(pairs, separators=(",", ":")).encode()
                ).hexdigest(),
                "expected_fingerprint": expected_fingerprint,
            },
            separators=(",", ":"),
        ),
        flush=True,
    )


def post_storage_acl():
    catalog = json.loads((ROOT / "catalog.json").read_text())
    source = json.loads(Path(__file__).with_name("stage_b_source.json").read_text())
    with phase_a.connect("supabase_admin", ROOT / "supabase_admin.pgpass") as db:
        acl_checkpoint("catalog_relation_ids")
        assert (
            db.execute(
                "SELECT count(*) FROM pg_class WHERE oid=ANY(%s)",
                ([r[0] for r in catalog["tables"]],),
            ).fetchone()[0]
            == 14
        )
        acl_checkpoint("catalog_function_ids")
        assert (
            db.execute(
                "SELECT count(*) FROM pg_proc WHERE oid=ANY(%s)",
                ([r[0] for r in catalog["functions"]],),
            ).fetchone()[0]
            == 26
        )
        for oid, _, _, owner in catalog["tables"]:
            acl_checkpoint("relation_owners")
            assert (
                db.execute(
                    "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=%s", (oid,)
                ).fetchone()[0]
                == owner
            )
        for oid, _, owner in catalog["functions"]:
            acl_checkpoint("function_owners")
            assert (
                db.execute(
                    "SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=%s", (oid,)
                ).fetchone()[0]
                == owner
            )
        for role in phase_a.ACTORS:
            for protected in catalog["owners"]:
                acl_checkpoint("actor_owner_membership")
                assert not db.execute(
                    "SELECT pg_has_role(%s,%s,'MEMBER') OR pg_has_role(%s,%s,'SET')",
                    (role, protected, role, protected),
                ).fetchone()[0]
            acl_checkpoint("actor_helper_denial")
            phase_a.deny_helper(db, role)
            for oid, name, kind, _ in catalog["tables"]:
                if kind == "S":
                    acl_checkpoint("actor_sequence_denial")
                    assert not db.execute(
                        "SELECT has_sequence_privilege(%s,%s,'USAGE,SELECT,UPDATE')", (role, oid)
                    ).fetchone()[0]
                else:
                    acl_checkpoint("actor_table_denial")
                    assert not db.execute(
                        "SELECT has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR has_any_column_privilege(%s,%s,'SELECT,INSERT,UPDATE,REFERENCES')",
                        (role, oid, role, oid),
                    ).fetchone()[0]
                    acl_checkpoint("actor_select_denial")
                    phase_a.deny_select(db, role, name)
            for oid, _, _ in catalog["functions"]:
                acl_checkpoint("actor_function_denial")
                assert not db.execute(
                    "SELECT has_function_privilege(%s,%s,'EXECUTE')", (role, oid)
                ).fetchone()[0]
        for oid, name, _ in catalog["functions"]:
            acl_checkpoint("helper_exact_grants")
            grants = db.execute(
                "SELECT CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END FROM pg_proc p,LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x WHERE p.oid=%s AND x.privilege_type='EXECUTE' AND x.grantee<>p.proowner",
                (oid,),
            ).fetchall()
            assert {r[0] for r in grants} == phase_a.EXPECTED_EXEC.get(name, set())
        acl_checkpoint("storage_membership_graph")
        graph = {
            r[0]
            for r in db.execute(
                "SELECT rolname FROM pg_roles WHERE pg_has_role('supabase_storage_admin',oid,'MEMBER')"
            )
        }
        assert graph == {
            "supabase_storage_admin",
            "authenticator",
            "anon",
            "authenticated",
            "service_role",
        }
        acl_checkpoint("storage_role_flags")
        flags = db.execute(
            "SELECT rolcanlogin,rolinherit,rolsuper,rolbypassrls,rolcreaterole FROM pg_roles WHERE rolname='supabase_storage_admin'"
        ).fetchone()
        assert flags == (True, False, False, False, True)
        for name in ("buckets", "objects"):
            acl_checkpoint("storage_table_owner_rls")
            assert db.execute(
                "SELECT pg_get_userbyid(relowner),relrowsecurity FROM pg_class WHERE oid=to_regclass(%s)",
                ("storage." + name,),
            ).fetchone() == ("supabase_storage_admin", True)
        migrations = db.execute("SELECT id,name FROM storage.migrations ORDER BY id").fetchall()
        fingerprint = sha256(json.dumps(migrations, separators=(",", ":")).encode()).hexdigest()
        migration_inventory(migrations, source)
        acl_checkpoint("storage_migration_count")
        assert len(migrations) == source["migration_count"]
        acl_checkpoint("storage_migration_fingerprint")
        assert fingerprint == source["migration_ids_names_sha256"]
        acl_checkpoint("storage_schema_owner")
        schema_owner = db.execute(
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='storage'"
        ).fetchone()[0]
        assert schema_owner == "supabase_admin"
        acl_checkpoint("migrator_disabled_flags")
        assert (
            db.execute(
                "SELECT rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s",
                (phase_a.MIGRATOR,),
            ).fetchone()
            == (False,) * 6
        )
        acl_checkpoint("migrator_password_null")
        assert db.execute(
            "SELECT rolpassword IS NULL FROM pg_authid WHERE rolname=%s", (phase_a.MIGRATOR,)
        ).fetchone()[0]
        acl_checkpoint("migrator_membership_none")
        assert (
            db.execute(
                "SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s) OR roleid=(SELECT oid FROM pg_roles WHERE rolname=%s)",
                (phase_a.MIGRATOR, phase_a.MIGRATOR),
            ).fetchone()[0]
            == 0
        )
        for principal in ("notebook_app", phase_a.IO_LOGIN):
            for target in catalog["owners"] + [
                "supabase_storage_admin",
                "authenticator",
                "service_role",
            ]:
                allowed = principal == target or (
                    principal == phase_a.IO_LOGIN and target == "notebook_storage_io"
                )
                acl_checkpoint("runtime_set_graph")
                assert (
                    db.execute("SELECT pg_has_role(%s,%s,'SET')", (principal, target)).fetchone()[0]
                    == allowed
                )
    print(
        "STAGE_B_INVENTORY="
        + json.dumps(
            {
                "storage_table_owner": "supabase_storage_admin",
                "storage_schema_owner": schema_owner,
                "tables_rls": True,
                "owner_flags": list(flags),
                "migration_count": len(migrations),
                "migration_fingerprint": fingerprint,
                "app_relations": 14,
                "app_functions": 26,
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    passed()


def engine(role):
    return create_engine(
        f"postgresql+psycopg://{role}@db:5432/postgres",
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        hide_parameters=True,
        connect_args={
            "passfile": str(ROOT / f"{role}.pgpass"),
            "connect_timeout": 3,
            "options": "-c statement_timeout=3000 -c lock_timeout=1000",
        },
    )


async def verify():
    case("post_storage_acl")
    post_storage_acl()
    f = {k: UUID(v) for k, v in json.loads((ROOT / "fixture.json").read_text()).items()}
    config = StorageConfig(ENDPOINT, BUCKET, SecretStr((ROOT / "service.jwt").read_text()), True)
    app_engine, io_engine = engine("notebook_app"), engine(phase_a.IO_LOGIN)

    @contextlib.contextmanager
    def actor_tx(actor):
        with Session(app_engine) as db, db.begin():
            db.execute(
                text("SELECT set_config('request.jwt.claim.sub',:actor,true)"),
                {"actor": str(actor)},
            )
            yield db

    @contextlib.contextmanager
    def io_tx():
        with Session(io_engine) as db, db.begin():
            db.execute(text("SET LOCAL ROLE notebook_storage_io"))
            assert db.execute(text("SELECT session_user,current_user")).one() == (
                phase_a.IO_LOGIN,
                "notebook_storage_io",
            )
            yield db

    service = UploadService(actor_tx=actor_tx, io_tx=io_tx, storage=config)
    # Observe the existing default factory, without replacing real HTTP or authorization.
    default_factory = service.store_factory
    constructed = []

    def observe_factory(authorize):
        store = default_factory(authorize)
        constructed.append(True)
        return store

    service.store_factory = observe_factory
    headers = {"authorization": "Bearer " + config.token.get_secret_value()}
    scopes = []
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10, connect=3), trust_env=False, follow_redirects=False
        ) as client:
            case("private_http")
            # Readiness GET only, before any mutation; not a retried acceptance case.
            async with asyncio.timeout(10):
                while True:
                    try:
                        ready = await request(client, "GET", ENDPOINT + "/status")
                        assert ready.status == 200
                        break
                    except httpx.ConnectError:
                        await asyncio.sleep(0.1)
            assert (await request(client, "GET", "http://gateway:10000/not-storage")).status == 404
            result = await request(
                client,
                "POST",
                ENDPOINT + "/bucket",
                headers=headers,
                json={"id": BUCKET, "name": BUCKET, "public": False},
            )
            assert result.status in (200, 201)
            bucket = await request(client, "GET", ENDPOINT + "/bucket/" + BUCKET, headers=headers)
            assert bucket.status == 200 and json.loads(bucket.body)["public"] is False
            passed()
            for name in ("owner", "editor"):
                case(name + "_upload")
                body = ("Stage B synthetic " + name + "\n").encode()
                upload = TextUpload(
                    "synthetic.txt", "text/plain", body, body.decode(), sha256(body).hexdigest()
                )
                receipt = await service.upload(
                    actor_id=f[name], notebook_id=f["notebook"], idempotency_key=name, upload=upload
                )
                scope = ObjectScope(
                    f["workspace"], f["notebook"], receipt.document.id, receipt.document.version_id
                )
                with actor_tx(f[name]) as db:
                    row = db.execute(
                        text(
                            "SELECT i.id,i.sha256,i.byte_count,i.state,i.stored_at IS NOT NULL,d.current_version_id=v.id,j.id,j.status FROM upload_intents i JOIN documents d ON d.id=i.document_id JOIN document_versions v ON v.id=i.version_id JOIN ingestion_jobs j ON j.id=i.job_id WHERE i.document_id=:d AND i.actor_user_id=:a"
                        ),
                        {"d": scope.document_id, "a": f[name]},
                    ).one()
                    assert row[1:] == (
                        upload.sha256,
                        len(body),
                        "finalized",
                        True,
                        True,
                        receipt.job.id,
                        "queued",
                    )
                read = await request(
                    client,
                    "GET",
                    ENDPOINT + "/object/authenticated/" + BUCKET + "/" + scope.key,
                    headers=headers,
                )
                assert (
                    read.status == 200
                    and read.body == body
                    and sha256(read.body).hexdigest() == upload.sha256
                )
                duplicate = await request(
                    client,
                    "POST",
                    ENDPOINT + "/object/" + BUCKET + "/" + scope.key,
                    headers={**headers, "content-type": "text/plain", "x-upsert": "false"},
                    content=b"not original",
                )
                assert duplicate.status in (400, 409)
                assert (
                    await request(
                        client,
                        "GET",
                        ENDPOINT + "/object/authenticated/" + BUCKET + "/" + scope.key,
                        headers=headers,
                    )
                ).body == body
                scopes.append((scope, row[0], f[name], upload, receipt))
                passed()
                case("idempotency")
                assert (
                    await service.upload(
                        actor_id=f[name],
                        notebook_id=f["notebook"],
                        idempotency_key=name,
                        upload=upload,
                    )
                    == receipt
                )
                other = TextUpload(
                    "synthetic.txt",
                    "text/plain",
                    b"changed",
                    "changed",
                    sha256(b"changed").hexdigest(),
                )
                try:
                    await service.upload(
                        actor_id=f[name],
                        notebook_id=f["notebook"],
                        idempotency_key=name,
                        upload=other,
                    )
                except DocumentError as error:
                    assert error.code == "upload_idempotency_conflict"
                else:
                    raise AssertionError("expected_idempotency_conflict")
                with actor_tx(f[name]) as db:
                    assert (
                        db.execute(
                            text("SELECT count(*) FROM upload_intents WHERE actor_user_id=:a"),
                            {"a": f[name]},
                        ).scalar_one()
                        == 1
                    )
                passed()
            case("application_denials")
            assert service.list_documents(actor_id=f["revoked"], notebook_id=f["notebook"])
            with phase_a.connect("supabase_admin", ROOT / "supabase_admin.pgpass") as db:
                db.execute("DELETE FROM app_admissions WHERE user_id=%s", (f["revoked"],))
            before_denials = len(constructed)
            for name in ("viewer", "foreign", "revoked"):
                try:
                    await service.upload(
                        actor_id=f[name],
                        notebook_id=f["notebook"],
                        idempotency_key="denied",
                        upload=scopes[0][3],
                    )
                except DocumentError as error:
                    assert error.status_code == (404 if name == "foreign" else 403)
                else:
                    raise AssertionError("expected_actor_denial")
            assert len(constructed) == before_denials
            with actor_tx(f["foreign"]) as db:
                assert (
                    db.execute(
                        text("SELECT count(*) FROM upload_intents WHERE actor_user_id=:a"),
                        {"a": f["foreign"]},
                    ).scalar_one()
                    == 0
                )
            passed()
            case("jwt_denials")
            scope, _, _, upload, _ = scopes[0]
            for jwt_case, token_name in (
                ("missing", None),
                ("invalid", "invalid.jwt"),
                ("anon", "anon.jwt"),
                ("authenticated_a", "user-a.jwt"),
                ("authenticated_b", "user-b.jwt"),
            ):
                print("STAGE_B_JWT=" + jwt_case, flush=True)
                auth = (
                    {}
                    if token_name is None
                    else {"authorization": "Bearer " + (ROOT / token_name).read_text()}
                )
                target = ENDPOINT + "/object/authenticated/" + BUCKET + "/" + scope.key
                denied = await request(client, "GET", target, headers=auth)
                assert denied.status in (400, 401, 403, 404) and upload.data not in denied.body
                assert (
                    await request(client, "GET", ENDPOINT + "/bucket/" + BUCKET, headers=auth)
                ).status in (400, 401, 403, 404)
                listing = await request(
                    client,
                    "POST",
                    ENDPOINT + "/object/list/" + BUCKET,
                    headers=auth,
                    json={"prefix": "", "limit": 10},
                )
                assert listing.status in (400, 401, 403, 404) or (
                    listing.status == 200 and json.loads(listing.body) == []
                )
                assert (
                    await request(
                        client,
                        "DELETE",
                        ENDPOINT + "/object/" + BUCKET + "/" + scope.key,
                        headers=auth,
                    )
                ).status in (400, 401, 403, 404)
                other = ObjectScope(*(uuid4() for _ in range(4)))
                target = ENDPOINT + "/object/" + BUCKET + "/" + other.key
                assert (
                    await request(
                        client,
                        "POST",
                        target,
                        headers={**auth, "content-type": "text/plain"},
                        content=b"forbidden",
                    )
                ).status in (400, 401, 403, 404)
                assert (
                    await request(
                        client,
                        "GET",
                        ENDPOINT + "/object/authenticated/" + BUCKET + "/" + other.key,
                        headers=headers,
                    )
                ).status == 404
                control = await request(
                    client,
                    "GET",
                    ENDPOINT + "/object/authenticated/" + BUCKET + "/" + scope.key,
                    headers=headers,
                )
                assert control.status == 200 and sha256(control.body).hexdigest() == upload.sha256
            passed()
            case("cleanup")
            with io_tx() as db:
                assert all(claim_cleanup(db, intent) is None for _, intent, _, _, _ in scopes)
            with actor_tx(f["owner"]) as db:
                for scope, _, _, _, _ in scopes:
                    assert db.execute(
                        text("SELECT public.notebook_delete_document(:d)"), {"d": scope.document_id}
                    ).scalar_one()
            # Honor actual 5min safety delay plus original <=120s write lease. No clock/ledger rewrites.
            deadline = time.monotonic() + 435
            remaining = list(scopes)
            while remaining:
                assert time.monotonic() < deadline, "cleanup_due_deadline"
                for entry in remaining[:]:
                    scope, intent, _, _, _ = entry
                    with io_tx() as db:
                        ids = list_cleanup(db)
                        claim = claim_cleanup(db, intent) if intent in ids else None
                    if claim is None:
                        continue
                    with io_tx() as db:
                        assert not finish_cleanup(db, intent, uuid4())
                    await delete_claimed(client, config, claim, scope)
                    with io_tx() as db:
                        assert finish_cleanup(db, intent, claim.token)
                    with io_tx() as db:
                        assert not finish_cleanup(db, intent, claim.token)
                    remaining.remove(entry)
                if remaining:
                    await asyncio.sleep(2)
            passed()
            case("complete")
            passed()
    finally:
        app_engine.dispose()
        io_engine.dispose()


def main():
    os.umask(0o077)
    try:
        assert sys.argv[1:] in (["prepare"], ["verify"])
        if sys.argv[1] == "prepare":
            prepare()
        else:
            asyncio.run(verify())
    except Exception as error:  # noqa: BLE001 -- finite no-raw-output process boundary
        # Reuse finite exact-type/SQLSTATE reporter; never print message/SQL/DSN/traceback.
        metadata = json.loads(RunnerTrace().failure(error))
        metadata["stage"] = CURRENT if CURRENT in CASES else "unknown"
        print("STAGE_B_FAILURE=" + json.dumps(metadata, separators=(",", ":")), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
