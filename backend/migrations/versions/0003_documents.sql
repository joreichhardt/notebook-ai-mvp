-- No SECURITY DEFINER, runtime role changes, Storage schema or vector claims.
CREATE TABLE public.documents (
 id uuid PRIMARY KEY,
 workspace_id uuid NOT NULL,
 notebook_id uuid NOT NULL,
 created_by uuid NOT NULL,
 filename varchar(255) NOT NULL CHECK (length(filename) BETWEEN 1 AND 255),
 current_version_id uuid,
 deleted_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (id, notebook_id, workspace_id),
 FOREIGN KEY (notebook_id, workspace_id) REFERENCES public.notebooks(id, workspace_id) ON DELETE CASCADE
);
CREATE TABLE public.document_versions (
 id uuid PRIMARY KEY,
 document_id uuid NOT NULL,
 notebook_id uuid NOT NULL,
 workspace_id uuid NOT NULL,
 version integer NOT NULL CHECK (version > 0),
 media_type varchar(32) NOT NULL CHECK (media_type IN ('text/plain','text/markdown','application/pdf')),
 byte_count integer NOT NULL CHECK (byte_count BETWEEN 1 AND 26214400),
 sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
 storage_key text NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (document_id, version),
 UNIQUE (id, document_id, notebook_id, workspace_id),
 UNIQUE (id, notebook_id, workspace_id),
 FOREIGN KEY (document_id, notebook_id, workspace_id) REFERENCES public.documents(id, notebook_id, workspace_id) ON DELETE CASCADE,
 CHECK (storage_key = workspace_id::text || '/' || notebook_id::text || '/' || document_id::text || '/' || id::text || '/original')
);
-- Allows document(current_version_id) followed by its version in one transaction.
ALTER TABLE public.documents ADD CONSTRAINT documents_current_version_fk
 FOREIGN KEY (current_version_id, id, notebook_id, workspace_id)
 REFERENCES public.document_versions(id, document_id, notebook_id, workspace_id)
 DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE public.index_profiles (
 id uuid PRIMARY KEY,
 workspace_id uuid NOT NULL,
 notebook_id uuid NOT NULL,
 provider varchar(64) NOT NULL CHECK (length(provider) > 0),
 model varchar(200) NOT NULL CHECK (length(model) > 0),
 revision varchar(200) NOT NULL CHECK (length(revision) > 0),
 dimensions integer NOT NULL CHECK (dimensions BETWEEN 1 AND 65536),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (id, notebook_id, workspace_id),
 UNIQUE (notebook_id, provider, model, revision, dimensions),
 FOREIGN KEY (notebook_id, workspace_id) REFERENCES public.notebooks(id, workspace_id) ON DELETE CASCADE
);
CREATE TABLE public.chunks (
 id uuid PRIMARY KEY,
 document_version_id uuid NOT NULL,
 document_id uuid NOT NULL,
 notebook_id uuid NOT NULL,
 workspace_id uuid NOT NULL,
 ordinal integer NOT NULL CHECK (ordinal >= 0),
 text text NOT NULL CHECK (length(text) BETWEEN 1 AND 2000000),
 sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
 page integer CHECK (page BETWEEN 1 AND 500),
 start_offset integer NOT NULL CHECK (start_offset >= 0),
 end_offset integer NOT NULL CHECK (end_offset > start_offset AND end_offset <= 2000000),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (document_version_id, ordinal),
 FOREIGN KEY (document_version_id, document_id, notebook_id, workspace_id)
 REFERENCES public.document_versions(id, document_id, notebook_id, workspace_id) ON DELETE CASCADE,
 CHECK (end_offset - start_offset = length(text))
);
CREATE TABLE public.ingestion_jobs (
 id uuid PRIMARY KEY,
 document_version_id uuid NOT NULL,
 notebook_id uuid NOT NULL,
 workspace_id uuid NOT NULL,
 actor_user_id uuid NOT NULL,
 -- Historical identifier intentionally has no FK: disconnect must preserve history.
 connection_id uuid,
 index_profile_id uuid,
 idempotency_key varchar(128) NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
 status varchar(16) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
 available_at timestamptz NOT NULL DEFAULT now(),
 lease_token uuid,
 lease_expires_at timestamptz,
 heartbeat_at timestamptz,
 error_code varchar(64) CHECK (error_code ~ '^[a-z][a-z0-9_]{0,63}$'),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (actor_user_id, notebook_id, idempotency_key),
 FOREIGN KEY (document_version_id, notebook_id, workspace_id)
 REFERENCES public.document_versions(id, notebook_id, workspace_id) ON DELETE CASCADE,
 FOREIGN KEY (index_profile_id, notebook_id, workspace_id)
 REFERENCES public.index_profiles(id, notebook_id, workspace_id) DEFERRABLE INITIALLY DEFERRED,
 CHECK ((connection_id IS NULL) = (index_profile_id IS NULL)),
 CHECK ((status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL AND attempts > 0)
     OR (status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL AND heartbeat_at IS NULL))
);
CREATE INDEX documents_notebook_idx ON public.documents(notebook_id, workspace_id) WHERE deleted_at IS NULL;
CREATE INDEX chunks_scope_idx ON public.chunks(workspace_id, notebook_id, document_version_id);
CREATE INDEX jobs_claim_idx ON public.ingestion_jobs(actor_user_id, available_at, id) WHERE status IN ('queued','running');

