"""Frozen 0005 invoker lease behavior for version-pinned DB regressions only.

0006 intentionally rejects these unbound execution fixtures. This module is not
imported by application code; head behavior is tested against canonical uploads.
Copied from the 0005 baseline, retaining the original SQL/fencing semantics.
"""

from uuid import uuid4

from sqlalchemy import text


def _duration(seconds):
    if not 1 <= seconds <= 300:
        raise ValueError("lease_seconds must be between 1 and 300")
    return seconds


def claim_job(session, job_id, *, lease_seconds=60):
    _duration(lease_seconds)
    if (
        session.execute(
            text("""
        SELECT d.id FROM public.documents d
        JOIN public.document_versions v ON v.id=d.current_version_id AND v.document_id=d.id
        JOIN public.ingestion_jobs j ON j.document_version_id=v.id
        WHERE j.id=:job FOR UPDATE OF d SKIP LOCKED
    """),
            {"job": job_id},
        ).scalar_one_or_none()
        is None
    ):
        return None
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
        {"job": job_id, "token": uuid4(), "seconds": _duration(lease_seconds)},
    ).scalar_one_or_none()


def heartbeat_job(session, job_id, token, *, lease_seconds=60):
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


def lock_job_for_publish(session, job_id, token):
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


def finish_job(session, job_id, token, *, status, error_code=None):
    if status not in ("succeeded", "failed", "cancelled"):
        raise ValueError("terminal status required")
    if not lock_job_for_publish(session, job_id, token):
        return False
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
