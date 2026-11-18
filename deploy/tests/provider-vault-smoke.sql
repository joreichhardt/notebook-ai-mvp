-- Only against a disposable, extension-capable PostgreSQL instance.
\set ON_ERROR_STOP on
SET ROLE notebook_app;
BEGIN;
SET LOCAL request.jwt.claim.sub = '10000000-0000-4000-8000-000000000001';
SELECT notebook_provider.create_key('20000000-0000-4000-8000-000000000001', 'synthetic-vault-fixture') AS probe \gset
SELECT 1 / (coalesce(notebook_provider.read_key('20000000-0000-4000-8000-000000000001', :'probe') = 'synthetic-vault-fixture', false)::int);
SET LOCAL request.jwt.claim.sub = '10000000-0000-4000-8000-000000000002';
SELECT 1 / ((notebook_provider.read_key('20000000-0000-4000-8000-000000000001', :'probe') IS NULL AND NOT notebook_provider.delete_key('20000000-0000-4000-8000-000000000001', :'probe'))::int);
SET LOCAL request.jwt.claim.sub = '10000000-0000-4000-8000-000000000001';
SELECT 1 / (notebook_provider.delete_key('20000000-0000-4000-8000-000000000001', :'probe')::int);
SELECT 1 / ((notebook_provider.read_key('20000000-0000-4000-8000-000000000001', :'probe') IS NULL)::int);
ROLLBACK;
RESET ROLE;
SELECT 1 / ((NOT has_schema_privilege('anon','notebook_provider','USAGE')
    AND NOT has_function_privilege('authenticated','notebook_provider.read_key(uuid,uuid)','EXECUTE')
    AND NOT has_table_privilege('notebook_app','vault.decrypted_secrets','SELECT'))::int);