CREATE FUNCTION public.notebook_immutable_row() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog AS $$ BEGIN
 RAISE EXCEPTION 'immutable_record' USING ERRCODE='23514';
END $$;
CREATE TRIGGER version_immutable BEFORE UPDATE ON public.document_versions
 FOR EACH ROW EXECUTE FUNCTION public.notebook_immutable_row();
CREATE TRIGGER profile_immutable BEFORE UPDATE ON public.index_profiles
 FOR EACH ROW EXECUTE FUNCTION public.notebook_immutable_row();
CREATE FUNCTION public.notebook_document_identity() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog AS $$ BEGIN
 IF (NEW.id,NEW.workspace_id,NEW.notebook_id,NEW.created_by,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.notebook_id,OLD.created_by,OLD.created_at)
 OR (OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS DISTINCT FROM OLD.deleted_at)
 THEN RAISE EXCEPTION 'immutable_identity' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER document_identity BEFORE UPDATE ON public.documents
 FOR EACH ROW EXECUTE FUNCTION public.notebook_document_identity();
CREATE FUNCTION public.notebook_job_identity() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog AS $$ BEGIN
 IF (NEW.id,NEW.document_version_id,NEW.notebook_id,NEW.workspace_id,NEW.actor_user_id,
     NEW.connection_id,NEW.index_profile_id,NEW.idempotency_key,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.document_version_id,OLD.notebook_id,OLD.workspace_id,OLD.actor_user_id,
     OLD.connection_id,OLD.index_profile_id,OLD.idempotency_key,OLD.created_at)
 OR NEW.attempts < OLD.attempts
 THEN RAISE EXCEPTION 'immutable_identity' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER job_identity BEFORE UPDATE ON public.ingestion_jobs
 FOR EACH ROW EXECUTE FUNCTION public.notebook_job_identity();
REVOKE ALL ON FUNCTION public.notebook_immutable_row(),public.notebook_document_identity(),public.notebook_job_identity() FROM PUBLIC;

