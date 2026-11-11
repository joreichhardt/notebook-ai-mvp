# Implementation plan

Baseline: `0c75d3c89fe50bd576fe123ec831d59eaa073556`. Integration: `integration/notebooklm`, worktree `../notebook-integration`. Main agent owns root config, app/main.py, architecture and orchestration docs.

| Task | Owner | Paths | Status |
|---|---|---|---|
| A auth/data | auth agent | backend/app/{auth,db,workspaces,notebooks}, backend/migrations, supabase/migrations, backend/tests/auth | verified (foundation only) |
| D frontend | frontend agent | frontend | verified (foundation only) |
| E deployment | deployment agent | deploy,docker,.github,docs/operations,SECURITY.md | verified (foundation only) |
| B RAG | next wave | documents,ingestion,retrieval,chat,workers,parser | todo |
| C providers | next wave | providers,connections | todo |
| F review | independent reviewer after integration | tests/security,tests/e2e,docs/review | todo |

Each agent writes only its own docs/handoffs/<task>.md. No nested delegation or production actions. Dedicated worktrees/branches; merge reviewed commits only.

Active worker branches/worktrees: `agent/auth-data` → `../notebook-auth-data`; `agent/frontend` → `../notebook-frontend`; `agent/deploy` → `../notebook-deploy`. Each starts from baseline above.

## First integrated milestone

A: `9a4aa79` + session/logout corrections `3b19320`; D: `1fab8dc` + formatting `8c752da`; E: `521a2da`. Reviewed implementation candidate `7752e2f`. F report initially `757c2fe`, fixes verified in `18b82ec` (all three findings closed). No worker is authorized to deploy.

Remaining implementation: A invitation/bootstrap/OAuth/recovery/membership, B upload/jobs/RAG/graph, C personal providers and Bitwarden lifecycle, D corresponding complete flows, E actual self-hosted Supabase/release/restore/rootless/scans, F full product acceptance. These are unfinished work, not all external blockers.

External release prerequisites: repository remote/Actions; confirmed rootless runtime; app-specific Bitwarden project and machine account mapping; SMTP/Google OAuth and provider authorization; note.yokoma.de routing. No secret values requested in chat.

## Wave 2 · integrated

Baseline `a34c43b`. A2 owns invitations/bootstrap/membership and all schema/migrations in notebook-auth-data. C owns provider adapters + personal connection lifecycle in notebook-providers (agent/providers), requests schema from A2. D2 owns frontend flow integration and removal of development/rollout notices in notebook-frontend. Main owns contracts/root configuration, integration and checks. No production changes authorized to workers.

A2 `8c609c1` + bootstrap tests `2914ad1`; C `9d1d9a0` + delete-response correction `b752ad7`; D2 `20ed98c` + discovery-retry correction `3c5eb91`. Candidate `e228dbc`; 50 backend tests (real PostgreSQL), 17 frontend tests, 9 provisioning tests pass. F review `5a6eead`, both findings verified closed by `58eea05`.

Implemented: existing verified-account invite acceptance, admin bootstrap, owner member roles/last-owner guard; personal API connections via Bitwarden, model discovery/selection, retryable cleanup. UI contains no development rollout notices. Remaining: new-account/OAuth registration, invitation revoke/list, audit/rate limits, uploads/RAG/graph, generation/streaming/embeddings, default personal model, credential replacement, live Supabase/Bitwarden and production release gates.
