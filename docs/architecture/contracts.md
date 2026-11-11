# Contracts v1

## Wave 3 implementation contract (not yet implemented)

This section fixes the next integration seams; capability flags remain off until the corresponding complete route is wired and tested. First deliver document/job/chunk persistence and provider execution, then upload/worker/chat/UI against those tested seams. This is not a reduction of the full PDF, vector and graph requirements.

### Documents and jobs

- `POST /notebooks/{id}/documents`: multipart `file`, plus optional `connection_id` and `index_profile_id` (both required together for embedding). Header `Idempotency-Key` required, 1–128 characters. Return 202 `{document,job}` only after original storage and durable job creation succeed. Repeated identical request returns the existing result; conflicting content/options returns 409. Limit actual streamed file bytes to 25 MiB, not just Content-Length. Editor/owner only. TXT/MD must be valid UTF-8 without binary/NUL content; PDF requires signature validation and isolated parsing. Display filename never becomes a storage path.
- `GET /notebooks/{id}/documents`: `{items:[{id,notebook_id,filename,version_id,status,error_code}]}`; status `queued|processing|ready|failed`. `DELETE /documents/{id}` revokes visibility immediately and schedules idempotent storage cleanup. `POST /documents/{id}/retry` retries the current failed version under the current actor's permission and explicitly selected own connection; never inherits another uploader's credentials.
- Original storage is a private Supabase bucket; server-generated object key `workspace_id/notebook_id/document_id/version_id/original`. No browser storage credentials or unrestricted public bucket. Authorized downloads via BFF; any signed URL lifetime at most 60 seconds. Missing storage configuration fails closed. Cross-service failures require compensation/reconciliation, not falsely successful jobs.
- A owns ORM classes `Document`, `DocumentVersion`, `IngestionJob`, `Chunk`, `IndexProfile` and migration 0003. Documents carry workspace/notebook, creator, display filename, current version and deletion timestamp. Immutable versions carry document/workspace/notebook, sequential version, media type, byte count, SHA-256, unique storage key and created timestamp. Scope must be enforced by composite FKs, not just application code.
- Job carries version/workspace/notebook, actor_user_id, nullable connection_id and index_profile_id, idempotency_key, status (`queued|running|succeeded|failed|cancelled`), attempts, available_at, lease_token, lease_expires_at, heartbeat_at, safe error_code and timestamps. Text extraction alone needs no model credentials; embedding requires both connection/profile. Retain connection ID as historical identifier on disconnect (no credential reference, no cascade deleting job history); runtime lookup must fail closed if absent/inactive/not owned by actor. No model work without that check.
- Claim uses an atomic conditional update or `FOR UPDATE SKIP LOCKED`, with fenced lease token, heartbeat, bounded retries and stale-lease recovery. Before claim, model call and publish recheck admission, membership/edit role, active document version and personal connection. Obsolete workers cannot publish after a lease is reassigned. Deletes/version changes invalidate pending work and exclude old chunks from retrieval immediately.
- Chunk carries version/document/notebook/workspace, ordinal, text, SHA-256, nullable page (PDF 1-based), start/end offsets within its page/text. Unique `(document_version_id,ordinal)`; ordered offsets and nonnegative ordinals. Initial text chunks target 1200 characters, 200 overlap, maximum extracted text 2 million characters and 500 PDF pages. All limits enforced before publication.
- Index profile is immutable notebook-scoped `{id,workspace_id,notebook_id,provider,model,revision,dimensions}` with positive bounded dimension. No credentials/owner connection in a shared profile. Vector persistence and graph tables follow in a separate A-owned migration once the actual vector runtime is verified; text-only is explicitly text-only, never reported as hybrid search.
- RLS: admitted current members read nondeleted document/version/chunk/profile data; owner/editor mutate. Job status may be exposed to members through sanitized document DTOs; raw jobs are actor-private, with owner/editor checks for mutation. Runtime cannot grant itself membership or use privileged generic SQL. Worker credential and lease publication boundary must be reviewed before enabling ingestion. Storage policies are separately verified on the actual Supabase schema; no fake Storage tables in application migrations.

### Provider execution seam

