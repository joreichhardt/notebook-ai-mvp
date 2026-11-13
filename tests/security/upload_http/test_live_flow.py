"""Opt-in Stage B root-ASGI TXT/MD proof, NOT production enablement.

Only Main's reviewed immutable combined A5/B5 packet may invoke the live test.
No bootstrap, grants, migrations, daemon, cleanup, or remote execution here.
The external Supabase user response alone is synthetic; Storage HTTP is real.
"""

import asyncio
import logging
import os
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from app.auth.security import CSRF_COOKIE, SESSION_COOKIE
from app.auth.settings import Settings
from app.auth.supabase import Identity, SupabaseAuth
from app.db import session as session_layer
from app.documents import router as reader
from app.documents import upload_router as writer
from app.documents.storage import ObjectScope, StorageConfig, SupabaseStorage
from app.main import app
from app.workers.dispatch import SelectionFactory, dispatch_one
from app.workers.text import TextWorker
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session


class PacketRejected(RuntimeError):
    """Fixed packet diagnostics only; never attach source exceptions or values."""


def require(condition, label):
    if not condition:
        raise PacketRejected(label)


@dataclass(frozen=True)
class PacketConfig:
    passfiles: dict[str, Path]
    token_path: Path
    endpoint: str = "http://gateway:10000/storage/v1"
    bucket: str = "a5-private"


def database_url(role):
    require(role in ("notebook_app", "a5_phase_a_io", "supabase_admin"), "b5_role_required")
    return URL.create(
        "postgresql+psycopg", username=role, host="db", port=5432, database="postgres"
    )


