"""Invoker-only lease primitives; no worker login, dispatch or privilege escalation.

Use a short READ COMMITTED transaction as notebook_app, with an authenticated
actor's transaction-local context. Commit claims/heartbeats before network I/O.
RLS rechecks admission, edit membership, current source and active owned connection.
These helpers do not commit. For publication: lock_job_for_publish, insert chunks,
finish_job in ONE transaction; roll back everything if any helper returns false.
Do not publish by calling finish_job in a different transaction from chunk writes.
"""

from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session


def _duration(seconds: int) -> int:
    if not 1 <= seconds <= 300:
        raise ValueError("lease_seconds must be between 1 and 300")
    return seconds


def claim_job(session: Session, job_id: UUID, *, lease_seconds: int = 60) -> UUID | None:
    """Claim a specific actor-owned job, or recover an expired lease, at most 5 times."""
    token = uuid4()
    return session.execute(
        text("""
        WITH candidate AS (
          SELECT id FROM public.ingestion_jobs
          WHERE id=:job AND attempts<5 AND available_at<=clock_timestamp()
          AND (status='queued' OR (status='running' AND lease_expires_at<=clock_timestamp()))
          FOR UPDATE SKIP LOCKED
        )
        UPDATE public.ingestion_jobs j
        SET status='running', attempts=j.attempts+1, lease_token=:token,
            heartbeat_at=clock_timestamp(), updated_at=clock_timestamp(), error_code=NULL,
            lease_expires_at=clock_timestamp()+make_interval(secs => :seconds)
        FROM candidate c WHERE j.id=c.id RETURNING j.lease_token
        """),
        {"job": job_id, "token": token, "seconds": _duration(lease_seconds)},
    ).scalar_one_or_none()


def heartbeat_job(session: Session, job_id: UUID, token: UUID, *, lease_seconds: int = 60) -> bool:
    """An expired or reassigned token can never renew a lease."""
    return (
        session.execute(
            text("""
        UPDATE public.ingestion_jobs
        SET heartbeat_at=clock_timestamp(), updated_at=clock_timestamp(),
            lease_expires_at=clock_timestamp()+make_interval(secs => :seconds)
        WHERE id=:job AND status='running' AND lease_token=:token
          AND lease_expires_at>clock_timestamp()
        RETURNING id
        """),
            {"job": job_id, "token": token, "seconds": _duration(lease_seconds)},
        ).scalar_one_or_none()
        is not None
    )


def lock_job_for_publish(session: Session, job_id: UUID, token: UUID) -> bool:
    """Lock source then job against deletion/version changes/reassignment until commit.

    False means rollback. Call finish_job last, after all chunk writes, to recheck
    the clock and current authorization; if false, rollback those writes too.
    """
    document = session.execute(
        text("""
        SELECT d.id FROM public.documents d
        JOIN public.document_versions v ON v.id=d.current_version_id AND v.document_id=d.id
        JOIN public.ingestion_jobs j ON j.document_version_id=v.id
        WHERE j.id=:job AND j.status='running' AND j.lease_token=:token
          AND j.lease_expires_at>clock_timestamp()
        FOR UPDATE OF d
        """),
        {"job": job_id, "token": token},
    ).scalar_one_or_none()
    if document is None:
        return False
    # UPDATE (not just SELECT) invokes the job's writer/connection policy.
    return (
        session.execute(
            text("""
        UPDATE public.ingestion_jobs SET updated_at=clock_timestamp()
        WHERE id=:job AND status='running' AND lease_token=:token
          AND lease_expires_at>clock_timestamp()
        RETURNING id
        """),
            {"job": job_id, "token": token},
        ).scalar_one_or_none()
        is not None
    )


def finish_job(
    session: Session,
    job_id: UUID,
    token: UUID,
    *,
    status: Literal["succeeded", "failed", "cancelled"],
    error_code: str | None = None,
) -> bool:
    """Fenced terminal transition; a false result MUST roll back publication."""
    if status not in ("succeeded", "failed", "cancelled"):
        raise ValueError("terminal status required")
    return (
        session.execute(
            text("""
        UPDATE public.ingestion_jobs
        SET status=:status, error_code=:error, lease_token=NULL, lease_expires_at=NULL,
            heartbeat_at=NULL, updated_at=clock_timestamp()
        WHERE id=:job AND status='running' AND lease_token=:token
          AND lease_expires_at>clock_timestamp()
        RETURNING id
        """),
            {"job": job_id, "token": token, "status": status, "error": error_code},
        ).scalar_one_or_none()
        is not None
    )
