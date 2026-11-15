#!/bin/sh
# Entire upstream init directory is replaced in this minimal profile.
# The auth user owns only its own schema; API role comes from existing Alembic.
set -eu
umask 077
NOTEBOOK_AUTH_DATABASE_URL=$(cat /run/secrets/notebook_auth_database_url)
export NOTEBOOK_AUTH_DATABASE_URL
if ! psql --no-psqlrc --host /tmp --username postgres --dbname postgres --set ON_ERROR_STOP=1 >/dev/null 2>&1 <<'SQL'
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
\getenv auth_url NOTEBOOK_AUTH_DATABASE_URL
SELECT :'auth_url' ~ '^postgres://supabase_auth_admin:[A-Za-z0-9_-]{48}@db:5432/postgres$' AS valid \gset
\if :valid
SELECT format('CREATE ROLE supabase_auth_admin LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L', split_part(split_part(:'auth_url', '@', 1), ':', 3)) \gexec
CREATE SCHEMA auth AUTHORIZATION supabase_auth_admin;
ALTER ROLE supabase_auth_admin SET search_path = auth;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
\else
SELECT 1 / 0;
\endif
SQL
then
    printf '%s\n' 'Private Auth database initialization failed.' >&2
    exit 1
fi
unset NOTEBOOK_AUTH_DATABASE_URL
