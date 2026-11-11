"""App foundation and RLS. Runtime role cannot create admissions/membership."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""DO $$ BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='notebook_app') THEN
        CREATE ROLE notebook_app LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT;
      END IF;
    END $$""")
    op.create_table("app_admissions", sa.Column("user_id", sa.Uuid(), primary_key=True))
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("personal_owner_id", sa.Uuid(), unique=True),
    )
    op.create_table(
        "workspace_members",
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("role", sa.String(10), nullable=False),
        sa.CheckConstraint("role IN ('owner','editor','viewer')"),
    )
    op.create_table(
        "notebooks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.UniqueConstraint("id", "workspace_id"),
    )
    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE")),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("role IN ('owner','editor','viewer')"),
    )
    for table in ("app_admissions", "workspaces", "workspace_members", "notebooks", "invitations"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    uid = "nullif(current_setting('request.jwt.claim.sub',true),'')::uuid"
    admitted = f"EXISTS (SELECT 1 FROM app_admissions WHERE user_id={uid})"
    member = f"EXISTS (SELECT 1 FROM workspace_members m WHERE m.workspace_id=notebooks.workspace_id AND m.user_id={uid})"
    writer = member[:-1] + " AND m.role IN ('owner','editor'))"
    op.execute(
        f"CREATE POLICY admission_self ON app_admissions FOR SELECT TO notebook_app USING (user_id={uid})"
    )
    op.execute(
        f"CREATE POLICY membership_self ON workspace_members FOR SELECT TO notebook_app USING (user_id={uid} AND {admitted})"
    )
    op.execute(
        f"CREATE POLICY workspace_read ON workspaces FOR SELECT TO notebook_app USING ({admitted} AND EXISTS (SELECT 1 FROM workspace_members m WHERE m.workspace_id=workspaces.id AND m.user_id={uid}))"
    )
    op.execute(
        f"CREATE POLICY notebook_read ON notebooks FOR SELECT TO notebook_app USING ({admitted} AND {member})"
    )
    for command in ("INSERT", "UPDATE", "DELETE"):
        clause = (
            f"WITH CHECK ({admitted} AND {writer})"
            if command == "INSERT"
            else f"USING ({admitted} AND {writer})"
        )
        if command == "UPDATE":
            clause += f" WITH CHECK ({admitted} AND {writer})"
        op.execute(
            f"CREATE POLICY notebook_{command.lower()} ON notebooks FOR {command} TO notebook_app {clause}"
        )
    op.execute("GRANT USAGE ON SCHEMA public TO notebook_app")
    op.execute("GRANT SELECT ON app_admissions,workspaces,workspace_members TO notebook_app")
    op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON notebooks TO notebook_app")


def downgrade():
    for table in ("invitations", "notebooks", "workspace_members", "workspaces", "app_admissions"):
        op.drop_table(table)
