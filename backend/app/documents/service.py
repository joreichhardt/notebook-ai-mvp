"""Gated upload orchestration using SHORT trusted transaction factories.

Factories are server dependencies, never browser parameters. ActorTx must yield
fresh notebook_app context for an already verified actor and commit on exit;
IoTx must be a SEPARATE nonprivileged login pinned to notebook_storage_io.
No engine/role/credential provisioning, HTTP registration or cleanup finality here.
"""
import asyncio
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from hashlib import sha256
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import Admission, Document, DocumentVersion, IngestionJob, Member, Notebook
from app.db.uploads import (
    UploadConflict,
    UploadUnavailable,
    check_upload_admission,
    claim_upload_io,
    confirm_upload_io,
    finalize_upload,
    reserve_upload,
)
from app.documents.storage import (
    Authorize,
    ObjectScope,
    StorageConfig,
    StorageError,
    SupabaseStorage,
)
from app.ingestion.text import TextRejected, TextUpload, read_text_upload

ActorTx = Callable[[UUID], AbstractContextManager[Session]]
IoTx = Callable[[], AbstractContextManager[Session]]


class DocumentError(RuntimeError):
    def __init__(self, code: str, status_code: int = 503):
        self.code, self.status_code = code, status_code
        super().__init__(code)


class DocumentDTO(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    notebook_id: UUID
    filename: str
    version_id: UUID
    status: Literal["queued", "processing", "ready", "failed"]
    error_code: Literal["not_processed", "processing_failed", "processing_cancelled"] | None


class JobDTO(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]


class UploadReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)
    document: DocumentDTO
    job: JobDTO


class UploadStore(Protocol):
    async def put(self, actor_id: UUID, scope: ObjectScope, data: bytes, media_type: str) -> None: ...
    async def read(self, actor_id: UUID, scope: ObjectScope) -> bytes: ...


class _BufferedUpload:
    def __init__(self, data: bytes):
        self.data, self.offset = data, 0

    async def read(self, size: int) -> bytes:
        result = self.data[self.offset:self.offset + size]
        self.offset += len(result)
        return result


