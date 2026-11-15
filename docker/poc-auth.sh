#!/bin/sh
# GoTrue has no *_FILE contract for these values. No shell tracing or output.
set -eu
umask 077
[ -z "${BWS_ACCESS_TOKEN:-}" ] || exit 1
GOTRUE_DB_DATABASE_URL=$(cat /run/secrets/notebook_auth_database_url)
GOTRUE_JWT_SECRET=$(cat /run/secrets/notebook_auth_jwt_secret)
GOTRUE_SMTP_PASS=$(cat /run/secrets/notebook_smtp_password)
[ -n "$GOTRUE_DB_DATABASE_URL" ] && [ -n "$GOTRUE_JWT_SECRET" ] && [ -n "$GOTRUE_SMTP_PASS" ] || exit 1
export GOTRUE_DB_DATABASE_URL GOTRUE_JWT_SECRET GOTRUE_SMTP_PASS
exec /usr/local/bin/auth
