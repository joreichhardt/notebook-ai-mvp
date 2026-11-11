"""Controlled membership RPCs and personal provider records."""

from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("platform_admins", sa.Column("user_id", sa.Uuid(), primary_key=True))
    op.add_column("invitations", sa.Column("consumed_by", sa.Uuid()))
    op.create_table(
        "user_provider_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("secret_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("model", sa.String(200)),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_user_provider_connections_user_id", "user_provider_connections", ["user_id"]
    )
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError(
        "Membership migration requires reviewed forward recovery; no automatic downgrade."
    )
