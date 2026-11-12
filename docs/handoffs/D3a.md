# D3a · Read-only current-source status frontend

## Scope / base

Worktree `/home/jre/orca/workspaces/gemini-nb-clone/notebook-source-status-ui`, branch `notebook-source-status-ui`; verified starting HEAD `a0ee89e4b66fe510a0e6b4fb86a29678a78489a2` (Main application acceptance `dc34927`). Starting status contained only pre-existing untracked `.pi/`, preserved. Dispatch `ctx_d00062dfceac`, task `task_7d5badea2d3b`; no helpers.

## Implemented (not yet runtime-accepted)

- Existing `/api/capabilities` documents boolean passes through discovery; only literal `true` renders the source reader. False/absent sends no document request and shows no product rollout notices.
- Read-only contracted `GET /api/notebooks/{id}/documents`, using existing same-origin request/session/CSRF handling and AbortSignal. No DTO additions beyond the existing contract, no backend changes/enablement.
- Accessible German Quellen region, filename as React text, semantic list, all four current-version statuses, finite mapping of existing `not_processed`, `processing_failed`, `processing_cancelled`; unknown errors use fixed fallback. Raw error strings never rendered by this slice. Loading, empty, denied, safe fetch failure, manual refresh/fetch retry included; no polling or job retry.
- Scope-keyed source component clears old user/notebook rows during render, aborts on scope change/unmount and ignores settled stale responses. Workspace switching and logout start clear selected notebook immediately; WorkspaceApp is user-keyed. Existing navigation, admission and mutation transport preserved.
- No upload/delete-source/job-retry/chat controls, providers, production, Storage or diagnostic runs.

## Prepared tests

`frontend/src/App.test.tsx`: all statuses; hostile HTML filename/unknown error; each finite processing code; false/absent zero requests; loading/error/retry/empty; refresh403 clearing; late notebook response isolation; visible notebook/workspace switch clearing; logout-start cancellation; user-change/unmount isolation. Existing workspace/auth/provider tests retained.

`frontend/src/api.test.ts`: explicit capability normalization and encoded contracted GET with same-origin credentials, signal and no mutation headers; existing transport tests retained. These are mock/component tests, not end-to-end/backend/Storage evidence.

## Checks actually run

Local source inspection only, plus `git diff --check` (passed). Base/status commands: `git status --short`, `git rev-parse HEAD`, `git branch --show-current`. No local dependencies present (`frontend/node_modules` and project `node_modules/.bin/vitest` absent); no install, test/build/container/DB/integration command run. Automatic editor/LSP feedback is inconclusive because React, JSX runtime and test packages are missing, not a source-error acceptance result.

Read AGENTS, product specification, contracts, plan and Main CONTINUE read-only; Main owns index/plan/contracts updates and should link this handoff without marking D3 complete.

## Remote validation proposal — HOLD, not executed

Main reply `msg_5298367a0605` reserves next serialized frontend-only kube1 slot but **execution requires explicit Main release** after A5 coordination. Prerequisites read: `~/dev/homeserver/README.md`, `~/dev/homeserver/secrets/README.md`, `docs/operations/testing-kube1.md`.

- Immutable committed `git archive <full-SHA>` only, exclusively `/home/jre/notebook-tests/d3a/<full-SHA>` with private0700 parent/snapshot and extraction umask022. No `.pi`, secrets or local runtime material.
- Repo-derived image: `node:22-bookworm-slim@sha256:18052396bf1d0c6e0a8c58181b5c1457da19409efbd5032a3f4d86974e74d13c` (`docker/frontend.Dockerfile`). No build. Unique labels/project `notebook-test-d3a`, no ports, socket, DB or production mounts; max1CPU/1GiB/128PIDs, non-root, read-only rootfs/cap-drop/no-new-privileges, bounded tmpfs.
- Read-only frontend source mount; external own private runtime volumes for `node_modules` and npm cache only. `npm ci --ignore-scripts --no-audit --no-fund`, then `./node_modules/.bin/vitest run src/App.test.tsx src/api.test.ts --maxWorkers=1` and `./node_modules/.bin/tsc --noEmit`; bounded supervisor preserving exact native failure. Install requires registry.npmjs.org HTTPS/DNS only; test/typecheck runner can use network none. No lock changes/install scripts/unapproved package endpoints.
- Record each native exit/result and exact commands, cleanup only own labeled resources and verify0 own containers/networks/volumes. Do not retry unexplained failures until green.

Remaining gates: authorized host slot, actual focused Vitest and TypeScript validation (currently unrun), Main source/integration review; real route/capability/Storage/full-flow acceptance remains separate and D3 incomplete. No deployment requested or performed.
