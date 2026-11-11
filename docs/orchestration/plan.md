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

## Wave 2 · active

Baseline `a34c43b`. A2 owns invitations/bootstrap/membership and all schema/migrations in notebook-auth-data. C owns provider adapters + personal connection lifecycle in notebook-providers (agent/providers), requests schema from A2. D2 owns frontend flow integration and removal of development/rollout notices in notebook-frontend. Main owns contracts/root configuration, integration and checks. No production changes authorized to workers.
