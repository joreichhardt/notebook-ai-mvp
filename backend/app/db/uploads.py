"""Actor-scoped upload persistence. No commits, I/O, actor issuance or Storage credentials."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class UploadConflict(ValueError):
    """Idempotency key refers to different immutable request content/options (HTTP409)."""


class UploadUnavailable(ValueError):
    """Absent, inaccessible, expired or not yet confirmed (opaque to HTTP caller)."""


@dataclass(frozen=True)
class UploadIntent:
    id: UUID
    document_id: UUID
    version_id: UUID
    job_id: UUID
    workspace_id: UUID
    notebook_id: UUID
    storage_key: str
    state: str
    filename: str
    sha256: str
    byte_count: int
    media_type: str


_COLUMNS = "id,document_id,version_id,job_id,workspace_id,notebook_id,storage_key,state,filename,sha256,byte_count,media_type"


def reserve_upload(
    session: Session,
    notebook_id: UUID,
    *,
    idempotency_key: str,
    filename: str,
    sha256: str,
    byte_count: int,
    media_type: str,
    connection_id: UUID | None = None,
    index_profile_id: UUID | None = None,
    document_id: UUID | None = None,
    expected_version_id: UUID | None = None,
) -> UploadIntent:
    """Reserve immutable new TXT/MD intent before any external side effect.

    Commit this transaction before I/O. Options/replacements are deliberately unsupported.
    All IDs/key come from PostgreSQL defaults, never filename or browser actor input.
    """
    if any(
        x is not None for x in (connection_id, index_profile_id, document_id, expected_version_id)
    ):
        raise ValueError("upload_options_unsupported")
    if media_type not in ("text/plain", "text/markdown"):
        raise ValueError("upload_media_unsupported")
    # Notebook lock serializes reservations and parent teardown; no global job scans.
    workspace = session.execute(
        text("""
        SELECT n.workspace_id FROM public.notebooks n
        JOIN public.workspace_members m ON m.workspace_id=n.workspace_id
        WHERE n.id=:n AND m.user_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid
        AND m.role IN ('owner','editor') FOR UPDATE OF n
    """),
        {"n": notebook_id},
    ).scalar_one_or_none()
    if workspace is None:
        raise UploadUnavailable("upload_unavailable")
    params = {
        "n": notebook_id,
        "w": workspace,
        "k": idempotency_key,
        "f": filename,
        "h": sha256,
        "b": byte_count,
        "m": media_type,
    }
    session.execute(
        text("""
        INSERT INTO public.upload_intents(workspace_id,notebook_id,idempotency_key,filename,sha256,byte_count,media_type)
        VALUES (:w,:n,:k,:f,:h,:b,:m) ON CONFLICT(actor_user_id,notebook_id,idempotency_key) DO NOTHING
    """),
        params,
    )
    row = (
        session.execute(
            text(
                f"SELECT {_COLUMNS} FROM public.upload_intents WHERE notebook_id=:n AND idempotency_key=:k"
            ),
            params,
        )
        .mappings()
        .one()
    )
    intent = UploadIntent(**row)
    if (intent.filename, intent.sha256, intent.byte_count, intent.media_type) != (
        filename,
        sha256,
        byte_count,
        media_type,
    ):
        raise UploadConflict("upload_idempotency_conflict")
    return intent


@dataclass(frozen=True)
class UploadResult:
    document_id: UUID
    version_id: UUID
    job_id: UUID


def finalize_upload(session: Session, intent_id: UUID) -> UploadResult:
    """Insert as notebook_app, verify via boolean RPC, commit guard enforces coupling.

    Caller commits before returning HTTP202. Never perform I/O in this transaction.
    Expiry, absent receipt or authorization fails opaquely. No automatic replacement.
    """
    from app.db.models import Document, DocumentVersion, IngestionJob

    row = (
        session.execute(
            text(f"SELECT {_COLUMNS} FROM public.upload_intents WHERE id=:i"), {"i": intent_id}
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["state"] not in ("stored", "finalized"):
        raise UploadUnavailable("upload_unavailable")
    intent = UploadIntent(**row)
    # Parent serializes competing new-source finalizers. Inserted source/job
    # rows remain uncommitted until the ledger verifier succeeds.
    parent = session.execute(
        text("SELECT id FROM public.notebooks WHERE id=:n FOR UPDATE"), {"n": intent.notebook_id}
    ).scalar_one_or_none()
    if parent is None:
        raise UploadUnavailable("upload_unavailable")
    # READ COMMITTED fresh row after waiting for the parent lock; never replay
    # the pre-lock state or an ORM identity-map object from a competing request.
    row = (
        session.execute(
            text(f"SELECT {_COLUMNS} FROM public.upload_intents WHERE id=:i"), {"i": intent_id}
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["state"] not in ("stored", "finalized"):
        raise UploadUnavailable("upload_unavailable")
    intent = UploadIntent(**row)
    if intent.state == "stored":
        actor = session.execute(
            text("SELECT nullif(current_setting('request.jwt.claim.sub',true),'')::uuid")
        ).scalar_one()
        session.add(
            Document(
                id=intent.document_id,
                workspace_id=intent.workspace_id,
                notebook_id=intent.notebook_id,
                created_by=actor,
                filename=intent.filename,
                current_version_id=intent.version_id,
            )
        )
        session.flush()
        session.add(
            DocumentVersion(
                id=intent.version_id,
                document_id=intent.document_id,
                workspace_id=intent.workspace_id,
                notebook_id=intent.notebook_id,
                version=1,
                sha256=intent.sha256,
                byte_count=intent.byte_count,
                media_type=intent.media_type,
                storage_key=intent.storage_key,
            )
        )
        session.flush()
        key = session.execute(
            text("SELECT idempotency_key FROM public.upload_intents WHERE id=:i"), {"i": intent_id}
        ).scalar_one()
        session.add(
            IngestionJob(
                id=intent.job_id,
                document_version_id=intent.version_id,
                workspace_id=intent.workspace_id,
                notebook_id=intent.notebook_id,
                actor_user_id=actor,
                idempotency_key=key,
            )
        )
        session.flush()
    if not session.execute(
        text("SELECT public.finalize_upload(:i)"), {"i": intent_id}
    ).scalar_one():
        raise UploadUnavailable("upload_unavailable")
    return UploadResult(intent.document_id, intent.version_id, intent.job_id)


def check_upload_admission(session: Session, intent_id: UUID, token: UUID) -> bool:
    """Fresh actor/editor+intent/write-lease check before trusted external I/O.

    An authorization checkpoint, NOT a transaction across network I/O or guarantee
    that later revocation cannot race the remote request. Cleanup retains the key.
    """
    return (
        session.execute(
            text("""
        SELECT i.id FROM public.upload_intents i
        JOIN public.notebooks n ON n.id=i.notebook_id AND n.workspace_id=i.workspace_id
        JOIN public.workspace_members m ON m.workspace_id=i.workspace_id AND m.user_id=i.actor_user_id
        WHERE i.id=:i AND i.state='reserved' AND i.write_token=:t
        AND i.expires_at>clock_timestamp() AND i.write_expires_at>clock_timestamp()
        AND m.role IN ('owner','editor')
    """),
            {"i": intent_id, "t": token},
        ).scalar_one_or_none()
        is not None
    )


@dataclass(frozen=True)
class UploadWriteClaim:
    intent_id: UUID
    token: UUID
    storage_key: str
    sha256: str
    byte_count: int
    media_type: str


@dataclass(frozen=True)
class CleanupClaim:
    intent_id: UUID
    token: UUID
    storage_key: str


def claim_upload_io(
    session: Session, intent_id: UUID, *, lease_seconds: int = 60
) -> UploadWriteClaim | None:
    """Separate storage-capability session only; commit before performing I/O."""
    row = (
        session.execute(
            text("SELECT * FROM public.claim_upload_io(:i,:s)"),
            {"i": intent_id, "s": lease_seconds},
        )
        .mappings()
        .one_or_none()
    )
    return UploadWriteClaim(**row) if row is not None else None


def confirm_upload_io(
    session: Session, intent_id: UUID, token: UUID, *, sha256: str, byte_count: int
) -> bool:
    """Trusted complete-object readback receipt, not supplied by browser."""
    return bool(
        session.execute(
            text("SELECT public.confirm_upload_io(:i,:t,:h,:b)"),
            {"i": intent_id, "t": token, "h": sha256, "b": byte_count},
        ).scalar_one()
    )


def list_cleanup(session: Session, *, limit: int = 100) -> list[UUID]:
    """Bounded IDs only, including periodically due cleaned tombstones."""
    return list(
        session.execute(text("SELECT * FROM public.list_cleanup(:n)"), {"n": limit}).scalars()
    )


def claim_cleanup(
    session: Session, intent_id: UUID, *, lease_seconds: int = 60
) -> CleanupClaim | None:
    row = (
        session.execute(
            text("SELECT * FROM public.claim_cleanup(:i,:s)"), {"i": intent_id, "s": lease_seconds}
        )
        .mappings()
        .one_or_none()
    )
    return CleanupClaim(**row) if row is not None else None


def finish_cleanup(session: Session, intent_id: UUID, token: UUID) -> bool:
    """Record one absence-check cycle only, NEVER permanent remote finality."""
    return bool(
        session.execute(
            text("SELECT public.finish_cleanup(:i,:t)"), {"i": intent_id, "t": token}
        ).scalar_one()
    )
