"""Quota head: direct runtime SQL, not privileged quota assertions."""

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_uploads import app, reserve

pytest_plugins = ["test_uploads"]


def test_actor_notebook_capacity_rolls_back_statement(scope):
    actor, _, _, _, _, notebook = scope
    for n in range(10):
        with app(actor) as db:
            reserve(db, notebook, idempotency_key=f"quota-{n}")
    with pytest.raises(DBAPIError) as caught, app(actor) as db:
        reserve(db, notebook, idempotency_key="eleventh")
    assert isinstance(caught.value.orig, psycopg.Error)
    assert caught.value.orig.sqlstate == "P0001"
    assert caught.value.orig.diag.message_primary == "quota_capacity"
    with app(actor) as db:
        assert (
            db.execute(
                text("SELECT count(*) FROM upload_intents WHERE notebook_id=:n"), {"n": notebook}
            ).scalar_one()
            == 10
        )
        assert reserve(db, notebook, idempotency_key="quota-0").id


def test_classifier_matches_only_exact_driver_errors():
    from app.db.quota import classify_quota_error

    assert classify_quota_error(ValueError("quota_capacity")) is None
    assert classify_quota_error(psycopg.errors.RaiseException("quota_capacity")) is None
