-- Old 0005 writers do not take the gate. Exclude ALL relevant old mutations
-- before inventory/seed; operator-proven external worker quiescence is separate.
SET LOCAL lock_timeout='5s';
LOCK TABLE public.workspaces,public.notebooks,public.app_admissions,
 public.workspace_members,public.documents,public.document_versions,
 public.ingestion_jobs,public.upload_intents IN SHARE ROW EXCLUSIVE MODE;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM public.ingestion_jobs j
  LEFT JOIN public.upload_intents i ON i.job_id=j.id
  WHERE (i.id IS NULL AND (j.status IN ('queued','running') OR j.lease_token IS NOT NULL OR j.lease_expires_at IS NOT NULL))
  OR (i.id IS NOT NULL AND ((j.document_version_id,j.workspace_id,j.notebook_id,j.actor_user_id,j.idempotency_key)
   IS DISTINCT FROM (i.version_id,i.workspace_id,i.notebook_id,i.actor_user_id,i.idempotency_key)
   OR j.connection_id IS NOT NULL OR j.index_profile_id IS NOT NULL
   OR (j.status='running' AND (j.lease_token IS NULL OR j.lease_expires_at IS NULL OR j.attempts NOT BETWEEN 1 AND 5))
   OR (j.status<>'running' AND (j.lease_token IS NOT NULL OR j.lease_expires_at IS NOT NULL))
   OR (j.status='queued' AND j.attempts<>0))))
 OR EXISTS(SELECT 1 FROM public.ingestion_jobs j JOIN public.upload_intents i ON i.version_id=j.document_version_id WHERE j.id<>i.job_id)
 OR EXISTS(SELECT 1 FROM public.upload_intents i LEFT JOIN public.ingestion_jobs j ON j.id=i.job_id
  WHERE i.state='finalized' AND j.id IS NULL)
 OR EXISTS(SELECT 1 FROM public.upload_intents i JOIN public.ingestion_jobs j ON j.id=i.job_id
  LEFT JOIN public.documents d ON d.id=i.document_id
  LEFT JOIN public.document_versions v ON v.id=i.version_id
  WHERE (d.id,d.workspace_id,d.notebook_id,d.created_by,d.filename)
   IS DISTINCT FROM (i.document_id,i.workspace_id,i.notebook_id,i.actor_user_id,i.filename)
  OR (v.id,v.document_id,v.workspace_id,v.notebook_id,v.version,v.sha256,v.byte_count,v.media_type,v.storage_key)
   IS DISTINCT FROM (i.version_id,i.document_id,i.workspace_id,i.notebook_id,1,i.sha256,i.byte_count,i.media_type,i.storage_key)
  OR (i.state='finalized' AND (d.deleted_at IS NOT NULL OR d.current_version_id IS DISTINCT FROM i.version_id)))
 THEN RAISE EXCEPTION 'quota_preflight_required' USING ERRCODE='P0001'; END IF;
END $$;

ALTER TABLE public.upload_intents
 ADD COLUMN job_state varchar(16) CHECK(job_state IN ('queued','running','succeeded','failed','cancelled')),
 ADD COLUMN job_attempts integer,
 ADD COLUMN job_lease_token uuid,
 ADD COLUMN job_lease_expires_at timestamptz,
 ADD COLUMN work_cancelled boolean NOT NULL DEFAULT false;
-- Seed persisted canonical evidence only; no receipts, cleanup, status or history
-- rewrites. A missing historical deleted job is UNKNOWN external occupancy.
UPDATE public.upload_intents i SET job_state=j.status,job_attempts=j.attempts,
 job_lease_token=j.lease_token,job_lease_expires_at=j.lease_expires_at
 FROM public.ingestion_jobs j WHERE j.id=i.job_id;
DO $$ DECLARE t timestamptz:=clock_timestamp(); BEGIN
 IF EXISTS(SELECT FROM public.upload_intents WHERE
  ((state IN ('reserved','stored') AND expires_at>t) OR (state='finalized' AND job_state IN ('queued','running')))
  GROUP BY actor_user_id,notebook_id HAVING count(*)>10)
 OR EXISTS(SELECT FROM public.upload_intents WHERE
  ((state IN ('reserved','stored') AND expires_at>t) OR (state='finalized' AND job_state IN ('queued','running')))
  GROUP BY notebook_id HAVING count(*)>50)
 OR EXISTS(SELECT FROM public.upload_intents WHERE job_state='running' AND job_lease_expires_at>t
  GROUP BY actor_user_id HAVING count(*)>2)
 OR EXISTS(SELECT FROM public.upload_intents WHERE job_state='running' AND job_lease_expires_at>t
  GROUP BY notebook_id HAVING count(*)>4)
 THEN RAISE EXCEPTION 'quota_preflight_required' USING ERRCODE='P0001'; END IF;
