DO $$ BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='notebook_membership') THEN
  CREATE ROLE notebook_membership NOLOGIN NOSUPERUSER BYPASSRLS NOINHERIT;
 END IF;
END $$;
GRANT USAGE,CREATE ON SCHEMA public TO notebook_membership;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.app_admissions,public.workspaces,public.workspace_members,public.invitations TO notebook_membership;
GRANT SELECT ON public.platform_admins TO notebook_membership;
ALTER TABLE public.platform_admins ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.platform_admins FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.platform_admins FROM PUBLIC;
CREATE POLICY admin_self ON public.platform_admins FOR SELECT TO notebook_app USING (user_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid);
GRANT SELECT ON public.platform_admins TO notebook_app;
ALTER TABLE public.user_provider_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_provider_connections FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.user_provider_connections FROM PUBLIC;
CREATE POLICY connection_owner ON public.user_provider_connections TO notebook_app
 USING (user_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid AND EXISTS (SELECT 1 FROM public.app_admissions a WHERE a.user_id=user_provider_connections.user_id))
 WITH CHECK (user_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid AND EXISTS (SELECT 1 FROM public.app_admissions a WHERE a.user_id=user_provider_connections.user_id));
GRANT SELECT,INSERT,UPDATE,DELETE ON public.user_provider_connections TO notebook_app;

CREATE FUNCTION public.notebook_create_invite(p_id uuid,p_hash text,p_email text,p_workspace uuid,p_role text)
RETURNS timestamptz LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid; expiry timestamptz:=now()+interval '7 days';
BEGIN
 IF actor IS NULL OR NOT EXISTS(SELECT 1 FROM public.app_admissions WHERE user_id=actor) THEN RAISE EXCEPTION 'admission_required' USING ERRCODE='42501'; END IF;
 IF p_role NOT IN ('owner','editor','viewer') OR length(p_hash)<>64 OR length(p_email)<3 THEN RAISE EXCEPTION 'invalid_invitation' USING ERRCODE='22023'; END IF;
 IF p_workspace IS NULL THEN
  IF NOT EXISTS(SELECT 1 FROM public.platform_admins WHERE user_id=actor) THEN RAISE EXCEPTION 'admin_required' USING ERRCODE='42501'; END IF;
 ELSE
  PERFORM 1 FROM public.workspaces WHERE id=p_workspace FOR UPDATE;
  IF NOT EXISTS(SELECT 1 FROM public.workspace_members WHERE workspace_id=p_workspace AND user_id=actor AND role='owner') THEN RAISE EXCEPTION 'owner_required' USING ERRCODE='42501'; END IF;
 END IF;
 INSERT INTO public.invitations(id,token_hash,email,workspace_id,role,created_by,expires_at) VALUES(p_id,p_hash,lower(p_email),p_workspace,p_role,actor,expiry);
 RETURN expiry;
END $$;

CREATE FUNCTION public.notebook_accept_invite(p_hash text)
RETURNS TABLE(workspace_id uuid,invited_workspace_id uuid) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid;
 email text:=nullif(current_setting('request.jwt.claim.email',true),''); inv public.invitations; personal uuid;
BEGIN
 IF actor IS NULL OR email IS NULL THEN RAISE EXCEPTION 'identity_required' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(actor::text,0));
 SELECT * INTO inv FROM public.invitations WHERE token_hash=p_hash;
 IF NOT FOUND THEN RAISE EXCEPTION 'invitation_invalid' USING ERRCODE='22023'; END IF;
 IF inv.workspace_id IS NOT NULL THEN PERFORM 1 FROM public.workspaces w WHERE w.id=inv.workspace_id FOR UPDATE; END IF;
 SELECT * INTO inv FROM public.invitations WHERE token_hash=p_hash FOR UPDATE;
 IF lower(email)<>inv.email OR inv.revoked_at IS NOT NULL OR inv.expires_at<=now() THEN RAISE EXCEPTION 'invitation_invalid' USING ERRCODE='22023'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.app_admissions WHERE user_id=inv.created_by) OR
   (inv.workspace_id IS NULL AND NOT EXISTS(SELECT 1 FROM public.platform_admins WHERE user_id=inv.created_by)) OR
   (inv.workspace_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.workspace_members m WHERE m.workspace_id=inv.workspace_id AND m.user_id=inv.created_by AND m.role='owner')) THEN RAISE EXCEPTION 'invitation_invalid' USING ERRCODE='22023'; END IF;
 IF inv.consumed_at IS NOT NULL AND inv.consumed_by IS DISTINCT FROM actor THEN RAISE EXCEPTION 'invitation_invalid' USING ERRCODE='22023'; END IF;
 IF inv.consumed_at IS NOT NULL THEN
  SELECT w.id INTO personal FROM public.workspaces w WHERE w.personal_owner_id=actor;
  RETURN QUERY SELECT personal,inv.workspace_id; RETURN;
 END IF;
 INSERT INTO public.app_admissions(user_id) VALUES(actor) ON CONFLICT DO NOTHING;
 INSERT INTO public.workspaces(id,name,personal_owner_id) VALUES(gen_random_uuid(),'Mein Workspace',actor) ON CONFLICT(personal_owner_id) DO NOTHING;
 SELECT w.id INTO personal FROM public.workspaces w WHERE w.personal_owner_id=actor;
 INSERT INTO public.workspace_members VALUES(personal,actor,'owner') ON CONFLICT DO NOTHING;
 IF inv.workspace_id IS NOT NULL THEN
  INSERT INTO public.workspace_members VALUES(inv.workspace_id,actor,inv.role) ON CONFLICT DO NOTHING;
 END IF;
 UPDATE public.invitations SET consumed_at=now(),consumed_by=actor WHERE id=inv.id;
 RETURN QUERY SELECT personal,inv.workspace_id;
