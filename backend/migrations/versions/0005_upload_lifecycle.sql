-- Immutable server identity and durable keys intentionally have NO parent FK.
-- Deleting an application parent must never erase unreconciled external I/O.
CREATE TABLE public.upload_intents (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 actor_user_id uuid NOT NULL DEFAULT nullif(current_setting('request.jwt.claim.sub',true),'')::uuid,
 workspace_id uuid NOT NULL,
 notebook_id uuid NOT NULL,
 document_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
 version_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
 job_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
 idempotency_key varchar(128) NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 filename varchar(255) NOT NULL CHECK(length(filename) BETWEEN 1 AND 255),
 sha256 varchar(64) NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 byte_count integer NOT NULL CHECK(byte_count BETWEEN 1 AND 26214400),
 media_type varchar(32) NOT NULL CHECK(media_type IN ('text/plain','text/markdown')),
 storage_key text GENERATED ALWAYS AS
  (workspace_id::text||'/'||notebook_id::text||'/'||document_id::text||'/'||version_id::text||'/original') STORED NOT NULL UNIQUE,
 state varchar(20) NOT NULL DEFAULT 'reserved'
  CHECK(state IN ('reserved','stored','finalized','cleanup_pending','deleting','cleaned')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '15 minutes',
 write_token uuid,
 write_expires_at timestamptz,
 stored_at timestamptz,
 cleanup_token uuid,
 cleanup_expires_at timestamptz,
 reconcile_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(actor_user_id,notebook_id,idempotency_key)
);
CREATE INDEX upload_expiry_idx ON public.upload_intents(expires_at,id) WHERE state IN ('reserved','stored');
CREATE INDEX upload_cleanup_idx ON public.upload_intents(reconcile_at,id) WHERE state IN ('cleanup_pending','deleting','cleaned');
CREATE INDEX upload_actor_idx ON public.upload_intents(actor_user_id,workspace_id) WHERE state IN ('reserved','stored');
ALTER TABLE public.upload_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.upload_intents FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.upload_intents FROM PUBLIC;
CREATE POLICY upload_read ON public.upload_intents FOR SELECT TO notebook_app USING (
 actor_user_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid
 AND EXISTS(SELECT 1 FROM public.notebooks n WHERE n.id=notebook_id AND n.workspace_id=upload_intents.workspace_id));
CREATE POLICY upload_reserve ON public.upload_intents FOR INSERT TO notebook_app WITH CHECK (
 actor_user_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid
 AND EXISTS(SELECT 1 FROM public.notebooks n WHERE n.id=notebook_id AND n.workspace_id=upload_intents.workspace_id)
 AND EXISTS(SELECT 1 FROM public.workspace_members m WHERE m.workspace_id=upload_intents.workspace_id
 AND m.user_id=actor_user_id AND m.role IN ('owner','editor')));
GRANT SELECT ON public.upload_intents TO notebook_app;
GRANT INSERT(workspace_id,notebook_id,idempotency_key,filename,sha256,byte_count,media_type)
 ON public.upload_intents TO notebook_app;
-- No UPDATE/DELETE, lifecycle fields, supplied UUIDs, receipt or lease grants.

DO $$ BEGIN
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='notebook_storage_io') THEN
  CREATE ROLE notebook_storage_io NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT;
 END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='notebook_upload_io_owner') THEN
  CREATE ROLE notebook_upload_io_owner NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT;
 END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='notebook_upload_finalize') THEN
  CREATE ROLE notebook_upload_finalize NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT;
 END IF;
END $$;
GRANT USAGE ON SCHEMA public TO notebook_storage_io,notebook_upload_io_owner,notebook_upload_finalize;
GRANT CREATE ON SCHEMA public TO notebook_upload_io_owner,notebook_upload_finalize;
GRANT SELECT ON public.upload_intents TO notebook_upload_io_owner,notebook_upload_finalize;
GRANT UPDATE(state,write_token,write_expires_at,stored_at,cleanup_token,cleanup_expires_at,reconcile_at)
 ON public.upload_intents TO notebook_upload_io_owner;
GRANT UPDATE(state) ON public.upload_intents TO notebook_upload_finalize;
CREATE POLICY upload_io ON public.upload_intents TO notebook_upload_io_owner USING(true) WITH CHECK(true);
CREATE POLICY upload_finalize_read ON public.upload_intents FOR SELECT TO notebook_upload_finalize
 USING(actor_user_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid);
