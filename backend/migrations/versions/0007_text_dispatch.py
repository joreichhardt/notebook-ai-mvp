"""Private exact text-job delegation; runtime provisioning is deliberately separate."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(text(Path(__file__).with_suffix(".sql").read_text()))


def downgrade():
    raise RuntimeError("0007 durable delegation requires a separately reviewed rollback")
