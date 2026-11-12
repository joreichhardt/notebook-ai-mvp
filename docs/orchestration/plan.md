# Implementation plan

## Aktueller Arbeitsstand

**Runtime-Recovery:** Nutzerautorisierter Pi-Corepatch:12/12Kontrollpfadtests grün; gezielter Secret-Guard-Fix:34/34echte Hooktests grün, Original15/34. Kein deaktivierter Schutz/Override. B3 lud den neuen Guard und belegte normale Softreview/Commit; A5 nach Nutzer-Exit/PIDnachweis wieder aufgenommen und nach weiterem sicheren Checkpoint ebenfalls frisch gestartet. Beide bestehenden Worktrees erhalten, keine Doppelinstanzen. Supervisor benötigt für den Guard noch `/reload` im Idle; für den Core einen frischen Prozess. [Nachweise und Rollback](../operations/agent-runtime.md), [aktueller Handoff](../handoffs/CONTINUE.md).

**Testhost:** Docker-Builds und containerisierte Tests ausschließlich auf kube1; Laptop ohne neue Containerlast. SSH/Docker/Compose geprüft, getrennte private Bereiche für Main/B3/A5 angelegt; Zuordnung und Kommandos im [Testbetriebs-Runbook](../operations/testing-kube1.md). Eigene native lokale Worker-DBs nach laufenden Tests geordnet stoppen und erhalten, neue schwere Tests remote. Bestehende Ports 55443/55444 unten sind historische lokale Reservierungen, keine kube1-Portfreigabe.

Aktueller geprüfter Integrationsstand **`8862a1e2636d`**: A5-Lifecycle/Status nach finalem worker_done integriert, auf kube1 frisch0005/238Defaulttests/Ruff und sechs aktive LSP-Pfade verifiziert. Daten-/Provider-Seam samt F3a-Nachprüfung bildet die Vorgängerbasis `f77255d`. Aktuelle Prüfnachweise stehen im [Integrationslog](integration-log.md); Einstieg in alle Anleitungen: [Dokumentationsindex](../README.md).

- **B3 geliefert, Mainintegration ausstehend:** `../notebook-upload-v3`, Branch `notebook-upload-v3`, Dispatch`ctx_e8fed420635f` keep-open. Getesteter Code **`ea704f48fc81c795ff6fafcb56a1c03e6faf367f`**, finaler Handoff **`17fcc812781431b6c45e96a669c30dfa9ac8efdb`**. Durable TXT/Markdown-Service/privater Storage-Adapter/Multipart/Worker und sichere MemberDTOs; Review B3-R1 mit echten Super/BYPASS-SETROLE-RED/GREEN geschlossen, Test-URL-Portabilitätsfix enthalten. Kube1SCRAM **314Defaulttests grün**, gezielt2/Watchdog1/A-Fixture1/Service12, Ruff grün, Cleanup0eigeneRessourcen. LSP ohne Diagnosen,3inconclusive und2 benannte Auxbefunde; kein pauschales clean. Noch kein Main-Integrationsnachweis, kein HTTP-/Engine-/Credentialwiring.
- **A5 arbeitet an0006:** `../notebook-upload-data-v3`, Branch `notebook-upload-data-v3`, gleicher Task`task_7890065e88b5`, aktueller Dispatch **`ctx_6cd79ee1f0f7`**/keep-open. Früherer Readinessfehler nach tatsächlichem Nutzer-Exit/PIDnachweis sicher behoben; Guardaktivierung nach committed HOLD erneut guardedgestoppt/Exit bewiesen/frisch gestartet. HEAD **`912ad6e6e1e75e2f6023009ef7995821a7a3add8`**, nur `.pi/` untracked. Teilbelege: echte Quota-REDs,10gezielteTests grün, zuletzt260Defaulttests mitWarnung ausgegeben; SSH-Tooltimeout bedeutet **kein bestätigter Gesamt-Exitcode/keine Finalabnahme**. Weitere Concurrency/Bypass/Migration-/Privilegeprüfungen, FinalRuff/LSP/Cleanup und Handoff offen.0006 weder integriert noch freigegeben; [Vertrag](../architecture/contracts.md) bleibt bindend.
- Beide im Orca-Run `run_a6efa1e8f690`, nur Pi, getrennte kube1-Projekte ohne Hostports. Frühere lokale55443/55444-DBs sind gestoppt und erhalten. Gemeinsame Dokumente pflegt Main; Worker liefern aktuelle eigene Handoffs. Nächster Main-Schritt: B3 seriell integrieren/eigenständig auf kube1 prüfen;0006 erst nach finalem Nachweis übernehmen.
- **Freischaltung blockiert:** dauerhafte Finalisierung/Reconciliation, echte private Supabase-Storage-Policies/I/O, sichere Actor-Dispatch-Laufzeit und Quoten. Zielgrenzen: outstanding 10 pro Actor/Notebook und 50 pro Notebook; aktiv 2 pro Actor und 4 pro Notebook. Noch kein Nachweis; actor-private Counts sind kein globales Limit. Lifecycle/Status ist integriert;0006-Quotenimplementierung wird separat geprüft und ist noch keine Freischaltungsgrundlage.
- Erster Slice: nur neue TXT-/Markdown-Dokumente, keine PDF-, Embedding-, Ersetzungs-, Lösch- oder Retry-HTTP-Routen. Dokument-/Chat-Capabilities bleiben aus. PDF benötigt isolierten Parser; anschließend D3/E3 und unabhängiger Full-Flow-Review.

