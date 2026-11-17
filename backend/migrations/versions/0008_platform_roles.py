"""Separate superadmin authority from application administration."""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    # Static reviewed DDL only; no operator/browser values or generated SQL.
    op.execute("""
ALTER TABLE public.platform_admins ADD COLUMN role text NOT NULL DEFAULT 'admin';
ALTER TABLE public.platform_admins ADD CONSTRAINT platform_admin_role
 CHECK (role IN ('admin','superadmin'));

DO $$ BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='notebook_platform_authority') THEN
  CREATE ROLE notebook_platform_authority NOLOGIN NOSUPERUSER BYPASSRLS NOINHERIT;
 END IF;
END $$;
GRANT USAGE,CREATE ON SCHEMA public TO notebook_platform_authority;
GRANT SELECT,INSERT ON public.platform_admins TO notebook_platform_authority;
GRANT SELECT ON public.app_admissions TO notebook_platform_authority;
-- PostgreSQL row locks require UPDATE privilege on at least one column.
-- Grant only the identity column, never UPDATE(role); runtime has no membership.
GRANT UPDATE(user_id) ON public.platform_admins,public.app_admissions TO notebook_platform_authority;

CREATE FUNCTION public.notebook_platform_administrators()
RETURNS TABLE(user_id uuid,role text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid;
BEGIN
 IF actor IS NULL OR NOT EXISTS (
  SELECT 1 FROM public.platform_admins p JOIN public.app_admissions a ON a.user_id=p.user_id
  WHERE p.user_id=actor AND p.role='superadmin'
 ) THEN RAISE EXCEPTION 'superadmin_required' USING ERRCODE='42501'; END IF;
 RETURN QUERY SELECT p.user_id,p.role FROM public.platform_admins p ORDER BY p.user_id;
END $$;

CREATE FUNCTION public.notebook_grant_app_admin(p_user_id uuid)
RETURNS TABLE(user_id uuid,role text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid;
BEGIN
 IF actor IS NULL OR NOT EXISTS (
  SELECT 1 FROM public.platform_admins p JOIN public.app_admissions a ON a.user_id=p.user_id
  WHERE p.user_id=actor AND p.role='superadmin'
 ) THEN RAISE EXCEPTION 'superadmin_required' USING ERRCODE='42501'; END IF;
 -- Serialize grants; recheck authority after waiting, before any mutation.
 PERFORM pg_advisory_xact_lock(hashtextextended('notebook-platform-authority',0));
 PERFORM 1 FROM public.platform_admins p JOIN public.app_admissions a ON a.user_id=p.user_id
  WHERE p.user_id=actor AND p.role='superadmin' FOR SHARE OF p,a;
 IF NOT FOUND THEN RAISE EXCEPTION 'superadmin_required' USING ERRCODE='42501'; END IF;
 IF p_user_id IS NULL OR NOT EXISTS (
  SELECT 1 FROM public.app_admissions a WHERE a.user_id=p_user_id
 ) THEN RAISE EXCEPTION 'admitted_user_required' USING ERRCODE='22023'; END IF;
 -- A normal grant cannot create a superadmin or demote an existing one.
 INSERT INTO public.platform_admins(user_id,role) VALUES(p_user_id,'admin')
  ON CONFLICT DO NOTHING;
 RETURN QUERY SELECT p.user_id,p.role FROM public.platform_admins p WHERE p.user_id=p_user_id;
END $$;

ALTER FUNCTION public.notebook_platform_administrators() OWNER TO notebook_platform_authority;
ALTER FUNCTION public.notebook_grant_app_admin(uuid) OWNER TO notebook_platform_authority;
REVOKE ALL ON FUNCTION public.notebook_platform_administrators(),public.notebook_grant_app_admin(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.notebook_platform_administrators(),public.notebook_grant_app_admin(uuid) TO notebook_app;
REVOKE CREATE ON SCHEMA public FROM notebook_platform_authority;
""")


def downgrade():
    raise RuntimeError("0008 platform authority requires an explicitly reviewed rollback")
