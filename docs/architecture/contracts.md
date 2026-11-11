# Contracts v1

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