END $$;
CREATE INDEX upload_quota_actor ON public.upload_intents(actor_user_id,notebook_id);
CREATE INDEX upload_quota_notebook ON public.upload_intents(notebook_id);
GRANT UPDATE(job_state,job_attempts,job_lease_token,job_lease_expires_at,work_cancelled)
 ON public.upload_intents TO notebook_upload_io_owner;
REVOKE SELECT ON public.upload_intents FROM notebook_app;
GRANT SELECT(id,actor_user_id,workspace_id,notebook_id,document_id,version_id,job_id,
 idempotency_key,filename,sha256,byte_count,media_type,storage_key,state,created_at,
 expires_at,write_token,write_expires_at,stored_at,cleanup_token,cleanup_expires_at,reconcile_at)
 ON public.upload_intents TO notebook_app;

CREATE FUNCTION public.quota_gate() RETURNS void LANGUAGE plpgsql VOLATILE
 SECURITY INVOKER SET search_path=pg_catalog AS $$ BEGIN
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'quota_isolation_unsupported' USING ERRCODE='P0001'; END IF;
 IF NOT pg_try_advisory_xact_lock(184956881,6) THEN
  RAISE EXCEPTION 'quota_gate_busy' USING ERRCODE='55P03'; END IF;
END $$;
REVOKE ALL ON FUNCTION public.quota_gate() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.quota_gate() TO notebook_app,notebook_membership,
 notebook_document_lifecycle,notebook_upload_finalize,notebook_upload_io_owner;

GRANT CREATE ON SCHEMA public TO notebook_upload_io_owner;
CREATE FUNCTION public.quota_before_gate() RETURNS trigger LANGUAGE plpgsql VOLATILE
 SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
 -- Deliberately NO table/capacity/identity query before the caller's RLS checks.
 PERFORM public.quota_gate();
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
ALTER FUNCTION public.quota_before_gate() OWNER TO notebook_upload_io_owner;
REVOKE ALL ON FUNCTION public.quota_before_gate() FROM PUBLIC;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['workspaces','notebooks','app_admissions','workspace_members',
 'documents','document_versions','ingestion_jobs','upload_intents'] LOOP
 EXECUTE format('CREATE TRIGGER a_quota_gate BEFORE INSERT OR UPDATE OR DELETE ON public.%I FOR EACH ROW EXECUTE FUNCTION public.quota_before_gate()',t);
 END LOOP;
END $$;

CREATE FUNCTION public.quota_capacity_check() RETURNS trigger LANGUAGE plpgsql VOLATILE
 SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE t timestamptz;
BEGIN
 -- AFTER RLS/WITH CHECK, fresh VOLATILE statement snapshot after winning gate.
 PERFORM public.quota_gate();
 t:=clock_timestamp();
 IF (SELECT count(*) FROM public.upload_intents i WHERE i.actor_user_id=NEW.actor_user_id
  AND i.notebook_id=NEW.notebook_id AND NOT i.work_cancelled AND
  ((i.state IN ('reserved','stored') AND i.expires_at>t) OR (i.state='finalized' AND i.job_state IN ('queued','running'))))>10
 OR (SELECT count(*) FROM public.upload_intents i WHERE i.notebook_id=NEW.notebook_id AND NOT i.work_cancelled AND
  ((i.state IN ('reserved','stored') AND i.expires_at>t) OR (i.state='finalized' AND i.job_state IN ('queued','running'))))>50
 OR (SELECT count(*) FROM public.upload_intents i WHERE i.actor_user_id=NEW.actor_user_id
  AND i.job_state='running' AND i.job_lease_expires_at>t)>2
 OR (SELECT count(*) FROM public.upload_intents i WHERE i.notebook_id=NEW.notebook_id
  AND i.job_state='running' AND i.job_lease_expires_at>t)>4
 THEN RAISE EXCEPTION 'quota_capacity' USING ERRCODE='P0001'; END IF;
 RETURN NULL;
END $$;
ALTER FUNCTION public.quota_capacity_check() OWNER TO notebook_upload_io_owner;
REVOKE ALL ON FUNCTION public.quota_capacity_check() FROM PUBLIC;
CREATE TRIGGER z_quota_capacity AFTER INSERT OR UPDATE ON public.upload_intents
 FOR EACH ROW EXECUTE FUNCTION public.quota_capacity_check();

