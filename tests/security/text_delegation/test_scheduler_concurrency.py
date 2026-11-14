"""Packet-only direct SQL concurrency gate; final real repository validation.

Not concurrent repository coverage. The separate 23 delegation cases retain that
repository boundary. Synthetic Store only, not supervisor/runtime enablement proof.
"""

import asyncio

import psycopg
import pytest
from concurrency import collect_claims, empty_diagnostic
from sqlalchemy import create_engine

from backend.tests.documents.test_service import URL, perform

pytest_plugins = ["test_delegation_database"]
pytestmark = pytest.mark.skipif(not URL, reason="dedicated kube1 cluster required")


def test_competing_direct_schedulers_issue_one_live_delivery(delegation_scene, tmp_path, request):
    request.node.concurrency_diagnostic = empty_diagnostic()
    c = delegation_scene
    receipt = perform(c)

    def engine_factory(index):
        # Independent pool/connection per contender, direct fixture principal/passfile.
        return create_engine(
            c["scheduler_engine"].url,
            hide_parameters=True,
            pool_size=1,
            max_overflow=0,
            pool_timeout=1,
            creator=lambda: psycopg.connect(
                str(c["scheduler_engine"].url).replace("postgresql+psycopg", "postgresql"),
                passfile=str(tmp_path / "scheduler.pgpass"),
                connect_timeout=3,
                options="-c statement_timeout=3000 -c lock_timeout=1000",
            ),
        )

    def read_fence(job_id):
        with psycopg.connect(URL) as db:
            return db.execute(
                "SELECT delivery_token FROM text_job_tickets WHERE job_id=%s", (job_id,)
            ).fetchone()

    ok, diagnostic = asyncio.run(
        collect_claims(engine_factory, receipt.job.id, read_fence, c["repo"].validate)
    )
    request.node.concurrency_diagnostic = diagnostic
    assert ok, "concurrency_failed"
