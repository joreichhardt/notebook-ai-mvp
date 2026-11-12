"""Gated one-shot dispatch: real canonical0006/SCRAM; synthetic originals only."""

import asyncio
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from app.auth.supabase import Identity
from app.workers.text import TextWorker
from sqlalchemy import text
from test_service import URL, perform

pytest_plugins = ["test_service"]
pytestmark = pytest.mark.skipif(not URL, reason="isolated PostgreSQL required")


def dispatcher(c, *, actor_tx=None, worker=None):
    from app.workers.dispatch import SelectionFactory, dispatch_one

    @contextmanager
    def selection(identity):
        with (actor_tx or c["actor_tx"])(identity.id) as db:
            yield db

    factory = SelectionFactory(selection, pool_timeout=1, connect_timeout=3)
    worker = worker or TextWorker(c["actor_engine"], store_factory=lambda auth: c["store"])
    return lambda who: dispatch_one(
        Identity(who, "synthetic@example.invalid"), worker=worker, selection=factory
    )


def test_dispatch_lost_wakeup_queued_only_and_no_io_in_selection(upload_scene):
    c = upload_scene
    receipt = perform(c)
    c["store"].actions.clear()
    # Recreate the helper after finalization: no transient wakeup is necessary.
    assert asyncio.run(dispatcher(c)(c["actor"])) == "succeeded"
    assert c["store"].actions == ["read"] and not c["active"]
    assert asyncio.run(dispatcher(c)(c["actor"])) == "idle"
    with c["actor_tx"](c["actor"]) as db:
        assert db.execute(
            text("SELECT status,attempts,lease_token FROM ingestion_jobs WHERE id=:j"),
            {"j": receipt.job.id},
        ).one() == ("succeeded", 1, None)


@pytest.mark.parametrize("who", ["editor", "viewer", "unadmitted"])
def test_dispatch_never_discovers_foreign_actor_jobs(upload_scene, who):
    c = upload_scene
    perform(c)
    c["store"].actions.clear()
    from app.workers.dispatch import DispatchUnavailable

    actor = uuid4() if who == "unadmitted" else c[who]
    if who == "unadmitted":
        with pytest.raises(DispatchUnavailable, match="admission_required"):
            asyncio.run(dispatcher(c)(actor))
    else:
        assert asyncio.run(dispatcher(c)(actor)) == "idle"
    assert c["store"].actions == []


def test_dispatch_revocation_between_selection_and_worker_rechecks(upload_scene):
    c = upload_scene
    receipt = perform(c)
    calls = []

    class RevokingWorker(TextWorker):
        async def run_one(self, *, actor_id, job_id):
            assert not c["active"]
            calls.append(job_id)
            with psycopg.connect(URL) as setup:
                setup.execute("DELETE FROM workspace_members WHERE user_id=%s", (actor_id,))
            return await super().run_one(actor_id=actor_id, job_id=job_id)

    worker = RevokingWorker(c["actor_engine"], store_factory=lambda auth: c["store"])
    c["store"].actions.clear()
    assert asyncio.run(dispatcher(c, worker=worker)(c["actor"])) == "not_claimed"
    assert calls == [receipt.job.id] and c["store"].actions == []


def test_dispatch_duplicate_selection_has_one_claim_and_one_read(upload_scene):
    c = upload_scene
    perform(c)
    c["store"].actions.clear()

    async def run():
        both = asyncio.Event()
        selected = []

        class PairedWorker(TextWorker):
            async def run_one(self, *, actor_id, job_id):
                assert not c["active"]
                selected.append(job_id)
                if len(selected) == 2:
                    both.set()
                await both.wait()
                return await super().run_one(actor_id=actor_id, job_id=job_id)

        worker = PairedWorker(c["actor_engine"], store_factory=lambda auth: c["store"])
        one = dispatcher(c, worker=worker)
        results = await asyncio.wait_for(asyncio.gather(one(c["actor"]), one(c["actor"])), 5)
        assert sorted(results) == ["not_claimed", "succeeded"]
        assert selected[0] == selected[1]

    asyncio.run(run())
    assert c["store"].actions == ["read"]


@pytest.mark.parametrize("expired", [False, True])
def test_dispatch_never_reclaims_running_rows_even_after_expiry(upload_scene, expired):
    from time import sleep

    c = upload_scene
    receipt = perform(c)
    worker = TextWorker(c["actor_engine"], lease_seconds=1, heartbeat_seconds=0.1)
    assert worker._claim(c["actor"], receipt.job.id)
    if expired:
        sleep(1.05)
    c["store"].actions.clear()
    assert asyncio.run(dispatcher(c)(c["actor"])) == "idle"
    assert c["store"].actions == []
    with c["actor_tx"](c["actor"]) as db:
        assert db.execute(
            text("SELECT status,attempts FROM ingestion_jobs WHERE id=:j"), {"j": receipt.job.id}
        ).one() == ("running", 1)


def test_dispatch_principal_subject_and_factory_contract_fail_closed(upload_scene):
    from app.workers.dispatch import DispatchUnavailable, SelectionFactory

    c = upload_scene
    perform(c)
    c["store"].actions.clear()
    with pytest.raises(ValueError):
        SelectionFactory(lambda _: None, pool_timeout=float("inf"), connect_timeout=3)
    with pytest.raises(ValueError):
        SelectionFactory(lambda _: None, pool_timeout=1, connect_timeout=0)

    @contextmanager
    def wrong_subject(actor):
        with c["actor_tx"](c["editor"]) as db:
            yield db

    with pytest.raises(DispatchUnavailable, match="unsafe_actor_context"):
        asyncio.run(dispatcher(c, actor_tx=wrong_subject)(c["actor"]))

    @contextmanager
    def wrong_role(actor):
        with c["io_tx"]() as db:
            yield db

    with pytest.raises(DispatchUnavailable, match="unsafe_database_role"):
        asyncio.run(dispatcher(c, actor_tx=wrong_role)(c["actor"]))
    assert c["store"].actions == []
