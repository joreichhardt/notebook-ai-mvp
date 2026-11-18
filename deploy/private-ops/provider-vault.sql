-- Operator-installed in the target database by the Vault extension owner.
-- Not a general application migration: plain PostgreSQL CI and Login do not need Vault.
BEGIN;
DO $prerequisite$
BEGIN
    IF current_setting('vault.getkey_script', true) IS NULL OR NOT (
        'supabase_vault' = ANY (regexp_split_to_array(
            current_setting('shared_preload_libraries'), '\s*,\s*'
        ))
    ) THEN
        RAISE EXCEPTION 'Configure and verify the persistent Vault server key before installing';
    END IF;
END;
$prerequisite$;
CREATE EXTENSION IF NOT EXISTS supabase_vault VERSION '0.3.1';
DO $permissions$
BEGIN
    IF (SELECT extversion FROM pg_extension WHERE extname = 'supabase_vault') <> '0.3.1'
        OR EXISTS (
            SELECT FROM pg_roles r
            WHERE r.rolname IN ('notebook_app', 'anon', 'authenticated', 'authenticator')
              AND has_any_column_privilege(r.oid, 'vault.decrypted_secrets', 'SELECT')
        ) THEN
        RAISE EXCEPTION 'Unsupported Vault version or direct Vault access';
    END IF;
END;
$permissions$;
CREATE ROLE notebook_provider_vault NOLOGIN NOINHERIT;
CREATE SCHEMA notebook_provider AUTHORIZATION notebook_provider_vault;
REVOKE ALL ON SCHEMA notebook_provider FROM PUBLIC;
GRANT USAGE ON SCHEMA notebook_provider TO notebook_app;
GRANT USAGE ON SCHEMA vault TO notebook_provider_vault;
GRANT EXECUTE ON FUNCTION vault.create_secret(text, text, text, uuid) TO notebook_provider_vault;
GRANT EXECUTE ON FUNCTION vault._crypto_aead_det_decrypt(bytea, bytea, bigint, bytea, bytea)
    TO notebook_provider_vault;
GRANT SELECT (id, name, decrypted_secret) ON vault.decrypted_secrets TO notebook_provider_vault;
GRANT SELECT (id, name), DELETE ON vault.secrets TO notebook_provider_vault;

SET LOCAL ROLE notebook_provider_vault;
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE FUNCTION notebook_provider.secret_name(connection_id uuid)
RETURNS text LANGUAGE sql STABLE SET search_path = pg_catalog
RETURN 'notebook-provider:' || nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
       || ':' || connection_id;

CREATE FUNCTION notebook_provider.create_key(connection_id uuid, key_value text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $function$
DECLARE scoped_name text := notebook_provider.secret_name(connection_id);
BEGIN
    IF scoped_name IS NULL OR key_value IS NULL OR length(key_value) NOT BETWEEN 1 AND 16384 THEN
        RAISE EXCEPTION 'Invalid provider key operation' USING ERRCODE = '22023';
    END IF;
    RETURN vault.create_secret(key_value, scoped_name, '', NULL);
END;
$function$;

CREATE FUNCTION notebook_provider.read_key(connection_id uuid, key_id uuid)
RETURNS text LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog
RETURN (SELECT decrypted_secret FROM vault.decrypted_secrets
        WHERE id = read_key.key_id AND name = notebook_provider.secret_name(read_key.connection_id));

CREATE FUNCTION notebook_provider.delete_key(connection_id uuid, key_id uuid)
RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog
AS $function$
    WITH removed AS (
        DELETE FROM vault.secrets WHERE id = delete_key.key_id
        AND name = notebook_provider.secret_name(delete_key.connection_id) RETURNING id
    )
    SELECT EXISTS (SELECT FROM removed);
$function$;

RESET ROLE;
-- Session startup defaults for the direct API login, scoped to this database.
DO $logging$
BEGIN
    EXECUTE format('ALTER ROLE notebook_app IN DATABASE %I SET log_parameter_max_length TO 0', current_database());
    EXECUTE format('ALTER ROLE notebook_app IN DATABASE %I SET log_parameter_max_length_on_error TO 0', current_database());
END;
$logging$;
GRANT EXECUTE ON FUNCTION notebook_provider.create_key(uuid, text),
    notebook_provider.read_key(uuid, uuid), notebook_provider.delete_key(uuid, uuid) TO notebook_app;
COMMIT;