def make_engine(role, passfile):
    return create_engine(
        database_url(role),
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


def private_file(root, name):
    path = root / name
    mode = path.lstat().st_mode
    require(
        not path.is_symlink() and stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o600,
        "b5_private_file_required",
    )
    return path


def configuration(environment=None, *, root=Path("/stage-b-secrets")):
    environment = os.environ if environment is None else environment
    require(
        environment.get("B5_UPLOAD_HTTP") == "isolated-stage-b-approved",
        "b5_opt_in_required",
    )
    require(
        not any(name.startswith(("PG", "NOTEBOOK_")) for name in environment)
        and not any(
            name in environment
            for name in ("DATABASE_URL", "SERVICE_KEY", "ANON_KEY", "AUTH_JWT_SECRET")
        ),
        "b5_inherited_configuration",
    )
    mode = root.lstat().st_mode
    require(
        root.is_absolute()
        and not any(path.is_symlink() for path in (root, *root.parents))
        and stat.S_ISDIR(mode)
        and stat.S_IMODE(mode) == 0o700,
        "b5_private_directory_required",
    )
    passfiles = {
        role: private_file(root, role + ".pgpass")
        for role in ("notebook_app", "a5_phase_a_io", "supabase_admin")
    }
    return PacketConfig(passfiles, private_file(root, "service.jwt"))


def opaque(operation):
    """Fail after a normal callback returns, not contextlib's exception throw path."""
    failed = False
    try:
        operation()
    except Exception:  # noqa: BLE001 — this is the packet's source-safe diagnostic boundary
        failed = True
    if failed:
        pytest.fail("b5_live_flow_failed", pytrace=False)


def stage(name):
    require(
        name
        in ("configuration", "principals", "setup", "closed", "denials", "owner", "editor", "done"),
        "b5_unknown_stage",
    )
    print("B5_HTTP_STAGE=" + name, flush=True)


class Body(httpx.AsyncByteStream):
    def __init__(self, filename, media, data):
        self.reads = 0
        self.value = (
            b'--b5-boundary\r\nContent-Disposition: form-data; name="file"; filename="'
            + filename.encode("ascii")
            + b'"\r\nContent-Type: '
            + media.encode("ascii")
            + b"\r\n\r\n"
            + data
            + b"\r\n--b5-boundary--\r\n"
        )

    async def __aiter__(self):
        self.reads += 1
        yield self.value


def principal(db, role):
    row = db.execute(
        text(
            "SELECT current_user,session_user,rolsuper,rolbypassrls,rolinherit "
            "FROM pg_roles WHERE rolname=session_user"
        )
    ).one()
    require(row[0] == role and row[1] == role, "b5_direct_login_required")
    if role != "supabase_admin":
        require(row[2] is False and row[3] is False, "b5_restricted_login_required")
    else:
        require(row[2] is True, "b5_assigned_setup_principal_required")
    if role == "a5_phase_a_io":
        require(row[4] is False, "b5_io_noinherit_required")


def inventory(admin_engine, workspace, bucket):
    # Privileged inventory only, NEVER evidence of application/Storage authorization.
    with admin_engine.begin() as db:
        return (
            db.execute(
                text(
                    "SELECT name FROM storage.objects WHERE bucket_id=:bucket AND name LIKE :prefix ORDER BY name"
                ),
                {"bucket": bucket, "prefix": str(workspace) + "/%"},
            )
            .scalars()
            .all()
        )


def committed(identity, receipt, ids, key, filename, media, data, *, ready=False):
    """Fresh actual notebook_app SELECTs; no privileged shortcuts or mirror fields."""
    with contextmanager(session_layer.authenticated_session)(identity) as db:
        params = {"actor": identity.id, "notebook": ids["notebook"], "key": key}
        row = db.execute(
            text(
                "SELECT i.document_id,i.version_id,i.job_id,i.storage_key,i.state,"
                "i.sha256,i.byte_count,i.stored_at IS NOT NULL,"
                "v.sha256,v.byte_count,v.media_type,v.processing_status,"
                "d.current_version_id,j.status,j.attempts,j.connection_id,j.index_profile_id "
                "FROM upload_intents i JOIN documents d ON d.id=i.document_id "
                "JOIN document_versions v ON v.id=i.version_id "
                "JOIN ingestion_jobs j ON j.id=i.job_id "
                "WHERE i.actor_user_id=:actor AND i.notebook_id=:notebook AND i.idempotency_key=:key"
            ),
            params,
        ).one()
        document, version, job = (
            UUID(receipt["document"]["id"]),
            UUID(receipt["document"]["version_id"]),
            UUID(receipt["job"]["id"]),
        )
        scope = ObjectScope(ids["workspace"], ids["notebook"], document, version)
        require(tuple(row[:3]) == (document, version, job), "b5_canonical_receipt_mismatch")
        require(row[3] == scope.key and row[4] == "finalized", "b5_canonical_original_mismatch")
        require(
            tuple(row[5:11])
            == (
                sha256(data).hexdigest(),
                len(data),
                True,
                sha256(data).hexdigest(),
                len(data),
                media,
            ),
            "b5_committed_integrity_mismatch",
        )
        require(
            row[12] == version and tuple(row[15:]) == (None, None), "b5_current_text_only_required"
        )
        require(
            (row[13], row[14]) == (("succeeded", 1) if ready else ("queued", 0)),
            "b5_canonical_job_state_mismatch",
        )
        require(receipt["document"]["filename"] == filename, "b5_filename_mismatch")
        if ready:
            require(row[11] == "ready", "b5_ready_required")
            parts = (
                db.execute(
                    text(
                        "SELECT text FROM chunks WHERE document_version_id=:version ORDER BY ordinal"
                    ),
                    {"version": version},
                )
                .scalars()
                .all()
            )
            require(parts == [data.decode("utf-8")], "b5_atomic_chunks_mismatch")
        count = db.execute(
            text(
                "SELECT count(*) FROM ingestion_jobs WHERE actor_user_id=:actor AND notebook_id=:notebook AND idempotency_key=:key"
            ),
            params,
        ).scalar_one()
        require(count == 1, "b5_duplicate_job")
        return scope


async def exercise(monkeypatch, config):
    ids = {
        name: uuid4()
        for name in ("owner", "editor", "viewer", "foreign", "unadmitted", "workspace", "notebook")
    }
    verified: dict[str, Identity] = {}
    actions = []
    with ExitStack() as stack:
        engines = {}
        for role, passfile in config.passfiles.items():
            engine = make_engine(role, passfile)
            engines[role] = engine
            stack.callback(engine.dispose)
        app_engine, io_engine, admin_engine = (
            engines[name] for name in ("notebook_app", "a5_phase_a_io", "supabase_admin")
        )
        stage("principals")
        for role, engine in engines.items():
            with engine.begin() as db:
                principal(db, role)
        with admin_engine.begin() as db:
            require(
                db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0006",
                "b5_migrated_database_required",
            )
        stage("setup")
        # No auth.users, role/grant/migration mutation; only this packet's random IDs.
        with admin_engine.begin() as db:
            db.execute(
                text(
                    "INSERT INTO app_admissions(user_id) VALUES (:owner),(:editor),(:viewer),(:foreign)"
                ),
                ids,
            )
            db.execute(
                text("INSERT INTO workspaces(id,name) VALUES (:workspace,'B5 synthetic HTTP')"), ids
            )
            db.execute(
                text(
                    "INSERT INTO workspace_members(workspace_id,user_id,role) VALUES (:workspace,:owner,'owner'),(:workspace,:editor,'editor'),(:workspace,:viewer,'viewer'),(:workspace,:unadmitted,'editor')"
                ),
                ids,
            )
            db.execute(
                text(
                    "INSERT INTO notebooks(id,workspace_id,title) VALUES (:notebook,:workspace,'B5 synthetic HTTP')"
                ),
                ids,
            )
        monkeypatch.setattr(session_layer, "engine", lambda: app_engine)
        original_identity = SupabaseAuth.identity

        async def user_response(self, method, path, token="", data=None):
            require(method == "GET" and path == "user" and data is None, "b5_auth_request_mismatch")
            if token not in ("owner", "editor", "viewer", "foreign", "unadmitted"):
                raise HTTPException(401, {"code": "invalid_credentials"})
            return {
                "id": str(ids[token]),
                "email": "b5@example.test",
                "email_confirmed_at": "synthetic",
            }

        async def capture_identity(self, token):
            user = await original_identity(self, token)
            require(type(user) is Identity, "b5_verified_identity_required")
            verified[token] = (
                user  # EXACT object returned by existing verifier, not an actor issuer.
            )
            return user

        monkeypatch.setattr(SupabaseAuth, "request", user_response)
        monkeypatch.setattr(SupabaseAuth, "identity", capture_identity)
        # Explicit server configuration, never ambient NOTEBOOK_* secret-file loading.
        settings = Settings(
            public_origin="https://b5.example.test",
            supabase_url="",
            supabase_anon_key=SecretStr(""),
            database_url=SecretStr(""),
            database_url_file="",
            supabase_anon_key_file="",
        )
        monkeypatch.setattr(writer, "get_settings", lambda: settings)
        monkeypatch.setattr(session_layer, "get_settings", lambda: settings)
        perform = SupabaseStorage._perform

        async def observe_storage(self, actor, scope, action, data=None, media_type="text/plain"):
            actions.append(action)  # Fixed operations ONLY, no token/key/HTTP body capture.
            return await perform(self, actor, scope, action, data, media_type)

        monkeypatch.setattr(SupabaseStorage, "_perform", observe_storage)
        require(not app.dependency_overrides, "b5_actual_root_dependencies_required")
        path = f"/api/notebooks/{ids['notebook']}/documents"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="https://b5.example.test",
            trust_env=False,
        ) as client:

            async def post(
                actor,
                key="b5-negative",
                data=b"synthetic",
                filename="synthetic.txt",
                media="text/plain",
                csrf=True,
            ):
                body = Body(filename, media, data)
                headers = {
                    "content-type": "multipart/form-data; boundary=b5-boundary",
                    "idempotency-key": key,
                    "origin": "https://b5.example.test",
                    "cookie": f"{SESSION_COOKIE}={actor}; {CSRF_COOKIE}=synthetic-csrf",
                }
                if csrf:
                    headers["x-csrf-token"] = "synthetic-csrf"
                response = await client.post(path, headers=headers, content=body)
                return response, body.reads

            async def get(actor):
                return await client.get(path, headers={"cookie": f"{SESSION_COOKIE}={actor}"})

            stage("closed")
            response, reads = await post("owner")
            require(response.status_code == 404 and reads == 0, "b5_root_post_not_closed")
            require((await get("owner")).status_code == 404, "b5_root_get_not_closed")
            require(not verified and not actions, "b5_closed_boundary_crossed")
            # TEST ONLY activation. Actual capabilities() and storage_io() remain unchanged.
            monkeypatch.setattr(writer, "capabilities", lambda: {"documents": True})
            monkeypatch.setattr(reader, "capabilities", lambda: {"documents": True})
            storage = StorageConfig(
                config.endpoint,
                config.bucket,
                SecretStr(config.token_path.read_text().strip()),
                allow_internal_http=True,
            )

            @contextmanager
            def io_tx():
                with Session(io_engine) as db, db.begin():
                    principal(db, "a5_phase_a_io")
                    db.execute(text("SET LOCAL ROLE notebook_storage_io"))
                    require(
                        db.execute(text("SELECT current_user,session_user")).one()
                        == ("notebook_storage_io", "a5_phase_a_io"),
                        "b5_io_role_mismatch",
                    )
                    yield db

            monkeypatch.setattr(writer, "storage_io", lambda: writer.StorageIo(io_tx, storage))
            stage("denials")
            for actor, csrf, status, code in (
                ("viewer", True, 403, "write_required"),
                ("foreign", True, 404, "notebook_not_found"),
                ("unadmitted", True, 403, "admission_required"),
                ("owner", False, 403, "csrf_denied"),
                ("invalid", True, 401, "invalid_credentials"),
            ):
                response, reads = await post(actor, csrf=csrf)
                require(response.status_code == status and reads == 0, "b5_prebody_denial_failed")
                public_error = response.json()
                require(
                    set(public_error) == {"detail"}
                    and set(public_error["detail"]) == {"code", "message"}
                    and public_error["detail"]["code"] == code,
                    "b5_fixed_error_contract_mismatch",
                )
                require(
                    not actions and not inventory(admin_engine, ids["workspace"], config.bucket),
                    "b5_denial_touched_storage",
                )
            with contextmanager(session_layer.authenticated_session)(verified["owner"]) as db:
                require(
                    db.execute(
                        text("SELECT count(*) FROM upload_intents WHERE notebook_id=:notebook"), ids
                    ).scalar_one()
                    == 0,
                    "b5_denial_reserved_upload",
                )
            for actor, filename, media, data in (
                ("owner", "synthetic.txt", "text/plain", b"Stage B synthetic TXT.\n"),
                ("editor", "synthetic.md", "text/markdown", b"# Stage B\nSynthetic Markdown.\n"),
            ):
                stage(actor)
                key = "b5-" + actor
                before_actions = len(actions)
                before_objects = inventory(admin_engine, ids["workspace"], config.bucket)
                response, reads = await post(actor, key, data, filename, media)
                require(response.status_code == 202 and reads == 1, "b5_upload_not_accepted")
                receipt = response.json()
                require(
                    set(receipt) == {"document", "job"} and receipt["job"]["status"] == "queued",
                    "b5_unsafe_receipt",
                )
                require(
                    set(receipt["document"])
                    == {"id", "notebook_id", "filename", "version_id", "status", "error_code"}
                    and set(receipt["job"]) == {"id", "status"},
                    "b5_unsafe_receipt_fields",
                )
                identity = verified[actor]
                scope = committed(identity, receipt, ids, key, filename, media, data)
                objects = inventory(admin_engine, ids["workspace"], config.bucket)
                require(
                    set(objects) == {*before_objects, scope.key}
                    and len(objects) == len(before_objects) + 1,
                    "b5_duplicate_original",
                )
                require(actions[before_actions:] == ["write", "read"], "b5_upload_io_mismatch")
                after_actions = len(actions)
                replay, _ = await post(actor, key, data, filename, media)
                require(
                    replay.status_code == 202 and replay.json() == receipt, "b5_replay_mismatch"
                )
                conflict, _ = await post(actor, key, data + b"changed", filename, media)
                require(conflict.status_code == 409, "b5_conflict_not_rejected")
                require(
                    len(actions) == after_actions
                    and inventory(admin_engine, ids["workspace"], config.bucket) == objects,
                    "b5_replay_repeated_io",
                )
                committed(identity, receipt, ids, key, filename, media, data)
                # Explicit trusted one-shot; captured original verified object, no public UUID issuer.
                result = await dispatch_one(
                    identity,
                    worker=TextWorker(app_engine, storage=storage),
                    selection=SelectionFactory(
                        contextmanager(session_layer.authenticated_session), 1, 3
                    ),
                )
                require(result == "succeeded", "b5_one_shot_did_not_succeed")
                require(actions[after_actions:] == ["read"], "b5_worker_not_actual_storage")
                committed(identity, receipt, ids, key, filename, media, data, ready=True)
                member = await get("viewer")
                require(member.status_code == 200, "b5_member_get_failed")
                payload = member.json()
                require(set(payload) == {"items"}, "b5_member_wrapper_unsafe")
                require(
                    all(set(item) == set(receipt["document"]) for item in payload["items"]),
                    "b5_private_fields_in_member_get",
                )
                expected = {**receipt["document"], "status": "ready", "error_code": None}
                require(expected in payload["items"], "b5_current_ready_source_missing")
                require(
                    (await get("foreign")).status_code == 404
                    and (await get("unadmitted")).status_code == 403,
                    "b5_reader_scope_failed",
                )
        # Intentionally retain own synthetic rows/objects for A5 packet teardown.
        # No DELETE/foreign cleanup and no second bootstrap or credential generation.
        stage("done")


@pytest.mark.skipif(
    os.environ.get("B5_UPLOAD_HTTP") != "isolated-stage-b-approved",
    reason="B5 Main-reviewed serialized isolated Stage B opt-in absent",
)
def test_real_root_http_upload_to_ready(monkeypatch):
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)

    async def bounded(config):
        async with asyncio.timeout(180):
            await exercise(monkeypatch, config)

    def run():
        stage("configuration")
        config = configuration()
        asyncio.run(bounded(config))

    try:
        opaque(run)
    finally:
        logging.disable(previous)