## Historische Meilensteine

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
| B3 | Pi RAG / notebook-upload-v3 | TXT/MD-Upload, Storage und actorgebundene Verarbeitung | in_progress; Verdrahtung benötigt A5 |
| A5 | Pi Daten / notebook-upload-data-v3 | Dauerhafte Upload-Intents/Finalisierung/Cleanup und Quellenstatus | integrated / verified `8862a1e`; Quoten-Folgeentwurf in_progress |
| D3 | Pi frontend / assigned after backend seam | Working upload/status/source/chat flow, no development notices | todo; B3 |
| E3 | Pi deployment / assigned separately | Actual Supabase/storage and isolated parser runtime, local integration evidence | todo; parser requirements |
| F3a | Independent Pi review / notebook-seam-review-v3 | Persistence/execution seam review | closed F3a-01 after A4; integrated edcaf398; full-flow review still pending |
| A4 | Pi data / notebook-profile-integrity-fix | Immutable profile correction, migration 0004 | integrated / independently verified; ae30cd5 |

Main owns contracts, app registration, root dependencies/lock, integration and orchestration records. A3 exclusively owns all ORM/migrations; C3 exclusively providers/connections plus provider tests. Workers may write their named handoff only. No production work, live credentials or nested delegation. A3 reserves isolated PostgreSQL port 55440 and a unique temporary data directory; existing 5432 and retained 55439 are untouched. C3 uses protocol fixtures and no database server. Tests using fixtures are not live-provider proof. Orca Run `run_a6efa1e8f690`: A3 dispatch `ctx_9dd83404d0a4` succeeded; C3 final dispatch `ctx_8c55888c9a53` succeeded after user-requested pauses. Branches `notebook-data-v3` and `notebook-provider-v3` preserved. Integration candidate f0937b1 initially passed 154 backend tests. After independent F3a review, A4 correction and closure, default pytest now includes security tests: 176 passed on fresh 0004; Ruff and changed-path LSP passed. Previous frontend17/build and provisioner9 remain successful, not rerun for backend-only closure. See integration log. B3/E3 must settle trusted dispatch, storage cleanup and parser isolation before enabling any capability.

## Wave 2 · integrated

Baseline `a34c43b`. A2 owns invitations/bootstrap/membership and all schema/migrations in notebook-auth-data. C owns provider adapters + personal connection lifecycle in notebook-providers (agent/providers), requests schema from A2. D2 owns frontend flow integration and removal of development/rollout notices in notebook-frontend. Main owns contracts/root configuration, integration and checks. No production changes authorized to workers.

A2 `8c609c1` + bootstrap tests `2914ad1`; C `9d1d9a0` + delete-response correction `b752ad7`; D2 `20ed98c` + discovery-retry correction `3c5eb91`. Candidate `e228dbc`; 50 backend tests (real PostgreSQL), 17 frontend tests, 9 provisioning tests pass. F review `5a6eead`, both findings verified closed by `58eea05`.

Implemented: existing verified-account invite acceptance, admin bootstrap, owner member roles/last-owner guard; personal API connections via Bitwarden, model discovery/selection, retryable cleanup. UI contains no development rollout notices. Remaining: new-account/OAuth registration, invitation revoke/list, audit/rate limits, uploads/RAG/graph, generation/streaming/embeddings, default personal model, credential replacement, live Supabase/Bitwarden and production release gates.
