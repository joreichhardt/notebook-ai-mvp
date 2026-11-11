import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

URL = os.getenv("NOTEBOOK_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL URL not configured")


def actor(uid, email="recipient@example.test"):
    db = psycopg.connect(URL)
    db.execute("SET ROLE notebook_app")
    db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(uid),))
    db.execute("SELECT set_config('request.jwt.claim.email',%s,true)", (email,))
    return db


@pytest.fixture
def world():
    owner, editor, ws = uuid4(), uuid4(), uuid4()
    with psycopg.connect(URL) as db:
        for uid in (owner, editor):
            db.execute("INSERT INTO app_admissions VALUES(%s)", (uid,))
        db.execute("INSERT INTO workspaces(id,name) VALUES(%s,%s)", (ws, "Invite Test"))
        db.execute(
            "INSERT INTO workspace_members VALUES(%s,%s,%s),(%s,%s,%s)",
            (ws, owner, "owner", ws, editor, "editor"),
        )
    return owner, editor, ws


def invite(owner, ws, email="recipient@example.test"):
    digest = uuid4().hex + uuid4().hex
    with actor(owner) as db:
        db.execute(
            "SELECT notebook_create_invite(%s,%s,%s,%s,%s)", (uuid4(), digest, email, ws, "viewer")
        )
    return digest


def accept(uid, digest, email="recipient@example.test"):
    with actor(uid, email) as db:
        return db.execute("SELECT * FROM notebook_accept_invite(%s)", (digest,)).fetchone()


def test_accept_one_time_personal_workspace_and_roles(world):
    owner, _editor, ws = world
    digest = invite(owner, ws)
    uid = uuid4()
    first = accept(uid, digest)
    assert first[1] == ws and first[0] != ws
    assert accept(uid, digest) == first
    with psycopg.connect(URL) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM workspaces WHERE personal_owner_id=%s", (uid,)
            ).fetchone()[0]
            == 1
        )
        assert (
            db.execute(
                "SELECT role FROM workspace_members WHERE workspace_id=%s AND user_id=%s", (ws, uid)
            ).fetchone()[0]
            == "viewer"
        )
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        accept(uuid4(), digest)


@pytest.mark.parametrize("reason", ["email", "expired", "revoked", "inviter_lost_owner"])
def test_invalid_invite_does_not_admit(world, reason):
    owner, _editor, ws = world
    digest = invite(owner, ws)
    uid = uuid4()
    with psycopg.connect(URL) as db:
        if reason == "expired":
            db.execute(
                "UPDATE invitations SET expires_at=now()-interval '1 minute' WHERE token_hash=%s",
                (digest,),
            )
        if reason == "revoked":
            db.execute("UPDATE invitations SET revoked_at=now() WHERE token_hash=%s", (digest,))
        if reason == "inviter_lost_owner":
            db.execute(
                "UPDATE workspace_members SET role='editor' WHERE workspace_id=%s AND user_id=%s",
                (ws, owner),
            )
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        accept(uid, digest, "wrong@example.test" if reason == "email" else "recipient@example.test")
    with psycopg.connect(URL) as db:
        assert not db.execute("SELECT 1 FROM app_admissions WHERE user_id=%s", (uid,)).fetchone()


def test_only_owner_invites_and_last_owner_protected(world):
    owner, editor, ws = world
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        invite(editor, ws)
    with actor(owner) as db:
        with pytest.raises(psycopg.errors.InvalidParameterValue), db.transaction():
            db.execute("SELECT notebook_change_member(%s,%s,NULL)", (ws, owner))
        db.execute("SELECT notebook_change_member(%s,%s,'owner')", (ws, editor))
    digest = invite(owner, ws)
    with actor(editor) as db:
        db.execute("SELECT notebook_change_member(%s,%s,'viewer')", (ws, owner))
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        accept(uuid4(), digest)
    with actor(owner) as db, pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("SELECT * FROM notebook_members(%s)", (ws,))


