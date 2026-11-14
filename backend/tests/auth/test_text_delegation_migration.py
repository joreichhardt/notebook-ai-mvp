"""New kube1 snapshot only: 0006->0007 has no historical ticket backfill (UNRUN)."""

import os

import psycopg
import pytest
from database_versions import isolated_database, migrate
from test_quota_migration import canonical_0005, seed_scope, snapshot

URL = os.getenv("NOTEBOOK_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="new isolated kube1 PostgreSQL required")


def test_migration_preserves_old_rows_and_only_new_finalization_issues_ticket():
    with isolated_database(URL, "0006") as url:
        actor, workspace, notebook = seed_scope(url)
        old_intent, _, _, _ = canonical_0005(url, actor, workspace, notebook, "before-0007")
        before = snapshot(url)
        result = migrate(url, "0007")
        assert result.returncode == 0, "0007 migration failed (driver output suppressed)"
        after = snapshot(url)
        assert after[1:] == before[1:]
        assert after[0] == [("0007",)]
        with psycopg.connect(url) as db:
            assert db.execute("SELECT count(*) FROM text_job_tickets").fetchone() == (0,)
        # Replay of an already-finalized upload is deliberately not backfill.
        with psycopg.connect(url, user="notebook_app") as db:
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(actor),))
            assert db.execute("SELECT finalize_upload(%s)", (old_intent,)).fetchone() == (True,)
        _, _, _, new_job = canonical_0005(url, actor, workspace, notebook, "after-0007")
        with psycopg.connect(url) as db:
            assert db.execute(
                "SELECT job_id,actor_user_id,purpose FROM text_job_tickets"
            ).fetchall() == [(new_job, actor, "text_extract_v1")]