class UploadService:
    def __init__(self, *, actor_tx: ActorTx | None = None, io_tx: IoTx | None = None,
                 storage: StorageConfig | None = None,
                 store_factory: Callable[[Authorize], UploadStore] | None = None):
        self.actor_tx, self.io_tx = actor_tx, io_tx
        self.configured = io_tx is not None and (storage is not None or store_factory is not None)
        # Explicit synthetic injection is for tests only. No default in-memory store.
        self.store_factory = store_factory or (lambda auth: SupabaseStorage(storage, authorize=auth))

    @staticmethod
    def _role(db: Session, expected: str):
        role = db.execute(text(
            "SELECT current_user,session_user,c.rolsuper,c.rolbypassrls,s.rolsuper,s.rolbypassrls "
            "FROM pg_roles c JOIN pg_roles s ON s.rolname=session_user WHERE c.rolname=current_user"
        )).one()
        if (role[0] != expected or any(role[2:])
                or (expected == "notebook_app" and role[1] != expected)
                or (expected == "notebook_storage_io" and role[1] == "notebook_app")):
            raise DocumentError("unsafe_database_role")
        db.execute(text("SET LOCAL statement_timeout='3s'"))
        db.execute(text("SET LOCAL lock_timeout='1s'"))

    @contextmanager
    def _actor(self, actor: UUID) -> Iterator[Session]:
        if not isinstance(actor, UUID):
            raise DocumentError("authentication_required", 401)
        if self.actor_tx is None:
            raise DocumentError("database_unconfigured")
        try:
            with self.actor_tx(actor) as db:
                self._role(db, "notebook_app")
                subject = db.execute(text("SELECT current_setting('request.jwt.claim.sub',true)")).scalar_one()
                if subject != str(actor):
                    raise DocumentError("unsafe_actor_context")
                if db.scalar(select(Admission.user_id).where(Admission.user_id == actor)) is None:
                    raise DocumentError("admission_required", 403)
                yield db
        except SQLAlchemyError:
            raise DocumentError("database_unavailable") from None

    @contextmanager
    def _io(self) -> Iterator[Session]:
        if self.io_tx is None:
            raise DocumentError("storage_unconfigured")
        try:
            with self.io_tx() as db:
                self._role(db, "notebook_storage_io")
                yield db
        except SQLAlchemyError:
            raise DocumentError("database_unavailable") from None

    @staticmethod
    def _notebook(db: Session, actor: UUID, notebook: UUID, *, write: bool):
        workspace = db.scalar(select(Notebook.workspace_id).where(Notebook.id == notebook))
        if workspace is None:
            raise DocumentError("notebook_not_found", 404)
        role = db.scalar(select(Member.role).where(Member.workspace_id == workspace, Member.user_id == actor))
        if role is None or (write and role not in ("owner", "editor")):
            raise DocumentError("write_required" if write else "notebook_not_found", 403 if write else 404)

    @staticmethod
    def _projection(notebook: UUID):
        return select(
            Document.id, Document.notebook_id, Document.filename,
            DocumentVersion.id.label("version_id"),
            DocumentVersion.processing_status.label("status"), DocumentVersion.error_code,
        ).join(DocumentVersion, (Document.current_version_id == DocumentVersion.id)
               & (Document.id == DocumentVersion.document_id)
               & (Document.notebook_id == DocumentVersion.notebook_id)
               & (Document.workspace_id == DocumentVersion.workspace_id)).where(
                   Document.notebook_id == notebook, Document.deleted_at.is_(None))

    def list_documents(self, *, actor_id: UUID, notebook_id: UUID, limit: int = 100) -> list[DocumentDTO]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DocumentError("invalid_limit", 422)
        with self._actor(actor_id) as db:
            self._notebook(db, actor_id, notebook_id, write=False)
            rows = db.execute(self._projection(notebook_id).order_by(Document.created_at, Document.id).limit(limit)).mappings()
            return [DocumentDTO(**row) for row in rows]  # NO raw/private job query for members

    async def upload(self, *, actor_id: UUID, notebook_id: UUID, idempotency_key: str,
                     upload: TextUpload, connection_id: UUID | None = None,
                     index_profile_id: UUID | None = None, document_id: UUID | None = None,
                     expected_version_id: UUID | None = None) -> UploadReceipt:
        try:
            with self._actor(actor_id) as db:
                self._notebook(db, actor_id, notebook_id, write=True)
            if not self.configured:
                raise DocumentError("storage_unconfigured")
            if connection_id is not None or index_profile_id is not None:
                raise DocumentError("unsupported_embedding", 415)
            if document_id is not None or expected_version_id is not None:
                raise DocumentError("unsupported_replacement", 415)
            if not 1 <= len(idempotency_key) <= 128 or any(c in idempotency_key for c in "\r\n\x00"):
                raise DocumentError("invalid_idempotency_key", 422)
            validated = await read_text_upload(_BufferedUpload(upload.data), upload.filename, upload.media_type)
            if validated != upload:
                raise DocumentError("invalid_upload", 422)
            with self._actor(actor_id) as db:
                intent = reserve_upload(db, notebook_id, idempotency_key=idempotency_key,
                                        filename=upload.filename, sha256=upload.sha256,
                                        byte_count=len(upload.data), media_type=upload.media_type)
            scope = ObjectScope(intent.workspace_id, intent.notebook_id, intent.document_id, intent.version_id)
            if scope.key != intent.storage_key:
                raise DocumentError("invalid_upload_intent")
            if intent.state not in ("stored", "finalized"):
                with self._io() as db:
                    claim = claim_upload_io(db, intent.id, lease_seconds=120)
                if claim is None:
                    raise DocumentError("upload_in_progress")
                if (claim.storage_key, claim.sha256, claim.byte_count, claim.media_type) != (
                    scope.key, upload.sha256, len(upload.data), upload.media_type,
                ):
                    raise DocumentError("invalid_upload_claim")

                async def authorize(actor: UUID, target: ObjectScope, action: Literal["read", "write"]):
                    if actor != actor_id or target != scope or action not in ("read", "write"):
                        raise DocumentError("upload_forbidden", 403)
                    with self._actor(actor_id) as db:
                        if not check_upload_admission(db, intent.id, claim.token):
                            raise DocumentError("upload_unavailable")

                # No transaction spans external work.120s single-use lease leaves
                # margin beyond two adapter30s action groups; never renew/reuse key.
                async with asyncio.timeout(90):
                    await authorize(actor_id, scope, "write")
                    store = self.store_factory(authorize)
                    await store.put(actor_id, scope, upload.data, upload.media_type)
                    await authorize(actor_id, scope, "read")
                    original = await store.read(actor_id, scope)
                    await authorize(actor_id, scope, "read")
                    if len(original) != len(upload.data) or sha256(original).hexdigest() != upload.sha256:
                        raise DocumentError("original_integrity_failed", 502)
                with self._io() as db:
                    if not confirm_upload_io(db, intent.id, claim.token,
                                             sha256=upload.sha256, byte_count=len(original)):
                        raise DocumentError("upload_unavailable")
            with self._actor(actor_id) as db:
                self._notebook(db, actor_id, notebook_id, write=True)
                result = finalize_upload(db, intent.id)
                row = db.execute(self._projection(notebook_id).where(Document.id == result.document_id)).mappings().one()
                # Only the uploader's canonical job, never member-wide raw job discovery.
                job = db.execute(select(IngestionJob.id, IngestionJob.status).where(
                    IngestionJob.id == result.job_id, IngestionJob.actor_user_id == actor_id)).mappings().one()
                receipt = UploadReceipt(document=DocumentDTO(**row), job=JobDTO(**job))
            return receipt  # Final transaction COMMITTED before any accepted response.
        except UploadConflict:
            raise DocumentError("upload_idempotency_conflict", 409) from None
        except UploadUnavailable:
            raise DocumentError("upload_unavailable") from None
        except TextRejected as exc:
            status = 415 if exc.code == "unsupported_media" else 413 if exc.code.endswith("too_large") else 422
            raise DocumentError(exc.code, status) from None
        except StorageError:
            raise DocumentError("storage_unavailable") from None
        except TimeoutError:
            raise DocumentError("upload_timeout") from None
