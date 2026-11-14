-- No LOGIN/password, runtime membership, backfill or application table grants.
SET LOCAL lock_timeout='5s';
LOCK TABLE public.upload_intents IN SHARE ROW EXCLUSIVE MODE;
DO $$ BEGIN
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='notebook_text_scheduler') THEN
  CREATE ROLE notebook_text_scheduler NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT;
 END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='notebook_text_dispatch_owner') THEN
  CREATE ROLE notebook_text_dispatch_owner NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT;
 END IF;
 IF EXISTS(SELECT FROM pg_roles WHERE rolname IN ('notebook_text_scheduler','notebook_text_dispatch_owner')
  AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolinherit OR rolcreaterole OR rolcreatedb))
 OR EXISTS(SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid OR r.oid=m.member
  WHERE r.rolname IN ('notebook_text_scheduler','notebook_text_dispatch_owner'))
 THEN RAISE EXCEPTION 'unsafe_text_dispatch_roles'; END IF;
END $$;
GRANT USAGE ON SCHEMA public TO notebook_text_scheduler,notebook_text_dispatch_owner;
GRANT CREATE ON SCHEMA public TO notebook_text_dispatch_owner;
GRANT EXECUTE ON FUNCTION public.quota_gate() TO notebook_text_dispatch_owner;

