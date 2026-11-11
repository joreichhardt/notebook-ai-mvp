# Integration log

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
