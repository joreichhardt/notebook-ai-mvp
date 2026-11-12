"""Preserve immutable index-profile identities against DELETE/reinsert."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    # An UPDATE trigger cannot prevent replacing the same UUID via deferred FKs.
    # New model/revision/dimensions require a new identity. Parent notebook
    # cascades still perform authorized teardown without child DELETE privileges.
    op.execute("REVOKE DELETE ON public.index_profiles FROM notebook_app")


def downgrade():
    # Restores 0003 behavior, including its known identity-replacement weakness.
    op.execute("GRANT DELETE ON public.index_profiles TO notebook_app")