CREATE FUNCTION public.quota_job_mirror() RETURNS trigger LANGUAGE plpgsql VOLATILE
 SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i public.upload_intents%ROWTYPE; t timestamptz;
BEGIN
 PERFORM public.quota_gate();
 IF TG_OP='DELETE' THEN
  -- Keep a live lease until expiry even after cascade; never erase occupancy.
  UPDATE public.upload_intents SET work_cancelled=true WHERE job_id=OLD.id;
  RETURN NULL;
 END IF;
 SELECT * INTO i FROM public.upload_intents WHERE job_id=NEW.id OR version_id=NEW.document_version_id;
 IF NOT FOUND THEN
  IF NEW.status IN ('queued','running') THEN
   RAISE EXCEPTION 'quota_legacy_unsupported' USING ERRCODE='P0001'; END IF;
  RETURN NULL;
 END IF;
 IF (NEW.id,NEW.document_version_id,NEW.workspace_id,NEW.notebook_id,NEW.actor_user_id,NEW.idempotency_key)
  IS DISTINCT FROM (i.job_id,i.version_id,i.workspace_id,i.notebook_id,i.actor_user_id,i.idempotency_key)
  OR NEW.connection_id IS NOT NULL OR NEW.index_profile_id IS NOT NULL
 THEN RAISE EXCEPTION 'upload_identity_mismatch' USING ERRCODE='23514'; END IF;
 t:=clock_timestamp();
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'queued' OR NEW.attempts<>0 OR NEW.lease_token IS NOT NULL OR NEW.lease_expires_at IS NOT NULL
  THEN RAISE EXCEPTION 'quota_legacy_unsupported' USING ERRCODE='P0001'; END IF;
 ELSE
  IF (NEW.status,NEW.attempts,NEW.lease_token,NEW.lease_expires_at,NEW.available_at)
   IS NOT DISTINCT FROM (OLD.status,OLD.attempts,OLD.lease_token,OLD.lease_expires_at,OLD.available_at)
  THEN RETURN NULL; END IF;
  IF OLD.status IN ('succeeded','failed','cancelled') OR i.work_cancelled OR i.state<>'finalized'
  OR NEW.available_at IS DISTINCT FROM OLD.available_at
  THEN RAISE EXCEPTION 'quota_legacy_unsupported' USING ERRCODE='P0001'; END IF;
  IF NEW.status='running' THEN
   IF NEW.lease_token IS NULL OR NEW.lease_expires_at IS NULL OR NEW.lease_expires_at<=t
    OR NEW.lease_expires_at>t+interval '300 seconds' THEN
    RAISE EXCEPTION 'quota_legacy_unsupported' USING ERRCODE='P0001'; END IF;
   IF OLD.status='running' AND OLD.lease_expires_at>t THEN
    IF NEW.lease_token IS DISTINCT FROM OLD.lease_token OR NEW.attempts<>OLD.attempts
     OR NEW.lease_expires_at<OLD.lease_expires_at THEN
     RAISE EXCEPTION 'quota_legacy_unsupported' USING ERRCODE='P0001'; END IF;
   ELSE
    IF OLD.status NOT IN ('queued','running') OR OLD.available_at>t
     OR NEW.attempts<>OLD.attempts+1 OR NEW.attempts>5
     OR NEW.lease_token IS NOT DISTINCT FROM OLD.lease_token THEN
     RAISE EXCEPTION 'quota_legacy_unsupported' USING ERRCODE='P0001'; END IF;
   END IF;
  ELSIF NEW.status IN ('succeeded','failed','cancelled') THEN
   IF OLD.status<>'running' OR OLD.lease_expires_at<=t OR NEW.attempts<>OLD.attempts
    OR NEW.lease_token IS NOT NULL OR NEW.lease_expires_at IS NOT NULL THEN
    RAISE EXCEPTION 'quota_legacy_unsupported' USING ERRCODE='P0001'; END IF;
  ELSE RAISE EXCEPTION 'quota_legacy_unsupported' USING ERRCODE='P0001'; END IF;
 END IF;
 UPDATE public.upload_intents SET job_state=NEW.status,job_attempts=NEW.attempts,
  job_lease_token=NEW.lease_token,job_lease_expires_at=NEW.lease_expires_at WHERE id=i.id;
 RETURN NULL;
END $$;
ALTER FUNCTION public.quota_job_mirror() OWNER TO notebook_upload_io_owner;
REVOKE ALL ON FUNCTION public.quota_job_mirror() FROM PUBLIC;
-- Alphabetically AFTER job_processing_status: a quota error rolls back status too.
CREATE TRIGGER z_quota_job_mirror AFTER INSERT OR UPDATE OR DELETE ON public.ingestion_jobs
 FOR EACH ROW EXECUTE FUNCTION public.quota_job_mirror();
