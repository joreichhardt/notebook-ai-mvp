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
