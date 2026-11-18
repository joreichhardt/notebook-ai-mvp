#!/bin/sh
# Invoked by PostgreSQL; stdout is reserved for the Vault library protocol.
set -eu
key=/run/secrets/notebook_provider_vault_key
[ -f "$key" ] && [ ! -L "$key" ] && [ -r "$key" ]
[ "$(stat -c '%a:%u' "$key")" = "600:$(id -u)" ]
[ "$(wc -c < "$key")" -eq 65 ]
LC_ALL=C grep -Eq '^[0-9a-f]{64}$' "$key"
exec head -n 1 "$key"
