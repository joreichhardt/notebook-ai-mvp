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

## Wave 3 · persistence and provider seam integrated

Resume baseline: `1fe389732d59acaaddcb1463b1ab726fd30805a9` (clean). Contract preparation commit follows this baseline. New Pi agents only; leave previous worker worktrees/sessions untouched. Start at most two independent workers initially, in new sibling worktrees based on the contract commit.

| Task | Owner / worktree | Scope | Status / dependency |
|---|---|---|---|
| A3 | Pi data / notebook-data-v3 | ORM + migration 0003 for documents, versions, chunks, profiles, fenced jobs; RLS/integrity tests | integrated / verified; HEAD eae387e |
| C3 | Pi providers / notebook-provider-v3 | Typed generation/streaming, embeddings, owned execution boundary, cancellation and egress tests | integrated / verified; HEAD bb00822 |
| B3 | Pi RAG / assigned after A3 | Upload/storage, isolated parsing, durable worker and scoped text retrieval/chat | todo; A3, C3 |
| D3 | Pi frontend / assigned after backend seam | Working upload/status/source/chat flow, no development notices | todo; B3 |
| E3 | Pi deployment / assigned separately | Actual Supabase/storage and isolated parser runtime, local integration evidence | todo; parser requirements |
| F3a | Independent Pi review / notebook-seam-review-v3 | Persistence/execution seam review | closed F3a-01 after A4; integrated edcaf398; full-flow review still pending |
| A4 | Pi data / notebook-profile-integrity-fix | Immutable profile correction, migration 0004 | integrated / independently verified; ae30cd5 |

Main owns contracts, app registration, root dependencies/lock, integration and orchestration records. A3 exclusively owns all ORM/migrations; C3 exclusively providers/connections plus provider tests. Workers may write their named handoff only. No production work, live credentials or nested delegation. A3 reserves isolated PostgreSQL port 55440 and a unique temporary data directory; existing 5432 and retained 55439 are untouched. C3 uses protocol fixtures and no database server. Tests using fixtures are not live-provider proof. Orca Run `run_a6efa1e8f690`: A3 dispatch `ctx_9dd83404d0a4` succeeded; C3 final dispatch `ctx_8c55888c9a53` succeeded after user-requested pauses. Branches `notebook-data-v3` and `notebook-provider-v3` preserved. Integration candidate f0937b1 initially passed 154 backend tests. After independent F3a review, A4 correction and closure, default pytest now includes security tests: 176 passed on fresh 0004; Ruff and changed-path LSP passed. Previous frontend17/build and provisioner9 remain successful, not rerun for backend-only closure. See integration log. B3/E3 must settle trusted dispatch, storage cleanup and parser isolation before enabling any capability.

## Wave 2 · integrated

Baseline `a34c43b`. A2 owns invitations/bootstrap/membership and all schema/migrations in notebook-auth-data. C owns provider adapters + personal connection lifecycle in notebook-providers (agent/providers), requests schema from A2. D2 owns frontend flow integration and removal of development/rollout notices in notebook-frontend. Main owns contracts/root configuration, integration and checks. No production changes authorized to workers.

A2 `8c609c1` + bootstrap tests `2914ad1`; C `9d1d9a0` + delete-response correction `b752ad7`; D2 `20ed98c` + discovery-retry correction `3c5eb91`. Candidate `e228dbc`; 50 backend tests (real PostgreSQL), 17 frontend tests, 9 provisioning tests pass. F review `5a6eead`, both findings verified closed by `58eea05`.

Implemented: existing verified-account invite acceptance, admin bootstrap, owner member roles/last-owner guard; personal API connections via Bitwarden, model discovery/selection, retryable cleanup. UI contains no development rollout notices. Remaining: new-account/OAuth registration, invitation revoke/list, audit/rate limits, uploads/RAG/graph, generation/streaming/embeddings, default personal model, credential replacement, live Supabase/Bitwarden and production release gates.