CREATE POLICY upload_finalize_write ON public.upload_intents FOR UPDATE TO notebook_upload_finalize
 USING(actor_user_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid)
 WITH CHECK(actor_user_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid);

CREATE FUNCTION public.claim_upload_io(p_intent uuid,p_seconds integer DEFAULT 60)
 RETURNS TABLE(intent_id uuid,token uuid,storage_key text,sha256 varchar,byte_count integer,media_type varchar)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 300 THEN RAISE EXCEPTION 'invalid_lease'; END IF;
 RETURN QUERY UPDATE public.upload_intents i SET write_token=gen_random_uuid(),
 write_expires_at=clock_timestamp()+make_interval(secs=>p_seconds)
 WHERE i.id=p_intent AND i.state='reserved' AND i.write_token IS NULL
 AND i.expires_at>clock_timestamp()
 RETURNING i.id,i.write_token,i.storage_key,i.sha256,i.byte_count,i.media_type;
END $$;
CREATE FUNCTION public.confirm_upload_io(p_intent uuid,p_token uuid,p_hash text,p_bytes integer)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE affected integer;
BEGIN
 UPDATE public.upload_intents SET state='stored',stored_at=clock_timestamp()
 WHERE id=p_intent AND state='reserved' AND write_token=p_token
 AND write_expires_at>clock_timestamp() AND expires_at>clock_timestamp()
 AND sha256=p_hash AND byte_count=p_bytes;
 GET DIAGNOSTICS affected=ROW_COUNT;
 RETURN affected=1;