-- Policies are invoker-only and use existing nonrecursive membership RLS.
DO $$
DECLARE t text; member text; writer text; visible text; writable text; uid text;
BEGIN
 uid := 'nullif(current_setting(''request.jwt.claim.sub'',true),'''')::uuid';
 FOREACH t IN ARRAY ARRAY['documents','document_versions','chunks','index_profiles','ingestion_jobs'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC',t);
  member := format('EXISTS (SELECT 1 FROM public.workspace_members m WHERE m.workspace_id=%I.workspace_id AND m.user_id=%s) AND EXISTS (SELECT 1 FROM public.app_admissions a WHERE a.user_id=%s)',t,uid,uid);
  writer := member || format(' AND EXISTS (SELECT 1 FROM public.workspace_members m WHERE m.workspace_id=%I.workspace_id AND m.user_id=%s AND m.role IN (''owner'',''editor''))',t,uid);
  visible := member;
  writable := writer;
  IF t='documents' THEN
   visible := visible || ' AND deleted_at IS NULL';
  ELSIF t='document_versions' THEN
   visible := visible || ' AND EXISTS (SELECT 1 FROM public.documents d WHERE d.id=document_id AND d.current_version_id=document_versions.id)';
   writable := writer || ' AND EXISTS (SELECT 1 FROM public.documents d WHERE d.id=document_id AND d.current_version_id=document_versions.id)';
  ELSIF t='chunks' THEN
   visible := visible || ' AND EXISTS (SELECT 1 FROM public.document_versions v WHERE v.id=document_version_id)';
   writable := writer || ' AND EXISTS (SELECT 1 FROM public.document_versions v WHERE v.id=document_version_id)';
  ELSIF t='ingestion_jobs' THEN
   -- Historical job metadata remains private to its actor, including stale/failed work.
   visible := visible || ' AND actor_user_id=' || uid;
   writable := writer || ' AND actor_user_id=' || uid || ' AND EXISTS (SELECT 1 FROM public.document_versions v WHERE v.id=document_version_id)'
    || ' AND (connection_id IS NULL OR EXISTS (SELECT 1 FROM public.user_provider_connections c JOIN public.index_profiles p ON p.id=index_profile_id WHERE c.id=connection_id AND c.user_id=actor_user_id AND c.status=''active'' AND c.provider_id=p.provider))';
  END IF;
  EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO notebook_app USING (%s)',t||'_read',t,visible);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT TO notebook_app WITH CHECK (%s)',t||'_insert',t,writable || CASE WHEN t='documents' THEN ' AND created_by='||uid||' AND deleted_at IS NULL' ELSE '' END);
  IF t NOT IN ('document_versions','index_profiles') THEN
   EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE TO notebook_app USING (%s) WITH CHECK (%s)',t||'_update',t,writable,writable);
   EXECUTE format('GRANT UPDATE ON public.%I TO notebook_app',t);
  END IF;
  EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE TO notebook_app USING (%s)',t||'_delete',t,writable);
  EXECUTE format('GRANT SELECT,INSERT,DELETE ON public.%I TO notebook_app',t);
 END LOOP;
END $$;

-- PostgreSQL also applies SELECT visibility to an UPDATE's new tuple. A soft
-- delete cannot pass the intentionally strict nondeleted SELECT policy. This
-- single-purpose RPC can only tombstone one currently authorized document.
DO $$ BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='notebook_document_lifecycle') THEN
  CREATE ROLE notebook_document_lifecycle NOLOGIN NOSUPERUSER BYPASSRLS NOINHERIT;
 END IF;
END $$;
GRANT USAGE,CREATE ON SCHEMA public TO notebook_document_lifecycle;
GRANT SELECT ON public.documents,public.app_admissions,public.workspace_members TO notebook_document_lifecycle;
GRANT UPDATE(deleted_at) ON public.documents TO notebook_document_lifecycle;
CREATE FUNCTION public.notebook_delete_document(p_document uuid) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=nullif(current_setting('request.jwt.claim.sub',true),'')::uuid;
 affected integer;
BEGIN
 UPDATE public.documents d SET deleted_at=clock_timestamp()
 WHERE d.id=p_document AND d.deleted_at IS NULL
 AND EXISTS (SELECT 1 FROM public.app_admissions a WHERE a.user_id=actor)
 AND EXISTS (SELECT 1 FROM public.workspace_members m WHERE m.workspace_id=d.workspace_id
             AND m.user_id=actor AND m.role IN ('owner','editor'));
 GET DIAGNOSTICS affected = ROW_COUNT;
 RETURN affected=1;
END $$;
ALTER FUNCTION public.notebook_delete_document(uuid) OWNER TO notebook_document_lifecycle;
REVOKE ALL ON FUNCTION public.notebook_delete_document(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.notebook_delete_document(uuid) TO notebook_app;
REVOKE CREATE ON SCHEMA public FROM notebook_document_lifecycle;
