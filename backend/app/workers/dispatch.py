"""Gated one-shot discovery for ONE trusted, already verified actor.

Caller must obtain Identity through app.db.session.identity (Supabase verification).
An Identity instance/UUID is NOT authentication by itself. No route, environment
actor, global registry, daemon, running recovery or provider execution is supplied.
Production issuance/wiring remains a separate gate.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from math import isfinite
from typing import Literal
from uuid import UUID

import psycopg
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.supabase import Identity
from app.db.session import authenticated_session
from app.documents.transactions import QuotaUnavailable, short_transaction
from app.workers.text import TextWorker

DispatchResult = Literal["idle", "not_claimed", "yielded", "succeeded", "failed", "lease_lost"]


class DispatchUnavailable(RuntimeError):
    """Opaque internal failure; never carries selected IDs or driver messages."""


@dataclass(frozen=True)
class SelectionFactory:
    """Trusted fresh committing Session factory with explicit acquisition contract.

    Injectors attest finite pool/connect limits no greater than the production
    factory's 1s/3s settings. This checks the declared contract, not the behavior
    of arbitrary Python code; the trusted injector must configure/enforce them.
    Neither these individual budgets nor retry admission promise total wall time
    (DNS/TLS/pre-ping/OS scheduling remain distinct). No I/O in this factory.
    """

    open: Callable[[Identity], AbstractContextManager[Session]]
    pool_timeout: float
    connect_timeout: float

    def __post_init__(self):
        for value, maximum in ((self.pool_timeout, 1), (self.connect_timeout, 3)):
            if type(value) not in (int, float) or not isfinite(value) or not 0 < value <= maximum:
                raise ValueError("bounded_selection_factory_required")
        if not callable(self.open):
            raise TypeError("bounded_selection_factory_required")


# Existing factory performs direct-login/subject binding and commits on exit;
# engine() supplies finite pool/connect and startup statement/lock budgets.
_DEFAULT = SelectionFactory(
    contextmanager(authenticated_session), pool_timeout=1, connect_timeout=3
)

_SELECT = text("""
SELECT j.id FROM public.ingestion_jobs j
JOIN public.upload_intents i ON i.job_id=j.id AND i.version_id=j.document_version_id
 AND i.notebook_id=j.notebook_id AND i.workspace_id=j.workspace_id
 AND i.actor_user_id=j.actor_user_id AND i.idempotency_key=j.idempotency_key
JOIN public.documents d ON d.id=i.document_id AND d.current_version_id=i.version_id
 AND d.notebook_id=i.notebook_id AND d.workspace_id=i.workspace_id AND d.created_by=i.actor_user_id
JOIN public.document_versions v ON v.id=i.version_id AND v.document_id=d.id
 AND v.notebook_id=d.notebook_id AND v.workspace_id=d.workspace_id
JOIN public.workspace_members m ON m.workspace_id=j.workspace_id AND m.user_id=:actor
WHERE j.actor_user_id=:actor AND i.state='finalized'
 AND m.role IN ('owner','editor') AND d.deleted_at IS NULL
 AND j.status='queued' AND j.attempts=0 AND j.lease_token IS NULL AND j.lease_expires_at IS NULL
 AND j.available_at<=clock_timestamp() AND j.connection_id IS NULL AND j.index_profile_id IS NULL
 AND v.media_type IN ('text/plain','text/markdown') AND v.media_type=i.media_type
 AND v.sha256=i.sha256 AND v.byte_count=i.byte_count AND v.storage_key=i.storage_key
ORDER BY j.created_at,j.id LIMIT 1
""")


async def dispatch_one(
    verified_identity: Identity,
    *,
    worker: TextWorker,
    selection: SelectionFactory | None = None,
) -> DispatchResult:
    """Select at most one canonical queued job; commit/close BEFORE one run_one.

    No selected ID is returned. A racing worker/revocation is resolved by the
    existing worker's atomic claim and fresh authorization, not by this SELECT.
    Sticky-cancel metadata stays private: its authoritative claim guard rejects
    reactivation. No loop, token reset, or retry of the worker/Storage invocation.
    """
    if not isinstance(verified_identity, Identity) or not isinstance(verified_identity.id, UUID):
        raise DispatchUnavailable("verified_identity_required")
    factory = selection if selection is not None else _DEFAULT
    if not isinstance(factory, SelectionFactory):
        raise DispatchUnavailable("bounded_selection_factory_required")

    def select_one() -> UUID | None:
        with factory.open(verified_identity) as db:
            safe = db.execute(
                text(
                    "SELECT current_user='notebook_app' AND session_user='notebook_app' "
                    "AND NOT c.rolsuper AND NOT c.rolbypassrls AND NOT s.rolsuper AND NOT s.rolbypassrls "
                    "FROM pg_roles c JOIN pg_roles s ON s.rolname=session_user WHERE c.rolname=current_user"
                )
            ).scalar_one()
            if safe is not True:
                raise DispatchUnavailable("unsafe_database_role")
            db.execute(text("SET LOCAL statement_timeout='3s'"))
            db.execute(text("SET LOCAL lock_timeout='1s'"))
            subject = db.execute(
                text("SELECT current_setting('request.jwt.claim.sub',true)")
            ).scalar_one()
            if subject != str(verified_identity.id):
                raise DispatchUnavailable("unsafe_actor_context")
            if (
                db.execute(text("SELECT current_setting('transaction_isolation')")).scalar_one()
                != "read committed"
            ):
                raise DispatchUnavailable("quota_isolation_unsupported")
            if (
                db.execute(
                    text("SELECT user_id FROM public.app_admissions WHERE user_id=:actor"),
                    {"actor": verified_identity.id},
                ).scalar_one_or_none()
                is None
            ):
                raise DispatchUnavailable("admission_required")
            return db.execute(_SELECT, {"actor": verified_identity.id}).scalar_one_or_none()

    try:
        job_id = short_transaction(select_one)
    except QuotaUnavailable:
        return "not_claimed"  # No claim or I/O has taken place, no invented success.
    except (SQLAlchemyError, psycopg.Error):
        raise DispatchUnavailable("database_unavailable") from None
    if job_id is None:
        return "idle"
    result = await worker.run_one(actor_id=verified_identity.id, job_id=job_id)
    match result:
        case "not_claimed" | "yielded" | "succeeded" | "failed" | "lease_lost":
            return result
        case _:
            raise DispatchUnavailable("invalid_worker_result")