END $$;

CREATE FUNCTION public.notebook_members(p_workspace uuid)
RETURNS TABLE(user_id uuid,role text) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.app_admissions WHERE app_admissions.user_id=actor) OR NOT EXISTS(SELECT 1 FROM public.workspace_members m WHERE m.workspace_id=p_workspace AND m.user_id=actor AND m.role='owner') THEN RAISE EXCEPTION 'owner_required' USING ERRCODE='42501'; END IF;
 RETURN QUERY SELECT m.user_id,m.role::text FROM public.workspace_members m WHERE m.workspace_id=p_workspace;
END $$;

CREATE FUNCTION public.notebook_change_member(p_workspace uuid,p_user uuid,p_role text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid; old_role text;
BEGIN
 PERFORM 1 FROM public.workspaces WHERE id=p_workspace FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM public.app_admissions WHERE user_id=actor) OR NOT EXISTS(SELECT 1 FROM public.workspace_members WHERE workspace_id=p_workspace AND user_id=actor AND role='owner') THEN RAISE EXCEPTION 'owner_required' USING ERRCODE='42501'; END IF;
 IF p_role IS NOT NULL AND p_role NOT IN ('owner','editor','viewer') THEN RAISE EXCEPTION 'invalid_role' USING ERRCODE='22023'; END IF;
 SELECT role INTO old_role FROM public.workspace_members WHERE workspace_id=p_workspace AND user_id=p_user;
 IF NOT FOUND THEN RAISE EXCEPTION 'member_missing' USING ERRCODE='22023'; END IF;
 IF old_role='owner' AND p_role IS DISTINCT FROM 'owner' AND (SELECT count(*) FROM public.workspace_members WHERE workspace_id=p_workspace AND role='owner')<=1 THEN RAISE EXCEPTION 'last_owner' USING ERRCODE='22023'; END IF;
 IF p_role IS NULL THEN DELETE FROM public.workspace_members WHERE workspace_id=p_workspace AND user_id=p_user;
 ELSE UPDATE public.workspace_members SET role=p_role WHERE workspace_id=p_workspace AND user_id=p_user; END IF;
 IF old_role='owner' AND p_role IS DISTINCT FROM 'owner' THEN UPDATE public.invitations SET revoked_at=now() WHERE workspace_id=p_workspace AND created_by=p_user AND consumed_at IS NULL; END IF;
END $$;

ALTER FUNCTION public.notebook_create_invite(uuid,text,text,uuid,text) OWNER TO notebook_membership;
ALTER FUNCTION public.notebook_accept_invite(text) OWNER TO notebook_membership;
ALTER FUNCTION public.notebook_members(uuid) OWNER TO notebook_membership;
ALTER FUNCTION public.notebook_change_member(uuid,uuid,text) OWNER TO notebook_membership;
REVOKE ALL ON FUNCTION public.notebook_create_invite(uuid,text,text,uuid,text),public.notebook_accept_invite(text),public.notebook_members(uuid),public.notebook_change_member(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.notebook_create_invite(uuid,text,text,uuid,text),public.notebook_accept_invite(text),public.notebook_members(uuid),public.notebook_change_member(uuid,uuid,text) TO notebook_app;
REVOKE CREATE ON SCHEMA public FROM notebook_membership;
