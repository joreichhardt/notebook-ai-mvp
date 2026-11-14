"""Packet-only PostgreSQL concurrency gate; synthetic Store, not supervisor proof."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import psycopg
import pytest
from app.workers.delegation import BoundedThreadRunner, DelegationRepository, SchedulerFactory
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.tests.documents.test_service import URL, perform

pytest_plugins = ["test_delegation_database"]
pytestmark = pytest.mark.skipif(not URL, reason="dedicated kube1 cluster required")


def test_competing_direct_schedulers_issue_one_live_delivery(delegation_scene, tmp_path):
    c = delegation_scene
    receipt = perform(c)
    barrier = Barrier(2, timeout=5)
    principals = []

    def claim():
        # Separate loops, thread runners and direct DB sessions, not serial duplicate calls.
        # Independent pool per contender, matching fixture principal/passfile.
        engine = create_engine(
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

        @contextmanager
        def transaction():
            with Session(engine) as db, db.begin():
                principals.append(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
                barrier.wait()  # both independent direct sessions have an open transaction
                yield db

        async def one():
            runner = BoundedThreadRunner()
            repo = DelegationRepository(SchedulerFactory(transaction, 1, 3), runner)
            try:
                return await repo.claim()
            finally:
                assert await runner.close()

        try:
            return asyncio.run(one())
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        grants = [future.result(timeout=12) for future in futures]
    assert len(principals) == 2 and len(set(principals)) == 2
    live = [grant for grant in grants if grant is not None]
    assert len(live) == 1 and live[0].job_id == receipt.job.id
    with psycopg.connect(URL) as db:
        assert db.execute(
            "SELECT delivery_token FROM text_job_tickets WHERE job_id=%s", (receipt.job.id,)
        ).fetchone() == (live[0].delivery_token,)
    assert asyncio.run(c["repo"].validate(live[0])) == "executable"
