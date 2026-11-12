"""Fail-closed canonical upload quotas; migration requires drained old workers."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text(Path(__file__).with_suffix(".sql").read_text()))


def downgrade():
    raise RuntimeError("0006 quota enforcement requires a separately reviewed rollback")
