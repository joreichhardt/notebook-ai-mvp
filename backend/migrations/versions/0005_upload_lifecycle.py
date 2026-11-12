"""Durable upload lifecycle and member-safe current-version processing metadata."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    # Version-controlled static DDL, not caller input; SQL identifiers are fixed.
    op.execute(text(Path(__file__).with_suffix(".sql").read_text()))


def downgrade():
    raise RuntimeError(
        "0005 retains cleanup tombstones; restore a reviewed backup, do not erase work"
    )
