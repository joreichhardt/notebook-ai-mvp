"""Scoped document persistence and actor-private jobs (text only)."""

from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    op.execute("DROP FUNCTION public.notebook_delete_document(uuid)")
    op.execute("ALTER TABLE public.documents DROP CONSTRAINT documents_current_version_fk")
    for table in ("ingestion_jobs", "chunks", "document_versions", "index_profiles", "documents"):
        op.drop_table(table)
    op.execute("DROP FUNCTION public.notebook_immutable_row()")
    op.execute("DROP FUNCTION public.notebook_document_identity()")
    op.execute("DROP FUNCTION public.notebook_job_identity()")
