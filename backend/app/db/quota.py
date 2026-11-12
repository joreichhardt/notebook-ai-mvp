"""Fixed quota gate and exact PostgreSQL error classification.

Call only after connection/verified-actor validation, in a short READ COMMITTED
transaction. On busy, roll back the WHOLE transaction before bounded retry.
Never retry external I/O; a worker yields without consuming an attempt. HTTP
owners map capacity to 429 and exhausted busy retry to 503, not every P0001.
"""

from typing import Literal

import psycopg
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

QuotaError = Literal[
    "quota_capacity",
    "quota_gate_busy",
    "quota_legacy_unsupported",
    "quota_preflight_required",
    "quota_isolation_unsupported",
]

_ERRORS: dict[tuple[str, str], QuotaError] = {
    ("P0001", "quota_capacity"): "quota_capacity",
    ("55P03", "quota_gate_busy"): "quota_gate_busy",
    ("P0001", "quota_legacy_unsupported"): "quota_legacy_unsupported",
    ("P0001", "quota_preflight_required"): "quota_preflight_required",
    ("P0001", "quota_isolation_unsupported"): "quota_isolation_unsupported",
}


def classify_quota_error(error: BaseException) -> QuotaError | None:
    """Recognize exact server SQLSTATE/primary message, never SQL/raw exception text."""
    driver = error.orig if isinstance(error, DBAPIError) else error
    if not isinstance(driver, psycopg.Error):
        return None
    state, message = driver.sqlstate, driver.diag.message_primary
    if state is None or message is None:
        return None
    return _ERRORS.get((state, message))


def quota_gate(session: Session) -> None:
    """First bookkeeping statement; no commit, actor issuance, retry or external I/O."""
    session.execute(text("SELECT public.quota_gate()"))
