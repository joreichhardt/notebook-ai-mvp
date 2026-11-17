"""Run only against the assigned isolated, migrated PostgreSQL test cluster."""
import os
from uuid import uuid4

import psycopg
import pytest

URL = os.getenv("NOTEBOOK_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL URL not configured")


def actor(uid):
    db = psycopg.connect(URL)
    db.execute("SET ROLE notebook_app")
    db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(uid),))
    return db


@pytest.fixture
def people():
    superadmin, admin, member, target = (uuid4() for _ in range(4))
    with psycopg.connect(URL) as db:
        for uid in (superadmin, admin, member, target):
            db.execute("INSERT INTO app_admissions(user_id) VALUES(%s)", (uid,))
        db.execute("INSERT INTO platform_admins(user_id,role) VALUES(%s,'superadmin')", (superadmin,))
        db.execute("INSERT INTO platform_admins(user_id) VALUES(%s)", (admin,))
    return superadmin, admin, member, target


@pytest.mark.parametrize("actor_index", [1, 2])
def test_admin_and_regular_user_cannot_grant_or_list(people, actor_index):
    _, _, _, target = people
    with actor(people[actor_index]) as db, pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("SELECT * FROM notebook_grant_app_admin(%s)", (target,))
    with actor(people[actor_index]) as db, pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("SELECT * FROM notebook_platform_administrators()")
    with psycopg.connect(URL) as db:
        assert db.execute("SELECT role FROM platform_admins WHERE user_id=%s", (target,)).fetchone() is None


def test_only_superadmin_grants_admin_and_grant_is_idempotent(people):
    superadmin, _, _, target = people
    for _ in range(2):
        with actor(superadmin) as db:
            assert db.execute("SELECT * FROM notebook_grant_app_admin(%s)", (target,)).fetchone() == (target, "admin")
    with actor(target) as db, pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.execute("SELECT * FROM notebook_grant_app_admin(%s)", (target,))


def test_unknown_user_cannot_be_admitted_by_admin_grant(people):
    with actor(people[0]) as db, pytest.raises(psycopg.errors.InvalidParameterValue):
        db.execute("SELECT * FROM notebook_grant_app_admin(%s)", (uuid4(),))


def test_existing_superadmin_is_never_demoted_by_admin_grant(people):
    with actor(people[0]) as db:
        assert db.execute("SELECT * FROM notebook_grant_app_admin(%s)", (people[0],)).fetchone() == (people[0], "superadmin")


def test_runtime_cannot_assign_itself_superadmin_directly(people):
    for uid in people[:3]:
        with actor(uid) as db, pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("UPDATE platform_admins SET role='superadmin' WHERE user_id=%s", (uid,))
        with actor(uid) as db, pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("INSERT INTO platform_admins(user_id,role) VALUES(%s,'superadmin')", (people[3],))


def test_default_existing_admin_is_not_a_superadmin_and_direct_read_is_self_only(people):
    with actor(people[1]) as db:
        assert db.execute("SELECT user_id,role FROM platform_admins").fetchall() == [(people[1], "admin")]
    with actor(people[2]) as db:
        assert db.execute("SELECT user_id,role FROM platform_admins").fetchall() == []


def test_function_authority_has_no_runtime_membership_or_role_update_privilege():
    with psycopg.connect(URL) as db:
        assert db.execute("SELECT pg_has_role('notebook_app','notebook_platform_authority','MEMBER')").fetchone() == (False,)
        assert db.execute("SELECT has_column_privilege('notebook_platform_authority','public.platform_admins','role','UPDATE')").fetchone() == (False,)
        assert db.execute("SELECT has_column_privilege('notebook_platform_authority','public.platform_admins','user_id','UPDATE')").fetchone() == (True,)
