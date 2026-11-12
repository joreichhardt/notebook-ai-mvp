from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Admission(Base):
    __tablename__ = "app_admissions"
    user_id: Mapped[UUID] = mapped_column(primary_key=True)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    personal_owner_id: Mapped[UUID | None] = mapped_column(unique=True)


class Member(Base):
    __tablename__ = "workspace_members"
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(10))
    __table_args__ = (CheckConstraint("role IN ('owner','editor','viewer')"),)


class Notebook(Base):
    __tablename__ = "notebooks"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    __table_args__ = (UniqueConstraint("id", "workspace_id"),)


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(10))
    created_by: Mapped[UUID]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_by: Mapped[UUID | None]
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("role IN ('owner','editor','viewer')"),)


class PlatformAdmin(Base):
    __tablename__ = "platform_admins"
    user_id: Mapped[UUID] = mapped_column(primary_key=True)


class ProviderConnection(Base):
    __tablename__ = "user_provider_connections"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(index=True)
    provider_id: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(120))
    secret_id: Mapped[UUID] = mapped_column(unique=True)
    model: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    notebook_id: Mapped[UUID]
    created_by: Mapped[UUID]
    filename: Mapped[str] = mapped_column(String(255))
    current_version_id: Mapped[UUID | None]
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("id", "notebook_id", "workspace_id"),
        ForeignKeyConstraint(
            ["notebook_id", "workspace_id"],
            ["notebooks.id", "notebooks.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["current_version_id", "id", "notebook_id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.notebook_id",
                "document_versions.workspace_id",
            ],
            name="documents_current_version_fk",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint("length(filename) BETWEEN 1 AND 255"),
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID]
    notebook_id: Mapped[UUID]
    workspace_id: Mapped[UUID]
    version: Mapped[int]
    media_type: Mapped[str] = mapped_column(String(32))
    byte_count: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    processing_status: Mapped[str] = mapped_column(String(16), server_default="failed")
    error_code: Mapped[str | None] = mapped_column(String(64), server_default="not_processed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("document_id", "version"),
        UniqueConstraint("id", "document_id", "notebook_id", "workspace_id"),
        UniqueConstraint("id", "notebook_id", "workspace_id"),
        ForeignKeyConstraint(
            ["document_id", "notebook_id", "workspace_id"],
            ["documents.id", "documents.notebook_id", "documents.workspace_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("version > 0"),
        CheckConstraint("media_type IN ('text/plain','text/markdown','application/pdf')"),
        CheckConstraint("byte_count BETWEEN 1 AND 26214400"),
        # SQLite-only unit fixtures are not schema/security evidence. Production
        # PostgreSQL always receives these checks (and uses migration DDL).
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'").ddl_if(dialect="postgresql"),
        CheckConstraint(
            "storage_key = workspace_id::text || '/' || notebook_id::text || '/' || "
            "document_id::text || '/' || id::text || '/original'"
        ).ddl_if(dialect="postgresql"),
    )


class UploadIntentRecord(Base):
    """Durable lifecycle ledger; intentionally no cascading parent foreign keys."""

    __tablename__ = "upload_intents"
    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    actor_user_id: Mapped[UUID] = mapped_column(
        server_default=func.nullif(func.current_setting("request.jwt.claim.sub", True), "")
    )
    workspace_id: Mapped[UUID]
    notebook_id: Mapped[UUID]
    document_id: Mapped[UUID] = mapped_column(unique=True, server_default=func.gen_random_uuid())
    version_id: Mapped[UUID] = mapped_column(unique=True, server_default=func.gen_random_uuid())
    job_id: Mapped[UUID] = mapped_column(unique=True, server_default=func.gen_random_uuid())
    idempotency_key: Mapped[str] = mapped_column(String(128))
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    byte_count: Mapped[int]
    media_type: Mapped[str] = mapped_column(String(32))
    # Generated by migration DDL. ORM never inserts ledger rows (column grants).
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    state: Mapped[str] = mapped_column(String(20), server_default="reserved")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    write_token: Mapped[UUID | None]
    write_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_token: Mapped[UUID | None]
    cleanup_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    # Privileged mirror metadata is excluded from ordinary ORM SELECTs. Even
    # explicit undefer cannot bypass the database's app column allowlist.
    job_state: Mapped[str | None] = mapped_column(String(16), deferred=True)
    job_attempts: Mapped[int | None] = mapped_column(deferred=True)
    job_lease_token: Mapped[UUID | None] = mapped_column(deferred=True)
    job_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), deferred=True
    )
    work_cancelled: Mapped[bool] = mapped_column(server_default="false", deferred=True)
    __table_args__ = (UniqueConstraint("actor_user_id", "notebook_id", "idempotency_key"),)


class IndexProfile(Base):
    __tablename__ = "index_profiles"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID]
    notebook_id: Mapped[UUID]
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    revision: Mapped[str] = mapped_column(String(200))
    dimensions: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("id", "notebook_id", "workspace_id"),
        UniqueConstraint("notebook_id", "provider", "model", "revision", "dimensions"),
        ForeignKeyConstraint(
            ["notebook_id", "workspace_id"],
            ["notebooks.id", "notebooks.workspace_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("dimensions BETWEEN 1 AND 65536"),
        CheckConstraint("length(provider) > 0"),
        CheckConstraint("length(model) > 0"),
        CheckConstraint("length(revision) > 0"),
    )


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_version_id: Mapped[UUID]
    document_id: Mapped[UUID]
    notebook_id: Mapped[UUID]
    workspace_id: Mapped[UUID]
    ordinal: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    page: Mapped[int | None]
    start_offset: Mapped[int]
    end_offset: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("document_version_id", "ordinal"),
        ForeignKeyConstraint(
            ["document_version_id", "document_id", "notebook_id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.notebook_id",
                "document_versions.workspace_id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint("ordinal >= 0"),
        CheckConstraint("length(text) BETWEEN 1 AND 2000000"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'").ddl_if(dialect="postgresql"),
        CheckConstraint("page BETWEEN 1 AND 500"),
        CheckConstraint("start_offset >= 0"),
        CheckConstraint("end_offset > start_offset AND end_offset <= 2000000"),
        CheckConstraint("end_offset - start_offset = length(text)"),
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_version_id: Mapped[UUID]
    notebook_id: Mapped[UUID]
    workspace_id: Mapped[UUID]
    actor_user_id: Mapped[UUID]
    connection_id: Mapped[UUID | None]
    index_profile_id: Mapped[UUID | None]
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    attempts: Mapped[int] = mapped_column(server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    lease_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("actor_user_id", "notebook_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["document_version_id", "notebook_id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.notebook_id",
                "document_versions.workspace_id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["index_profile_id", "notebook_id", "workspace_id"],
            ["index_profiles.id", "index_profiles.notebook_id", "index_profiles.workspace_id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("length(idempotency_key) BETWEEN 1 AND 128"),
        CheckConstraint("status IN ('queued','running','succeeded','failed','cancelled')"),
        CheckConstraint("attempts BETWEEN 0 AND 5"),
        CheckConstraint("error_code ~ '^[a-z][a-z0-9_]{0,63}$'").ddl_if(dialect="postgresql"),
        CheckConstraint("(connection_id IS NULL) = (index_profile_id IS NULL)"),
        CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND "
            "lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL AND attempts > 0) "
            "OR (status <> 'running' AND lease_token IS NULL AND "
            "lease_expires_at IS NULL AND heartbeat_at IS NULL)"
        ),
    )
