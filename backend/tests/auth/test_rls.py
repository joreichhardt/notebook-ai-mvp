"""Run only against an explicitly supplied isolated migrated PostgreSQL database."""

import os
from uuid import uuid4

import psycopg
import pytest

URL = os.getenv("NOTEBOOK_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Isolated PostgreSQL URL not configured")


def test_rls_isolation_admission_roles_and_context():
    owner, viewer, outsider, unadmitted = [uuid4() for _ in range(4)]
    ws, other, note = [uuid4() for _ in range(3)]
    with psycopg.connect(URL) as admin:
        admin.execute("INSERT INTO app_admissions VALUES (%s),(%s),(%s)", (owner, viewer, outsider))
        admin.execute(
            "INSERT INTO workspaces (id,name) VALUES (%s,%s),(%s,%s)", (ws, "A", other, "B")
        )
        for uid, role in [(owner, "owner"), (viewer, "viewer"), (unadmitted, "owner")]:
            admin.execute("INSERT INTO workspace_members VALUES (%s,%s,%s)", (ws, uid, role))
        admin.execute("INSERT INTO workspace_members VALUES (%s,%s,%s)", (other, outsider, "owner"))
        admin.execute("INSERT INTO notebooks VALUES (%s,%s,%s)", (note, ws, "Private A"))
    try:
        with psycopg.connect(URL) as db:
            db.execute("SET ROLE notebook_app")
            assert db.execute("SELECT * FROM notebooks").fetchall() == []
            for uid, count in [(owner, 1), (viewer, 1), (outsider, 0), (unadmitted, 0)]:
                db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(uid),))
                assert len(db.execute("SELECT * FROM notebooks").fetchall()) == count
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(viewer),))
            assert (
                db.execute(
                    "UPDATE notebooks SET title='forbidden' WHERE id=%s RETURNING id", (note,)
                ).fetchall()
                == []
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
                db.execute("INSERT INTO notebooks VALUES (%s,%s,%s)", (uuid4(), ws, "forbidden"))
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(owner),))
            assert (
                db.execute(
                    "UPDATE notebooks SET title='allowed' WHERE id=%s RETURNING id", (note,)
                ).fetchone()[0]
                == note
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
                db.execute("INSERT INTO app_admissions VALUES (%s)", (uuid4(),))
            db.commit()
            assert db.execute("SELECT * FROM notebooks").fetchall() == []
    finally:
        with psycopg.connect(URL) as admin:
            admin.execute("DELETE FROM workspaces WHERE id IN (%s,%s)", (ws, other))
            admin.execute(
                "DELETE FROM app_admissions WHERE user_id IN (%s,%s,%s)", (owner, viewer, outsider)
            )