CREATE FUNCTION public.quota_cancel_work() RETURNS trigger LANGUAGE plpgsql VOLATILE
 SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
 PERFORM public.quota_gate();
 IF TG_TABLE_NAME='app_admissions' THEN
  UPDATE public.upload_intents SET work_cancelled=true WHERE actor_user_id=OLD.user_id AND NOT work_cancelled;
 ELSIF TG_TABLE_NAME='workspace_members' THEN
  IF TG_OP='DELETE' OR NEW.role NOT IN ('owner','editor') THEN
   UPDATE public.upload_intents SET work_cancelled=true
    WHERE actor_user_id=OLD.user_id AND workspace_id=OLD.workspace_id AND NOT work_cancelled;
  END IF;
 ELSIF TG_TABLE_NAME='notebooks' THEN
  UPDATE public.upload_intents SET work_cancelled=true WHERE notebook_id=OLD.id AND NOT work_cancelled;
 ELSE
  IF TG_OP='DELETE' OR NEW.deleted_at IS NOT NULL OR NEW.current_version_id IS DISTINCT FROM OLD.current_version_id THEN
   UPDATE public.upload_intents SET work_cancelled=true WHERE document_id=OLD.id AND NOT work_cancelled;
  END IF;
 END IF;
 RETURN NULL;
END $$;
ALTER FUNCTION public.quota_cancel_work() OWNER TO notebook_upload_io_owner;
REVOKE ALL ON FUNCTION public.quota_cancel_work() FROM PUBLIC;
CREATE TRIGGER quota_cancel_work AFTER DELETE ON public.app_admissions FOR EACH ROW EXECUTE FUNCTION public.quota_cancel_work();
CREATE TRIGGER quota_cancel_work AFTER DELETE OR UPDATE OF role ON public.workspace_members FOR EACH ROW EXECUTE FUNCTION public.quota_cancel_work();
CREATE TRIGGER quota_cancel_work AFTER DELETE ON public.notebooks FOR EACH ROW EXECUTE FUNCTION public.quota_cancel_work();
CREATE TRIGGER quota_cancel_work AFTER DELETE OR UPDATE OF deleted_at,current_version_id ON public.documents FOR EACH ROW EXECUTE FUNCTION public.quota_cancel_work();
REVOKE CREATE ON SCHEMA public FROM notebook_upload_io_owner;

-- Replace only the fixed, already-installed 0002/0003/0005 RPC bodies. Existing
-- owners/ACLs, signatures, authorization and body text otherwise stay unchanged.
-- This is migration-time transformation, not a runtime dispatcher or query API.
DO $$
DECLARE signature text; definition text; anchor text;
BEGIN
 FOREACH signature IN ARRAY ARRAY[
  'public.notebook_create_invite(uuid,text,text,uuid,text)',
  'public.notebook_accept_invite(text)',
  'public.notebook_change_member(uuid,uuid,text)',
  'public.notebook_delete_document(uuid)',
  'public.finalize_upload(uuid)',
  'public.claim_upload_io(uuid,integer)',
  'public.confirm_upload_io(uuid,uuid,text,integer)',
  'public.list_cleanup(integer)',
  'public.claim_cleanup(uuid,integer)',
  'public.finish_cleanup(uuid,uuid)'] LOOP
  definition:=pg_get_functiondef(signature::regprocedure);
  anchor:=E'BEGIN\n';
  IF signature='public.notebook_create_invite(uuid,text,text,uuid,text)' THEN
   anchor:=$anchor$ IF actor IS NULL OR NOT EXISTS(SELECT 1 FROM public.app_admissions WHERE user_id=actor) THEN RAISE EXCEPTION 'admission_required' USING ERRCODE='42501'; END IF;
$anchor$;
  ELSIF signature='public.notebook_accept_invite(text)' THEN
   anchor:=$anchor$ IF actor IS NULL OR email IS NULL THEN RAISE EXCEPTION 'identity_required' USING ERRCODE='42501'; END IF;
$anchor$;
  END IF;
  IF length(definition)-length(replace(definition,anchor,''))<>length(anchor) THEN
   RAISE EXCEPTION 'quota_preflight_required' USING ERRCODE='P0001'; END IF;
  EXECUTE replace(definition,anchor,anchor||E' PERFORM public.quota_gate();\n');
 END LOOP;
END $$;