def test_parallel_accept_same_identity_is_idempotent(world):
    owner, _, ws = world
    digest = invite(owner, ws)
    uid = uuid4()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: accept(uid, digest), range(2)))
    assert results[0] == results[1]


def test_connection_rls_denies_foreign_owner(world):
    owner, editor, _ = world
    cid = uuid4()
    with actor(owner) as db:
        db.execute(
            "INSERT INTO user_provider_connections(id,user_id,provider_id,label,secret_id) VALUES(%s,%s,%s,%s,%s)",
            (cid, owner, "test", "Private", uuid4()),
        )
    with actor(editor) as db:
        assert not db.execute(
            "SELECT * FROM user_provider_connections WHERE id=%s", (cid,)
        ).fetchall()
        assert not db.execute(
            "DELETE FROM user_provider_connections WHERE id=%s RETURNING id", (cid,)
        ).fetchall()
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
            db.execute(
                "INSERT INTO user_provider_connections(id,user_id,provider_id,label,secret_id) VALUES(%s,%s,%s,%s,%s)",
                (uuid4(), owner, "test", "Forged", uuid4()),
            )


def test_rpc_not_public_and_role_not_inherited():
    with psycopg.connect(URL) as db:
        row = db.execute(
            "SELECT proconfig,proacl::text FROM pg_proc WHERE proname='notebook_accept_invite'"
        ).fetchone()
        assert "search_path=pg_catalog" in row[0]
        assert "=X/" not in row[1].replace("notebook_membership=X/", "").replace(
            "notebook_app=X/", ""
        )
        assert not db.execute(
            "SELECT pg_has_role('notebook_app','notebook_membership','MEMBER')"
        ).fetchone()[0]


def test_app_invites_require_platform_admin(world):
    owner, _, _ = world
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        invite(owner, None)
    with psycopg.connect(URL) as db:
        db.execute("INSERT INTO platform_admins VALUES(%s)", (owner,))
    try:
        digest = invite(owner, None)
        uid = uuid4()
        personal, target = accept(uid, digest)
        assert personal and target is None
    finally:
        with psycopg.connect(URL) as db:
            db.execute("DELETE FROM platform_admins WHERE user_id=%s", (owner,))


def test_bootstrap_verified_account_and_once():
    from app.auth.bootstrap import bootstrap

    uid = uuid4()
    email = f"{uid}@example.test"
    sqlalchemy_url = URL.replace("postgresql://", "postgresql+psycopg://")
    # Minimal Supabase-owned table fixture only in the explicitly configured disposable DB.
    with psycopg.connect(URL) as db:
        db.execute("CREATE SCHEMA IF NOT EXISTS auth")
        db.execute(
            "CREATE TABLE IF NOT EXISTS auth.users (id uuid PRIMARY KEY, email text, email_confirmed_at timestamptz)"
        )
        db.execute("INSERT INTO auth.users VALUES(%s,%s,NULL)", (uid, email))
    try:
        with pytest.raises(ValueError):
            bootstrap(email, sqlalchemy_url)
        with psycopg.connect(URL) as db:
            db.execute("UPDATE auth.users SET email_confirmed_at=now() WHERE id=%s", (uid,))
        assert bootstrap(email, sqlalchemy_url) == uid
        with pytest.raises(ValueError):
            bootstrap(email, sqlalchemy_url)
        with psycopg.connect(URL) as db:
            assert db.execute("SELECT 1 FROM platform_admins WHERE user_id=%s", (uid,)).fetchone()
            assert (
                db.execute(
                    "SELECT count(*) FROM workspaces WHERE personal_owner_id=%s", (uid,)
                ).fetchone()[0]
                == 1
            )
    finally:
        with psycopg.connect(URL) as db:
            db.execute("DELETE FROM platform_admins WHERE user_id=%s", (uid,))
            db.execute("DELETE FROM auth.users WHERE id=%s", (uid,))