- Preserve model discovery and connection management. Add typed messages and stream events in `app/providers`: `Adapter.stream(*,model,messages,max_output_tokens)` yields normalized text deltas and a terminal usage/finish event; `Adapter.embed(*,model,texts)` returns validated vectors with the actual model identity. Existing constructor remains compatible. Bound input/output, framing buffers, request deadline and batch size; no implicit retries after visible output.
- Implement native Anthropic Messages streaming, OpenAI-compatible streaming/embeddings and Ollama chat/embedding only where supported by official API documentation. Anthropic embeddings and Codex subscription are unsupported rather than silently rerouted. Explicit unsupported-operation errors, no fabricated capability/model/context metadata.
- A small connection execution service in `app/connections` takes authenticated actor ID and connection ID, verifies active ownership before secret resolution, resolves credentials server-side only, and calls the adapter. Rechecks for jobs and long-running operations must use fresh DB state, not a cached ORM object. No secret/key-bearing object escapes this boundary into jobs or DTOs.
- Cancellation closes upstream response/client; timeout, malformed protocol, rate-limit and credential errors produce sanitized application errors without request headers/body, provider raw text or secrets. No redirects, environment proxy inheritance, endpoint/account fallback, tools or model-generated code execution. Validate actual connected destinations against allowed addresses (close the DNS-check/connect gap) before treating model egress as production-ready.
- BFF chat SSE retains `delta`, `citation`, `done`, `error` events above; provider events are internal and do not constitute verified citations. B validates every citation against retrieved authorized chunks before exposing it. Never hold a request-scoped DB transaction open for the lifetime of an upstream stream.


All endpoints under /api. UUID identifiers. Error: {"detail": {"code": str, "message": str}}. Lists: {"items": [...]}. Unauthorized 401; forbidden 403; unavailable integration 503. No production auth mocks.

BFF session: HttpOnly Secure SameSite=Lax cookie; mutating requests validate Origin and CSRF. Supabase verifies identity; app_admissions independently gates app access. DB context is transaction-local, restricted role; missing identity denies access. Owner manages membership/content, editor edits content, viewer reads/asks. Chats private by user ID.

Initial endpoints: GET /auth/session -> {user: {id,email}|null}; POST /auth/login {email,password}; POST /auth/logout; GET /workspaces -> items {id,name,role}; GET/POST /workspaces/{id}/notebooks; PATCH/DELETE /notebooks/{id}. Notebook {id,workspace_id,title}. POST /invitations/accept {token}.

Provider contract: async test_connection, models, stream(messages), embed(texts); capabilities explicit. Connections belong to user ID and expose metadata only. Credentials resolved server-side from Bitwarden references. No arbitrary user endpoints; administrative allowlist. No fallback account.

Future document upload: POST /notebooks/{id}/documents multipart, 25 MiB maximum; PDF/TXT/MD. Job: workspace_id, notebook_id, document_version_id, actor_user_id, connection_id, idempotency_key; never credentials. Recheck permission on claim and before publish.

Chat SSE: delta {text}; citation {id,document_id,version_id,chunk_id,page,quote}; done {message_id}; error {code,message}. Disconnection cancels upstream. Validate all citations against selected authorized sources. Index profile: provider, model, revision, dimensions; no mixing profiles. Graph evidence uses same scoped chunk IDs.

## Wave 2 discovery

GET /capabilities exposes booleans only: auth.password_login, auth.registration, auth.google, auth.recovery, invitations, members, documents, chat. Unsupported methods are hidden; no roadmap/status notices in product UI. Provider catalog is authenticated GET /providers; an unconfigured secret store yields no connectable providers. Endpoint configuration is administrative JSON, never arbitrary URLs accepted from browser requests. Personal secret writes use Bitwarden SDK 2.1.0 while host bootstrap provisioning remains bws.

## Invitations, membership and personal providers

- GET /auth/admission: {admitted:true}; 403 means identity has no admission. Invite acceptance uses verified identity without prior admission.
- POST /invitations {email,workspace_id|null,role}: {id,token,expires_at}. Workspace owner for workspace invites; platform administrator for app invites. No email is sent. Tokens are returned once for explicit copying into /invite#token=... and stored hashed.
- POST /invitations/accept {token}: {workspace_id,invited_workspace_id}. Transactional verified email/current inviter/expiry/revocation checks; personal workspace exactly once.
- GET /workspaces/{id}/members: items {user_id,role}. PATCH/DELETE /workspaces/{id}/members/{user_id}; owner only, last owner protected.
- GET/POST /connections: metadata only / {provider_id,label,api_key}; provider IDs refer to administrator catalog.
- POST /connections/{id}/test: {models:[{id}],status:"ok"}; this verifies model discovery, not generation.
- PATCH /connections/{id} {model}: validates current model listing.
- DELETE /connections/{id}: 204 confirmed cleanup; 202 cleanup_pending preserves inactive metadata for retry. Other users cannot list, read, test or revoke the connection.
