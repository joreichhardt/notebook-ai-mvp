# Integration log

## Wave 3 persistence / execution seam · 2026-11-12

- A3 HEAD `eae387ef523d7fdb7a705f7c0c99e0fcc5a449f6` merged first; C3 HEAD `bb00822d722d153e7346179fec295401e43aa959` merged into candidate `f0937b1528b4903657fedc447c44afedfdb35a9f`. Both merges clean; worker changes respected assigned paths. Central dependency `httpcore>=1,<2` and `fail -> NoReturn` supplied by main `06c56b6`.
- Reviewed scoped DDL/RLS/lease and personal execution/egress/protocol boundaries. Added main-owned `backend/tests/integration/test_execution_database.py`: real notebook_app connection with single pooled connection verifies foreign same-workspace denial, fresh admission revocation, and disconnect during synthetic secret resolution preventing adapter construction. Vault/provider calls in these tests remain synthetic.
- Integrated suite: **154 backend tests passed** against real isolated PostgreSQL16, including fresh database migration regression through 0003; **17 frontend tests**, TypeScript/Vite build, **9 provisioner tests** and full backend Ruff passed. Existing upstream Starlette/httpx deprecation warning remains. Active LSP on seven core/new test files confirmed zero errors; initial timed-out probe was rerun successfully. Auxiliary numeric-conversion warning reviewed: elements are type/finite-checked and the enclosing adapter response catches conversion errors; German-copy spellcheck suggestions are not Python errors.
- Isolated retained cluster `/tmp/notebook-auth-pg` on 55439 restarted only after verifying stopped/free. First startup failed due default socket directory permissions; explicit socket directory inside the private cluster fixed startup. New `notebook_wave3_verify` migrated/tested and dropped; cluster stopped afterward. System 5432 and A3 55440 untouched.
- This milestone is persistence and execution only, NOT upload/chat/product acceptance. No document/chat capabilities enabled. Trusted job dispatch, fenced publish composition, private Storage/policies/cleanup, isolated parser runtime, aggregate quotas, actual index model compatibility, UI wiring and independent full-flow review remain. No live Supabase/Bitwarden/provider, container rebuild, kube1 mutation or production release performed.

Initial baseline prepared from requested project instructions. Existing diagram backups preserved outside implementation baseline. No remote configured; no production rollout performed.

## Integrated foundation · 2026-11-11

- Integrated A `9a4aa79`, D `1fab8dc` + `8c752da`, E `521a2da`, independent F `757c2fe`; fixed auth findings with `3b19320`.
- Main fixed Compose tmpfs list parsing and frontend edge network after real startup failures. Both containers now healthy; actual proxied GET /api/health/live returns ok and /api/auth/session returns user:null.
- 19 backend tests passed including real isolated PostgreSQL16 RLS; 9 frontend tests passed; 9 Bitwarden provisioner tests passed. Ruff, TypeScript and Vite build passed. One upstream Starlette/httpx deprecation warning remains.
- Both runtime images build. Runtime UID10001 API / UID101 frontend, read-only roots, ALL capabilities dropped verified. This is non-root, NOT rootless evidence.
- Browser inspected desktop/mobile login and actual container frontend. No console errors observed. No authenticated browser E2E claimed without Supabase/configured accounts.
- CI now requires npm tests, an ephemeral PostgreSQL service, migration and RLS tests. No GitHub run, Trivy scan, live Bitwarden request or production rollout occurred.
- Local Compose project notebook-foundation-verify remains available on 127.0.0.1:18080. No existing kube1 workload modified.

Independent review follow-up `18b82ec` closes all three findings on `7752e2f`; 15 auth/security tests and Compose validation passed.

Final local runtime image IDs: API `sha256:8bf059b0f54cec71e52d977993b72eb42bdc9da0002463f61a0430db34b4357f`; frontend `sha256:a24838066f626869eb662fc6109985565f34a1905ebf4642bc07d8785dbe6dff`. These are local image IDs, not published registry digests.

## Wave 2 · 2026-11-11

- Integrated A2, C, D2 on candidate `e228dbc`. User-requested development notices removed and unavailable controls hidden. Runtime discovery failure is retryable; no operator/setup message during loading.
- Verified fresh empty PostgreSQL database migrations through0002 plus43 backend tests before review fixes; after fixes full suite50 passed. Frontend17 tests and build passed; provisioner9 tests passed. Ruff passes.
- Independent review found per-item Bitwarden deletion errors ignored and nonretryable capability discovery. Both corrected with actual SDK response-class regressions and UI retry tests.
- Desktop browser inspected updated container-served landing page; roadmap text absent. No configured live authentication or personal provider integration claimed. Authenticated interaction tests use synthetic fixtures; PostgreSQL RLS/invitation tests use real isolated database.
- No changes to kube1 services, DNS or production credentials. SDK runtime configuration added; bws remains host-side provisioning.

Review closure `58eea05`:18 provider tests and17 frontend tests pass on candidate e228dbc. Final local images: API sha256:7538055dfaa46cbbd2cfdd22efb28f3ca5429b88045515defc572491c1219002; frontend sha256:3bb318856d7d8e6c25ee87098c310bd1157b0d2f275804aa348f06dd88f92b79. Both containers healthy. Browser reloaded final image and checked development notices absent. Test PostgreSQL stopped after verification; disposable data retained for next migration checks.
