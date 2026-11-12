# D3a · Read-only current-source status frontend

## Scope / base

Worktree `/home/jre/orca/workspaces/gemini-nb-clone/notebook-source-status-ui`, branch `notebook-source-status-ui`; verified starting HEAD `a0ee89e4b66fe510a0e6b4fb86a29678a78489a2` (Main application acceptance `dc34927`). Starting status contained only pre-existing untracked `.pi/`, preserved. Dispatch `ctx_d00062dfceac`, task `task_7d5badea2d3b`; no helpers.

## Implemented (focused frontend verification passed; not full-flow accepted)

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

## Remote validation plan and actual history

Main serialized each actual run after fresh idle/capacity checks; initial overlapping A5 activity correctly caused HOLD. A delayed conflicting authorization was reconciled before any pull/install, then explicit `D3-RELEASE-1` authorized the first run. Prerequisites read: `~/dev/homeserver/README.md`, `~/dev/homeserver/secrets/README.md`, `docs/operations/testing-kube1.md`.

- Immutable committed `git archive <full-SHA>` only, exclusively `/home/jre/notebook-tests/d3a/<full-SHA>` with private0700 parent/snapshot and extraction umask022. No `.pi`, secrets or local runtime material.
- Repo-derived image: `node:22-bookworm-slim@sha256:18052396bf1d0c6e0a8c58181b5c1457da19409efbd5032a3f4d86974e74d13c` (`docker/frontend.Dockerfile`). No build. Unique labels/project `notebook-test-d3a`, no ports, socket, DB or production mounts; max1CPU/1GiB/128PIDs, non-root, read-only rootfs/cap-drop/no-new-privileges, bounded tmpfs.
- Final approved mount layout: private disposable app-root RW at `/app`; immutable `src/`, `package.json`, `package-lock.json`, `tsconfig.json`, `vite.config.ts` individually RO, separate own npm cache RW. `HOME=/tmp`, two distinct empty tmpfs user/global npmrc files created under umask077; no inherited credentials/proxies/config. Bounded network-none npm registry smoke, then `npm ci --ignore-scripts --no-audit --no-fund --cache=/cache` (240s) on own labeled bridge; installed `vitest --help`/package version inspection network-none (30s), then `./node_modules/.bin/vitest run src/App.test.tsx src/api.test.ts --maxWorkers=1` (120s) and `./node_modules/.bin/tsc --noEmit` (60s) network-none. `timeout --signal=TERM --kill-after=15s` preserves native exits. No minWorkers flag (unsupported in pinned5.1.0). Registry URLs in lock verified exclusively HTTPS registry.npmjs.org; intended install traffic is not an egress-enforcement claim. No lock changes/install scripts/unapproved package endpoints.
- Record each native exit/result and exact commands, cleanup only own labeled resources and verify0 own containers/networks/volumes. Do not retry unexplained failures until green.

### Actual immutable f6d653126662e28cbf2b6ea404244d7af5e7dffb checks

Official registry response/computed index SHA matched repo pin; linux/amd64 manifest `sha256:47725aca53d3295ec2d3be8f62726599b3033cc262edc2c7e429909b3ac599fc`, config Node22.23.5. Archive16files verified SHA256 against committed contents, never overwritten. Read-only preflights: 12CPUs, ~26GiB available, low load, no notebook/build/run clients before each actual attempt. No foreign services stopped.

Protected metadata/log roots `/home/jre/notebook-tests/d3a/.runtime-<full-SHA>[-attemptN]/`; each `results.json` records native exits/resources and `logs/*-command.json` exact docker argv. Retained immutable source/image and protected logs; disposable runtime app/deps/cache removed after every attempt. Every attempt had cleanup0 and own containers/networks/volumes0/0/0, serialized slot released explicitly after each; each next attempt separately Main-authorized.

1. First run: pull0/network0/install125, OCI refused creating node_modules mountpoint below readonly source root; npm/Vitest/tsc unrun.
2. Attempt2: imageverify0/network0/install1; npm refused user/global config both `/dev/null` (same file); no packages/test assertions/tsc.
3. Attempt3: imageverify0/configsmoke0/network0/install0/Vitest1; Main-requested `--minWorkers=1` unsupported by locked Vitest5.1.0, zero assertions, tsc unrun. No source RED/GREEN claim for these infrastructure/CLI failures.
4. Attempt4: imageverify0/configsmoke0/network0/install0/help0; installed help proves `--maxWorkers` support. **Vitest0, 2files/33tests passed in5.19s** (App25/api8), tsc1: four real mock-typing errors because inferred `api.authConfig` return required documents while public AuthConfig permits absent capability. Vitest reports2isolated workers spawned across2files; maxWorkers1 bounds concurrent workers, not total lifetime processes. This run is **FAILED**, not acceptance.

### Final source verification: 76f488756e57a9d211e8dfb851c0be62ff5efd56

Main reviewed the exact one-line return annotation correction (`frontend/src/api.ts:130`, `Promise<AuthConfig>`) and authorized one new immutable-SHA verification. No casts, suppression, test deletion, runtime gate change or strictness change. New source snapshot, fresh idle preflight23:02:47CET (~26GiB available), same pinned Node22.23.5/Vitest5.1.0 and successful attempt4 layout/config.

**PASS:** imageverify0/configsmoke0/network0/install0/help0, focused **Vitest0: 2files/33tests (App25/api8),5.39s**, **tsc0**, supervisor exit0. Exact commands remain `./node_modules/.bin/vitest run src/App.test.tsx src/api.test.ts --maxWorkers=1` and `./node_modules/.bin/tsc --noEmit`; no minWorkers, no hidden retry. **Cleanup0 and own containers/networks/volumes0/0/0**, disposable app/cache removed; slot released in native message `msg_ca9f1ed9197a`. Protected metadata: `/home/jre/notebook-tests/d3a/.runtime-76f488756e57a9d211e8dfb851c0be62ff5efd56-verification1/results.json`, exact argv/native logs under sibling `logs/`. Immutable archive/image retained. This result is distinct from failed f6 runs; counts are not summed across attempts.

Local LSP cascades in main/Sharing/Connections remain dependency-context findings (no local React/test dependencies); successful remote strict `tsc --noEmit` covers the actual committed frontend source and tests. No dependency installation or unrelated edits made locally to silence those findings.

No frontend build, backend/DB/Storage/integration/end-to-end/provider/production check ran. Remaining gates: Main full UI source review/integration and any separately authorized build/browser/real API/full-flow checks; route/capability/Storage acceptance remains separate, D3 incomplete. Main owns plan/index update and should link this handoff. No deployment requested or performed.