CREATE TABLE public.text_job_tickets (
 job_id uuid PRIMARY KEY,
 intent_id uuid NOT NULL UNIQUE,
 actor_user_id uuid NOT NULL,
 workspace_id uuid NOT NULL,
 notebook_id uuid NOT NULL,
 document_version_id uuid NOT NULL,
 purpose text NOT NULL CHECK(purpose='text_extract_v1'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 delivery_token uuid,
 delivery_expires_at timestamptz,
 available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 acknowledged_at timestamptz,
 CHECK((delivery_token IS NULL)=(delivery_expires_at IS NULL))
);
ALTER TABLE public.text_job_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.text_job_tickets FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.text_job_tickets FROM PUBLIC,notebook_app,notebook_storage_io,notebook_text_scheduler;
GRANT SELECT,INSERT ON public.text_job_tickets TO notebook_text_dispatch_owner;
GRANT UPDATE(delivery_token,delivery_expires_at,available_at,acknowledged_at)
 ON public.text_job_tickets TO notebook_text_dispatch_owner;
CREATE POLICY text_ticket_scope ON public.text_job_tickets TO notebook_text_dispatch_owner
 USING(purpose='text_extract_v1') WITH CHECK(purpose='text_extract_v1');
CREATE INDEX text_ticket_pending ON public.text_job_tickets(available_at,created_at,job_id)
 WHERE acknowledged_at IS NULL;

-- Read-only definer policies are restricted to canonical finalized text uploads.
-- They do not change notebook_app's actor-private raw-job visibility.
GRANT SELECT(id,job_id,actor_user_id,workspace_id,notebook_id,version_id,document_id,
 media_type,state,work_cancelled,idempotency_key,sha256,byte_count,storage_key)
 ON public.upload_intents TO notebook_text_dispatch_owner;
GRANT SELECT(user_id) ON public.app_admissions TO notebook_text_dispatch_owner;
GRANT SELECT(user_id,workspace_id,role) ON public.workspace_members TO notebook_text_dispatch_owner;
GRANT SELECT(id,current_version_id,workspace_id,notebook_id,created_by,deleted_at)
 ON public.documents TO notebook_text_dispatch_owner;
GRANT SELECT(id,document_id,workspace_id,notebook_id,media_type,sha256,byte_count,storage_key)
 ON public.document_versions TO notebook_text_dispatch_owner;
GRANT SELECT(id,actor_user_id,workspace_id,notebook_id,document_version_id,idempotency_key,
 connection_id,index_profile_id,status,attempts,lease_expires_at,available_at)
 ON public.ingestion_jobs TO notebook_text_dispatch_owner;
CREATE POLICY text_dispatch_upload ON public.upload_intents FOR SELECT TO notebook_text_dispatch_owner
 USING(state='finalized' AND media_type IN ('text/plain','text/markdown'));
CREATE POLICY text_dispatch_admission ON public.app_admissions FOR SELECT TO notebook_text_dispatch_owner
 USING(EXISTS(SELECT 1 FROM public.upload_intents i WHERE i.actor_user_id=app_admissions.user_id));
CREATE POLICY text_dispatch_member ON public.workspace_members FOR SELECT TO notebook_text_dispatch_owner
 USING(EXISTS(SELECT 1 FROM public.upload_intents i WHERE i.actor_user_id=workspace_members.user_id
 AND i.workspace_id=workspace_members.workspace_id));
CREATE POLICY text_dispatch_document ON public.documents FOR SELECT TO notebook_text_dispatch_owner
 USING(EXISTS(SELECT 1 FROM public.upload_intents i WHERE i.document_id=documents.id
 AND i.workspace_id=documents.workspace_id AND i.notebook_id=documents.notebook_id
 AND i.actor_user_id=documents.created_by));
CREATE POLICY text_dispatch_version ON public.document_versions FOR SELECT TO notebook_text_dispatch_owner
 USING(EXISTS(SELECT 1 FROM public.upload_intents i WHERE i.version_id=document_versions.id
 AND i.document_id=document_versions.document_id AND i.workspace_id=document_versions.workspace_id
 AND i.notebook_id=document_versions.notebook_id));
CREATE POLICY text_dispatch_job ON public.ingestion_jobs FOR SELECT TO notebook_text_dispatch_owner
 USING(EXISTS(SELECT 1 FROM public.upload_intents i WHERE i.job_id=ingestion_jobs.id
 AND i.actor_user_id=ingestion_jobs.actor_user_id AND i.workspace_id=ingestion_jobs.workspace_id
 AND i.notebook_id=ingestion_jobs.notebook_id AND i.version_id=ingestion_jobs.document_version_id));

-- Owner-only predicate, never executable by scheduler/application users.
CREATE FUNCTION public.text_dispatch_eligible()
 RETURNS TABLE(job_id uuid,intent_id uuid,actor_user_id uuid,workspace_id uuid,
 notebook_id uuid,document_version_id uuid,job_status text,attempts integer,
 ingestion_expires_at timestamptz,job_available_at timestamptz)
 LANGUAGE sql VOLATILE SET search_path=pg_catalog AS $$
 SELECT j.id,i.id,i.actor_user_id,i.workspace_id,i.notebook_id,i.version_id,
 j.status::text,j.attempts,j.lease_expires_at,j.available_at
 FROM public.upload_intents i
 JOIN public.ingestion_jobs j ON j.id=i.job_id AND j.actor_user_id=i.actor_user_id
 AND j.workspace_id=i.workspace_id AND j.notebook_id=i.notebook_id
 AND j.document_version_id=i.version_id AND j.idempotency_key=i.idempotency_key
 JOIN public.documents d ON d.id=i.document_id AND d.current_version_id=i.version_id
 AND d.workspace_id=i.workspace_id AND d.notebook_id=i.notebook_id AND d.created_by=i.actor_user_id
 JOIN public.document_versions v ON v.id=i.version_id AND v.document_id=d.id
 AND v.workspace_id=i.workspace_id AND v.notebook_id=i.notebook_id
 JOIN public.app_admissions a ON a.user_id=i.actor_user_id
 JOIN public.workspace_members m ON m.user_id=i.actor_user_id AND m.workspace_id=i.workspace_id
 WHERE i.state='finalized' AND NOT i.work_cancelled AND d.deleted_at IS NULL
 AND m.role IN ('owner','editor') AND j.connection_id IS NULL AND j.index_profile_id IS NULL
 AND v.media_type IN ('text/plain','text/markdown') AND v.media_type=i.media_type
 AND v.sha256=i.sha256 AND v.byte_count=i.byte_count AND v.storage_key=i.storage_key
$$;
ALTER FUNCTION public.text_dispatch_eligible() OWNER TO notebook_text_dispatch_owner;
REVOKE ALL ON FUNCTION public.text_dispatch_eligible() FROM PUBLIC;

CREATE FUNCTION public.issue_text_job_ticket() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 -- Only the successful stored->finalized edge, not finalized replay or old rows.
 IF OLD.state='stored' AND NEW.state='finalized' THEN
  INSERT INTO public.text_job_tickets(job_id,intent_id,actor_user_id,workspace_id,
   notebook_id,document_version_id,purpose)
  SELECT e.job_id,e.intent_id,e.actor_user_id,e.workspace_id,e.notebook_id,e.document_version_id,'text_extract_v1'
  FROM public.text_dispatch_eligible() e WHERE e.intent_id=NEW.id
   AND e.job_status='queued' AND e.attempts=0;
  IF NOT FOUND THEN RAISE EXCEPTION 'text_ticket_issuance_failed' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NULL;
END $$;
ALTER FUNCTION public.issue_text_job_ticket() OWNER TO notebook_text_dispatch_owner;
REVOKE ALL ON FUNCTION public.issue_text_job_ticket() FROM PUBLIC;
CREATE TRIGGER issue_text_job_ticket AFTER UPDATE OF state ON public.upload_intents
 FOR EACH ROW EXECUTE FUNCTION public.issue_text_job_ticket();

CREATE FUNCTION public.claim_text_job(p_seconds integer DEFAULT 60)
 RETURNS TABLE(job_id uuid,actor_user_id uuid,workspace_id uuid,notebook_id uuid,
 document_version_id uuid,purpose text,delivery_token uuid)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 300 THEN RAISE EXCEPTION 'invalid_lease'; END IF;
 -- Gate first; no source/job/ledger writes or reversed source/job lock order.
 PERFORM public.quota_gate();
 RETURN QUERY WITH candidate AS (
  SELECT t.job_id FROM public.text_job_tickets t
  JOIN public.text_dispatch_eligible() e ON e.job_id=t.job_id
  WHERE t.acknowledged_at IS NULL AND t.available_at<=clock_timestamp()
   AND (t.delivery_expires_at IS NULL OR t.delivery_expires_at<=clock_timestamp())
   AND (e.job_status IN ('succeeded','failed','cancelled') OR
    (e.attempts<5 AND e.job_available_at<=clock_timestamp() AND
     (e.job_status='queued' OR (e.job_status='running' AND e.ingestion_expires_at<=clock_timestamp()))))
  ORDER BY t.available_at,t.created_at,t.job_id LIMIT 1 FOR UPDATE OF t SKIP LOCKED
 ) UPDATE public.text_job_tickets t SET delivery_token=gen_random_uuid(),
 delivery_expires_at=clock_timestamp()+make_interval(secs=>p_seconds),
 available_at=clock_timestamp()+make_interval(secs=>p_seconds)+interval '5 seconds'
 FROM candidate c WHERE t.job_id=c.job_id
 RETURNING t.job_id,t.actor_user_id,t.workspace_id,t.notebook_id,t.document_version_id,t.purpose,t.delivery_token;
END $$;

CREATE FUNCTION public.validate_text_job(p_job uuid,p_actor uuid,p_workspace uuid,p_notebook uuid,
 p_version uuid,p_purpose text,p_token uuid) RETURNS text
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result text;
BEGIN
 PERFORM public.quota_gate();
 SELECT CASE WHEN e.job_status IN ('succeeded','failed','cancelled') THEN e.job_status
  WHEN e.attempts<5 AND e.job_available_at<=clock_timestamp() AND
   (e.job_status='queued' OR (e.job_status='running' AND e.ingestion_expires_at<=clock_timestamp()))
  THEN 'executable' ELSE NULL END INTO result
 FROM public.text_job_tickets t JOIN public.text_dispatch_eligible() e ON e.job_id=t.job_id
 WHERE (t.job_id,t.actor_user_id,t.workspace_id,t.notebook_id,t.document_version_id,t.purpose,t.delivery_token)
  =(p_job,p_actor,p_workspace,p_notebook,p_version,p_purpose,p_token)
 AND t.acknowledged_at IS NULL AND t.delivery_expires_at>clock_timestamp();
 RETURN result;
END $$;

CREATE FUNCTION public.renew_text_job(p_job uuid,p_actor uuid,p_workspace uuid,p_notebook uuid,
 p_version uuid,p_purpose text,p_token uuid,p_seconds integer DEFAULT 60)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 300 THEN RAISE EXCEPTION 'invalid_lease'; END IF;
 PERFORM public.quota_gate();
 UPDATE public.text_job_tickets t SET
 delivery_expires_at=greatest(t.delivery_expires_at,clock_timestamp()+make_interval(secs=>p_seconds)),
 available_at=greatest(t.delivery_expires_at,clock_timestamp()+make_interval(secs=>p_seconds))+interval '5 seconds'
 WHERE (t.job_id,t.actor_user_id,t.workspace_id,t.notebook_id,t.document_version_id,t.purpose,t.delivery_token)
  =(p_job,p_actor,p_workspace,p_notebook,p_version,p_purpose,p_token) AND t.acknowledged_at IS NULL
 AND t.delivery_expires_at>clock_timestamp()
 AND EXISTS(SELECT 1 FROM public.text_dispatch_eligible() e WHERE e.job_id=t.job_id);
 RETURN FOUND;
END $$;

CREATE FUNCTION public.ack_text_job(p_job uuid,p_actor uuid,p_workspace uuid,p_notebook uuid,
 p_version uuid,p_purpose text,p_token uuid) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM public.quota_gate();
 UPDATE public.text_job_tickets t SET acknowledged_at=clock_timestamp()
 WHERE (t.job_id,t.actor_user_id,t.workspace_id,t.notebook_id,t.document_version_id,t.purpose,t.delivery_token)
  =(p_job,p_actor,p_workspace,p_notebook,p_version,p_purpose,p_token) AND t.acknowledged_at IS NULL
 AND t.delivery_expires_at>clock_timestamp()
 AND EXISTS(SELECT 1 FROM public.text_dispatch_eligible() e WHERE e.job_id=t.job_id
 AND e.job_status IN ('succeeded','failed','cancelled'));
 RETURN FOUND;
END $$;
ALTER FUNCTION public.claim_text_job(integer) OWNER TO notebook_text_dispatch_owner;
ALTER FUNCTION public.validate_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid) OWNER TO notebook_text_dispatch_owner;
ALTER FUNCTION public.renew_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid,integer) OWNER TO notebook_text_dispatch_owner;
ALTER FUNCTION public.ack_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid) OWNER TO notebook_text_dispatch_owner;
REVOKE ALL ON FUNCTION public.claim_text_job(integer),
 public.validate_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid),
 public.renew_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid,integer),public.ack_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.claim_text_job(integer),
 public.validate_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid),
 public.renew_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid,integer),public.ack_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid) TO notebook_text_scheduler;
REVOKE CREATE ON SCHEMA public FROM notebook_text_dispatch_owner;
