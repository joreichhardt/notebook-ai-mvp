# E — Cloudflare automation: Pi takeover handoff

Date: 2026-11-13. Branch `agent/deploy`, worktree
`/home/jre/orca/workspaces/gemini-nb-clone/notebook-deploy`.
Inherited baseline: `521a2da483f1eff9e65f730e1dbedb1ebb6d140f`.

## Ownership and coordination

User explicitly transferred the interrupted Codex work to Pi in the same
worktree after confirmed terminal termination. Existing uncommitted
`deploy/cloudflare/` and `deploy/tests/test_cloudflare.py` were retained and
completed, not recreated. `.pi/` remains untouched and uncommitted.

Pi reply handle: `term_cd14ca18-5ca5-4a7a-b37e-4b3372168ca4` (no dispatched Run
assigned). Main mailbox: `run:run_a6efa1e8f690`; Main acknowledged takeover and
scope. Main alone integrates; no main merge/push, pipeline creation or shared
configuration changes. This durable report survives terminal closure.

## Delivered

- `deploy/cloudflare/{__main__,routing,access,probes,__init__}.py`:
  standalone read-only inspection/dry-run, exact existing DNS reconciliation,
  protected whole-host Access preparation/inspection, connector-namespace
  readiness, guarded idempotent activation, DNS/TLS/unauthenticated denial checks,
  and fresh-state 503 rollback preserving foreign ingress/configuration.
- `deploy/tests/test_cloudflare.py`: synthetic API/HTTP and CLI tests, including
  conflicts, drift, ambiguous writes, access policy expansion/bypass refusal,
  secret-safe failures, permissions, lock contention and retained inode.
- `docs/operations/cloudflare-automation.md`: setup, commands, lock/exclusion
  contract, protected settings/credential lifecycle, recovery and honest gates.

Additional Pi changes over the preserved draft: case/trailing-dot and wildcard
hostname-overlap rejection, ambiguous Access POST regression, complete CLI
activate/repeat/rollback exercise, malformed-readiness fixed diagnostic,
explicit optional-Access type narrowing and formatting. The commit Secret Guard
rejected a synthetic credential-bearing URL fixture inherited from Codex. That
single fixture was removed entirely (not encoded/constructed to bypass scanning);
production rejection of URL credentials remains unchanged. Direct regression
coverage of that particular URL form is therefore absent.

No dependencies, migrations or root/shared configuration changes. The CLI uses
stdlib on Linux. Existing DNS is intentionally validation-only: missing or
conflicting records stop rather than silently creating/adopting them.

## Actually verified

- At takeover: `python3 -m unittest discover -s deploy/tests -q`: **37 passed**.
- Final same command: **42 passed** (33 Cloudflare + 9 existing provisioning).
- `uv run --frozen ruff check deploy/cloudflare deploy/tests/test_cloudflare.py`:
  passed.
- `uv run --frozen ruff format --check deploy/cloudflare deploy/tests/test_cloudflare.py`:
  six files formatted.
- Active LSP/auxiliary check of five Python files: no remaining diagnostics;
  four confirmed clean, one inconclusive on the last pass. Not claimed as a
  complete type-proof. Earlier actual findings were fixed.
- `git diff --check` and `git diff --cached --check` passed.
- Read official Cloudflare Access create reference and SDK request models;
  tunnel PUT replacement semantics confirmed in official SDK resource source.
  No authenticated Cloudflare API call was performed. The generated Access
  inline-policy model is incomplete; actual creation/enforcement is still a
  live integration gate, not proven by the fake API.

### Newly checked kube1 inventory (Main-approved, read-only)

Bounded BatchMode SSH with connection timeout, no elevation, no contents/env/logs,
no full inspect, no connector exec, no mutations:

- `/etc/notebook/secret-ids.json`: **absent**.
- `/etc/notebook/bws-token`: **absent**.
- `docker ps -a` filtered to Compose project `notebook-foundation`: query succeeded,
  **zero containers**. This says nothing about other project names or daemons.
- No concrete verified connector process identity/PID was supplied. No broad
  search or namespace probe was attempted. Origin reachability is unproven.

No live DNS/Tunnel/Access/BWS call, container start, migration, host edit or live
activation. Historical tunnel v9 / placeholder / DNS/TLS results from the
homeserver runbook were read, **not revalidated as current Cloudflare state**.

## Blocking gates / next actions for Main

1. **Real readiness is missing.** Main checked `50b16dd`: only
   `/api/health/live`, not `/api/health/ready` with full approved release SHA.
   CLI remains fail-closed; no liveness alias/proxy-generated readiness. Main
   owns actual dependency-aware readiness and release binding.
2. Provide a reviewed running internal reverse proxy and confirmed connector
   PID/network/reachability; reserve/approve the namespace probe separately.
   Neither an internal deployment nor rootless/runtime security is proven here.
3. Provision dedicated Notebook-scoped Cloudflare credentials through Bitwarden,
   explicit project/server and private runtime files; missing bootstrap files
   block that path. Do not reuse Excalidraw/admin credentials or ask for secrets
   in chat. Review exact operator-only owner email/team configuration.
4. Establish **one shared writer lock for all tunnel writers plus operational
   exclusion of Dashboard/other API writers**. Fingerprints are drift detection,
   not atomic CAS. Without this exclusion, no full-config PUT is safe.
5. Under coordinated approval, validate current DNS/tunnel ownership, prepare
   whole-host owner-only Access while still on 503, verify policy via API and
   anonymous DNS/TLS/login redirects. Never add a service-token/public bypass.
6. Once real readiness/release gates pass, Main may approve exact-fingerprint
   activation. Test actual owner Cloudflare login and app auth, forbidden
   identity, callbacks, upload/streaming and authenticated rollback behavior.
   Anonymous Access redirect is **not** authenticated public app smoke/readiness.
7. Bind the reviewed CLI into the eventual single release pipeline. None exists
   in this baseline and no competing one was added. Provide final scan/runtime/
   release evidence separately.

Status: **local implementation/test delivery ready for Main review; live Access,
origin activation and authenticated public acceptance externally blocked**.
User's permission for internal publication and owner-only external login remains
acknowledged; it does not waive these technical gates.
