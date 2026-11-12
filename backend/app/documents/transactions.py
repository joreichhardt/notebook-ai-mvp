"""Bounded retry of a WHOLE short DB callback, never a statement or external I/O.

The callback owns a fresh session/transaction and exits (commit or rollback and
close) before returning/raising. Three attempts, 250ms retry admission budget,
10/20ms backoff outside the callback. Factories must independently bound pool/
connection acquisition; statement/lock timeouts bound in-flight SQL. This is not
a way to interrupt an arbitrary factory or to retry an ambiguous commit failure.
"""

from collections.abc import Callable
from functools import wraps
from time import monotonic, sleep

import psycopg
from sqlalchemy.exc import SQLAlchemyError

from app.db.quota import classify_quota_error


class QuotaUnavailable(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def short_transaction[T](operation: Callable[[], T]) -> T:
    deadline = monotonic() + 0.25
    for attempt in range(3):
        # Backoff may oversleep (or this thread may be suspended). Check start
        # admission again; never interrupt an already admitted callback.
        if attempt and monotonic() >= deadline:
            raise QuotaUnavailable("quota_gate_busy") from None
        try:
            return operation()
        except (SQLAlchemyError, psycopg.Error) as exc:
            code = classify_quota_error(exc)
            if code is None:
                raise
            if code != "quota_gate_busy" or attempt == 2:
                raise QuotaUnavailable(code) from None
            delay = 0.01 * (attempt + 1)
            if monotonic() + delay >= deadline:
                raise QuotaUnavailable(code) from None
            sleep(delay)  # callback's transaction has already rolled back/closed
    raise AssertionError("unreachable retry state")


def retry_transaction[**P, T](operation: Callable[P, T]) -> Callable[P, T]:
    """For methods whose entire body is one short committing transaction."""

    @wraps(operation)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        return short_transaction(lambda: operation(*args, **kwargs))

    return wrapped