END $$;
ALTER FUNCTION public.claim_upload_io(uuid,integer) OWNER TO notebook_upload_io_owner;
ALTER FUNCTION public.confirm_upload_io(uuid,uuid,text,integer) OWNER TO notebook_upload_io_owner;
REVOKE ALL ON FUNCTION public.claim_upload_io(uuid,integer),public.confirm_upload_io(uuid,uuid,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.claim_upload_io(uuid,integer),public.confirm_upload_io(uuid,uuid,text,integer) TO notebook_storage_io;

-- SELECT-only verification owner. Policies expose only the current actor's
-- admission, membership, source and job; no provider/secret or generic query API.
GRANT SELECT ON public.app_admissions,public.workspace_members,public.notebooks,
 public.documents,public.document_versions,public.ingestion_jobs TO notebook_upload_finalize;
CREATE POLICY finalize_admission ON public.app_admissions FOR SELECT TO notebook_upload_finalize
 USING(user_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid);
CREATE POLICY finalize_membership ON public.workspace_members FOR SELECT TO notebook_upload_finalize
 USING(user_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid
 AND EXISTS(SELECT 1 FROM public.app_admissions a WHERE a.user_id=workspace_members.user_id));
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['notebooks','documents','document_versions','ingestion_jobs'] LOOP
 EXECUTE format('CREATE POLICY finalize_verify ON public.%I FOR SELECT TO notebook_upload_finalize USING (
 EXISTS(SELECT 1 FROM public.workspace_members m WHERE m.workspace_id=%I.workspace_id
 AND m.user_id=nullif(current_setting(''request.jwt.claim.sub'',true),'''')::uuid AND m.role IN (''owner'',''editor''))
 AND EXISTS(SELECT 1 FROM public.upload_intents i WHERE i.notebook_id=%s AND %s
 AND i.actor_user_id=nullif(current_setting(''request.jwt.claim.sub'',true),'''')::uuid))',
 t,t,CASE WHEN t='notebooks' THEN 'notebooks.id' ELSE quote_ident(t)||'.notebook_id' END,
 CASE t WHEN 'notebooks' THEN 'i.notebook_id=notebooks.id'
 WHEN 'documents' THEN 'i.document_id=documents.id AND documents.created_by=i.actor_user_id'
 WHEN 'document_versions' THEN 'i.version_id=document_versions.id'
 ELSE 'i.job_id=ingestion_jobs.id AND ingestion_jobs.actor_user_id=i.actor_user_id' END) ;
 END LOOP;
END $$;

CREATE FUNCTION public.finalize_upload(p_intent uuid) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i public.upload_intents%ROWTYPE;
BEGIN
 SELECT * INTO i FROM public.upload_intents WHERE id=p_intent FOR UPDATE;
 IF NOT FOUND OR i.state NOT IN ('stored','finalized') OR i.stored_at IS NULL
 OR (i.state='stored' AND (i.expires_at<=clock_timestamp() OR i.write_expires_at<=clock_timestamp()))
 THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.notebooks n WHERE n.id=i.notebook_id AND n.workspace_id=i.workspace_id)
 OR NOT EXISTS(SELECT 1 FROM public.app_admissions a WHERE a.user_id=i.actor_user_id)
 OR NOT EXISTS(SELECT 1 FROM public.workspace_members m WHERE m.workspace_id=i.workspace_id
 AND m.user_id=i.actor_user_id AND m.role IN ('owner','editor')) THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.documents d JOIN public.document_versions v ON v.id=d.current_version_id
 JOIN public.ingestion_jobs j ON j.document_version_id=v.id
 WHERE d.id=i.document_id AND d.notebook_id=i.notebook_id AND d.workspace_id=i.workspace_id
 AND d.created_by=i.actor_user_id AND d.filename=i.filename AND d.deleted_at IS NULL
 AND v.id=i.version_id AND v.document_id=i.document_id AND v.notebook_id=i.notebook_id
 AND v.workspace_id=i.workspace_id AND v.version=1 AND v.sha256=i.sha256
 AND v.byte_count=i.byte_count AND v.media_type=i.media_type AND v.storage_key=i.storage_key
 AND j.id=i.job_id AND j.actor_user_id=i.actor_user_id AND j.notebook_id=i.notebook_id
 AND j.workspace_id=i.workspace_id AND j.idempotency_key=i.idempotency_key
 AND j.connection_id IS NULL AND j.index_profile_id IS NULL
 AND (i.state='finalized' OR (j.status='queued' AND j.attempts=0))) THEN RETURN false; END IF;
 UPDATE public.upload_intents SET state='finalized' WHERE id=i.id;
 RETURN true;
END $$;
ALTER FUNCTION public.finalize_upload(uuid) OWNER TO notebook_upload_finalize;
REVOKE ALL ON FUNCTION public.finalize_upload(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.finalize_upload(uuid) TO notebook_app;

-- Deferrable commit guard is ledger-only; it cannot see arbitrary application
-- data. Upload-bound IDs cannot become committed current/runnable rows without
-- the independently verified receipt/finalization, even if Python ignores false.
CREATE FUNCTION public.upload_commit_guard() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE i public.upload_intents%ROWTYPE;
BEGIN
 SELECT * INTO i FROM public.upload_intents WHERE document_id=NEW.id OR version_id=NEW.id OR job_id=NEW.id;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF i.state<>'finalized' THEN
 RAISE EXCEPTION 'upload_not_finalized' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='documents' THEN
  IF (NEW.id,NEW.workspace_id,NEW.notebook_id,NEW.created_by,NEW.filename,NEW.current_version_id)
   IS DISTINCT FROM (i.document_id,i.workspace_id,i.notebook_id,i.actor_user_id,i.filename,i.version_id)
   OR NEW.deleted_at IS NOT NULL THEN RAISE EXCEPTION 'upload_identity_mismatch' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='document_versions' THEN
  IF (NEW.id,NEW.document_id,NEW.workspace_id,NEW.notebook_id,NEW.version,NEW.sha256,NEW.byte_count,NEW.media_type,NEW.storage_key)
   IS DISTINCT FROM (i.version_id,i.document_id,i.workspace_id,i.notebook_id,1,i.sha256,i.byte_count,i.media_type,i.storage_key)
   THEN RAISE EXCEPTION 'upload_identity_mismatch' USING ERRCODE='23514'; END IF;
 ELSE
  IF (NEW.id,NEW.document_version_id,NEW.workspace_id,NEW.notebook_id,NEW.actor_user_id,NEW.idempotency_key)
   IS DISTINCT FROM (i.job_id,i.version_id,i.workspace_id,i.notebook_id,i.actor_user_id,i.idempotency_key)
   OR NEW.connection_id IS NOT NULL OR NEW.index_profile_id IS NOT NULL
   THEN RAISE EXCEPTION 'upload_identity_mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NULL;
END $$;
ALTER FUNCTION public.upload_commit_guard() OWNER TO notebook_upload_io_owner;
REVOKE ALL ON FUNCTION public.upload_commit_guard() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER upload_document_guard AFTER INSERT ON public.documents
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.upload_commit_guard();
CREATE CONSTRAINT TRIGGER upload_version_guard AFTER INSERT ON public.document_versions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.upload_commit_guard();
CREATE CONSTRAINT TRIGGER upload_job_guard AFTER INSERT ON public.ingestion_jobs
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.upload_commit_guard();

-- Metadata is mutable, original identity/content is not. Existing unknown work
-- is explicitly failed/not_processed, not fabricated ready.
ALTER TABLE public.document_versions ADD COLUMN processing_status varchar(16) NOT NULL DEFAULT 'failed'
 CHECK(processing_status IN ('queued','processing','ready','failed'));
ALTER TABLE public.document_versions ADD COLUMN error_code varchar(64) DEFAULT 'not_processed'
 CHECK(error_code IN ('not_processed','processing_failed','processing_cancelled'));
DROP TRIGGER version_immutable ON public.document_versions;
CREATE FUNCTION public.version_content_immutable() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog AS $$ BEGIN
 IF (to_jsonb(NEW)-'processing_status'-'error_code') IS DISTINCT FROM
 (to_jsonb(OLD)-'processing_status'-'error_code') THEN
 RAISE EXCEPTION 'immutable_record' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER version_immutable BEFORE UPDATE ON public.document_versions
 FOR EACH ROW EXECUTE FUNCTION public.version_content_immutable();
REVOKE ALL ON FUNCTION public.version_content_immutable() FROM PUBLIC;
GRANT UPDATE(processing_status,error_code) ON public.document_versions TO notebook_app;
CREATE POLICY version_status_write ON public.document_versions FOR UPDATE TO notebook_app
 USING(EXISTS(SELECT 1 FROM public.workspace_members m WHERE m.workspace_id=document_versions.workspace_id
 AND m.user_id=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid AND m.role IN ('owner','editor'))
 AND EXISTS(SELECT 1 FROM public.documents d WHERE d.id=document_id AND d.current_version_id=document_versions.id))
 WITH CHECK(EXISTS(SELECT 1 FROM public.documents d WHERE d.id=document_id AND d.current_version_id=document_versions.id));
CREATE FUNCTION public.job_processing_status() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 UPDATE public.document_versions SET processing_status=CASE NEW.status
 WHEN 'queued' THEN 'queued' WHEN 'running' THEN 'processing' WHEN 'succeeded' THEN 'ready' ELSE 'failed' END,
 error_code=CASE NEW.status WHEN 'failed' THEN 'processing_failed' WHEN 'cancelled' THEN 'processing_cancelled' ELSE NULL END
 WHERE id=NEW.document_version_id;
 RETURN NEW;
END $$;
CREATE TRIGGER job_processing_status AFTER INSERT OR UPDATE OF status ON public.ingestion_jobs
 FOR EACH ROW EXECUTE FUNCTION public.job_processing_status();
REVOKE ALL ON FUNCTION public.job_processing_status() FROM PUBLIC;
-- Trigger arguments are OLD/NEW tuples from fixed tables, never user-selected
-- SQL. Owner can only update lifecycle columns on ledger, not application data.
CREATE FUNCTION public.upload_source_cleanup() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' OR NEW.deleted_at IS NOT NULL
 OR NEW.current_version_id IS DISTINCT FROM OLD.current_version_id THEN
 UPDATE public.upload_intents SET state='cleanup_pending',cleanup_token=NULL,cleanup_expires_at=NULL,
 reconcile_at=greatest(clock_timestamp(),coalesce(write_expires_at,clock_timestamp()))+interval '5 minutes'
 WHERE document_id=OLD.id AND state IN ('reserved','stored','finalized');
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER upload_source_cleanup AFTER DELETE OR UPDATE OF deleted_at,current_version_id ON public.documents
 FOR EACH ROW EXECUTE FUNCTION public.upload_source_cleanup();
CREATE FUNCTION public.upload_parent_cleanup() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 UPDATE public.upload_intents SET state='cleanup_pending',cleanup_token=NULL,cleanup_expires_at=NULL,
 reconcile_at=greatest(clock_timestamp(),coalesce(write_expires_at,clock_timestamp()))+interval '5 minutes'
 WHERE notebook_id=OLD.id AND state IN ('reserved','stored','finalized');
 RETURN NULL;
END $$;
CREATE TRIGGER upload_parent_cleanup AFTER DELETE ON public.notebooks
 FOR EACH ROW EXECUTE FUNCTION public.upload_parent_cleanup();
CREATE FUNCTION public.upload_actor_cleanup() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_TABLE_NAME='app_admissions' THEN
 UPDATE public.upload_intents SET state='cleanup_pending',
 reconcile_at=greatest(clock_timestamp(),coalesce(write_expires_at,clock_timestamp()))+interval '5 minutes'
 WHERE actor_user_id=OLD.user_id AND state IN ('reserved','stored');
 ELSIF TG_OP='DELETE' OR NEW.role NOT IN ('owner','editor') THEN
 UPDATE public.upload_intents SET state='cleanup_pending',
 reconcile_at=greatest(clock_timestamp(),coalesce(write_expires_at,clock_timestamp()))+interval '5 minutes'
 WHERE actor_user_id=OLD.user_id AND workspace_id=OLD.workspace_id AND state IN ('reserved','stored');
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER upload_admission_cleanup AFTER DELETE ON public.app_admissions
 FOR EACH ROW EXECUTE FUNCTION public.upload_actor_cleanup();
CREATE TRIGGER upload_membership_cleanup AFTER DELETE OR UPDATE OF role ON public.workspace_members
 FOR EACH ROW EXECUTE FUNCTION public.upload_actor_cleanup();
ALTER FUNCTION public.upload_source_cleanup() OWNER TO notebook_upload_io_owner;
ALTER FUNCTION public.upload_parent_cleanup() OWNER TO notebook_upload_io_owner;
ALTER FUNCTION public.upload_actor_cleanup() OWNER TO notebook_upload_io_owner;
REVOKE ALL ON FUNCTION public.upload_source_cleanup(),public.upload_parent_cleanup(),public.upload_actor_cleanup() FROM PUBLIC;

-- Only metadata IDs are listed. Active finalized sources never qualify.
-- Cleaned tombstones remain periodically discoverable: DELETE/timeout is not a
-- proof that a previously submitted remote PUT cannot materialize later.
CREATE FUNCTION public.list_cleanup(p_limit integer DEFAULT 100) RETURNS SETOF uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'invalid_limit'; END IF;
 WITH expired AS (SELECT id FROM public.upload_intents
 WHERE state IN ('reserved','stored') AND expires_at<=clock_timestamp()
 ORDER BY expires_at,id LIMIT p_limit FOR UPDATE SKIP LOCKED)
 UPDATE public.upload_intents i SET state='cleanup_pending',
 reconcile_at=greatest(clock_timestamp(),coalesce(write_expires_at,clock_timestamp()))+interval '5 minutes'
 FROM expired e WHERE i.id=e.id;
 RETURN QUERY SELECT id FROM public.upload_intents
 WHERE state IN ('cleanup_pending','deleting','cleaned') AND reconcile_at<=clock_timestamp()
 AND (cleanup_expires_at IS NULL OR cleanup_expires_at<=clock_timestamp())
 ORDER BY reconcile_at,id LIMIT p_limit;
END $$;
CREATE FUNCTION public.claim_cleanup(p_intent uuid,p_seconds integer DEFAULT 60)
 RETURNS TABLE(intent_id uuid,token uuid,storage_key text)
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 300 THEN RAISE EXCEPTION 'invalid_lease'; END IF;
 RETURN QUERY UPDATE public.upload_intents i SET state='deleting',cleanup_token=gen_random_uuid(),
 cleanup_expires_at=clock_timestamp()+make_interval(secs=>p_seconds)
 WHERE i.id=p_intent AND i.state IN ('cleanup_pending','deleting','cleaned')
 AND i.reconcile_at<=clock_timestamp()
 AND (i.cleanup_expires_at IS NULL OR i.cleanup_expires_at<=clock_timestamp())
 RETURNING i.id,i.cleanup_token,i.storage_key;
END $$;
CREATE FUNCTION public.finish_cleanup(p_intent uuid,p_token uuid) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE affected integer;
BEGIN
 UPDATE public.upload_intents SET state='cleaned',cleanup_token=NULL,cleanup_expires_at=NULL,
 reconcile_at=clock_timestamp()+interval '1 hour'
 WHERE id=p_intent AND state='deleting' AND cleanup_token=p_token AND cleanup_expires_at>clock_timestamp();
 GET DIAGNOSTICS affected=ROW_COUNT;
 RETURN affected=1;
END $$;
ALTER FUNCTION public.list_cleanup(integer) OWNER TO notebook_upload_io_owner;
ALTER FUNCTION public.claim_cleanup(uuid,integer) OWNER TO notebook_upload_io_owner;
ALTER FUNCTION public.finish_cleanup(uuid,uuid) OWNER TO notebook_upload_io_owner;
REVOKE ALL ON FUNCTION public.list_cleanup(integer),public.claim_cleanup(uuid,integer),public.finish_cleanup(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.list_cleanup(integer),public.claim_cleanup(uuid,integer),public.finish_cleanup(uuid,uuid) TO notebook_storage_io;
REVOKE CREATE ON SCHEMA public FROM notebook_upload_io_owner,notebook_upload_finalize;
