# C — personal provider connections

Branch agent/providers, baseline a34c43b. Owns providers/connections modules and tests. Requires A's ProviderConnection model and migration0002, and root bitwarden-sdk==2.1.0 dependency.

Router: app.connections.router.router (all paths include /api). GET providers returns only configured administrative endpoint IDs/labels/kind; no configured secret store gives empty items. GET connections returns caller-owned metadata without secret references or values. POST connections stores personal API key in Bitwarden and DB reference; POST /connections/{id}/test queries model catalogue; PATCH selects a returned model; DELETE revokes and removes metadata. Remote cleanup failure returns202 with disconnected + cleanup_pending and persists inactive metadata for retry. No model generation, subscription OAuth, embeddings, default connection, or worker integration yet.

Configuration prefix NOTEBOOK_PROVIDER_: ENDPOINTS JSON list {id,label,kind:openai|anthropic|ollama,base_url,allow_internal:false}; BITWARDEN_API_URL, BITWARDEN_IDENTITY_URL (explicit HTTPS URLs), BITWARDEN_PROJECT_ID, BITWARDEN_ORGANIZATION_ID, BITWARDEN_TOKEN_FILE (private file owned by runtime UID). API base URL includes /v1 for OpenAI/Anthropic, Ollama is root. No browser-defined endpoint allowed.

Host provisioning stays bws. bws2.1.0 create/edit accept secret values as argv; official Python SDK2.1.0 provides in-memory create/get/delete without argv exposure, approved by main for this personal lifecycle. Native SDK imported lazily. No persisted SDK state, SDK exceptions redacted. Actual installed SDK signatures and SecretResponse fields inspected; no live Bitwarden call or credentials used. Separate personal-secret machine account required; never reuse deployment token.

Residual release gates: real Bitwarden CRUD + account scope; DNS validation is pre-request, not connection-pinned, so administrative endpoints still require enforced egress policy to contain DNS rebinding; orphan reconciliation after a DB commit failure following remote create/delete; model catalogue is not proof of generation/embedding capability; no paid/live provider calls performed. In-process SDK network deadlines need deployment supervision before production. Local models currently require an explicit personal credential as well.

Sources: https://bitwarden.com/help/secrets-manager-cli/ ; https://github.com/bitwarden/sdk-sm/blob/main/languages/python/example.py ; https://platform.claude.com/docs/en/api/models/list ; https://docs.ollama.com/api/tags .

Validation: 11 tests passed (six provider/vault boundaries and five router tests); Ruff passed. Router tests exercise other-user ownership denial, metadata without secret IDs, CSRF, unknown endpoint rejection before vault access, and failed deletion persisting disconnected metadata followed by successful retry. SQLite here is explicitly domain/HTTP verification; A owns actual PostgreSQL RLS tests. No live provider or Bitwarden requests performed.

Review correction: Bitwarden deletion now requires SDK envelope success plus exactly one per-secret result with the requested ID and error=None. Missing/empty/mismatched/duplicate/error items fail closed and preserve the router's inactive retryable metadata. Verified against installed SDK2.1.0 actual schema constructors; provider suite now 18 tests passed.
