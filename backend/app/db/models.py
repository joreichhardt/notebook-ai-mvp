from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
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
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("role IN ('owner','editor','viewer')"),)
