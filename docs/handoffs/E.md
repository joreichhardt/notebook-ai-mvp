# E — deployment/security foundation

Baseline: 0c75d3c89fe50bd576fe123ec831d59eaa073556. Branch: agent/deploy. Worktree: notebook-deploy.

Implemented:
- Pinned multi-stage API/static frontend Dockerfiles, numeric non-root users, Docker context exclusions, restrictive foundation Compose, nginx reverse proxy with streaming and explicit unavailable Auth integration.
- Commit-pinned hosted CI checks/builds, Trivy HIGH/CRITICAL gate, all-severity secret gate and image SBOM artifacts. No deployment job or privileged PR runner.
- bws 2.1.0 targeted provisioning with explicit server/project/IDs, version check, private bootstrap read, clean child environment, suppressed CLI output, private staging, all-or-nothing immutable destination, cleanup on error.
- Nine unit tests for provisioning failures/security boundaries and operations/security docs identifying all incomplete release gates.

Verified:
- `python3 -m unittest discover -s deploy/tests -v`: 9 passed.
- `docker compose config --quiet`: passed.
- `docker build -f docker/api.Dockerfile -t notebook-api:agent-e .`: passed; local image ID sha256:72a305a007acbe5c46f79f2fc22713e36b52deecdcb63327ef355ab2b449c1c8 (baseline app only).
- API runtime UID 10001:10001, read-only root, ALL capabilities dropped, no-new-privileges, no network: import and actual HTTP liveness returned 200. Initial immediate request raced startup; bounded readiness retry passed. Test container removed.
- `git diff --check`: passed.

Not verified: frontend image build until D is merged; complete Compose start; real Bitwarden access; rootless daemon; Trivy scan; live Supabase, migrations, SMTP/OAuth, tunnel, registry/Actions/deployment, backup restore. These are explicit next-wave gates, not successful tests. Runtime images retain upstream shell/package tooling pending distroless validation; no waiver granted.

Integration:
- No Python dependency changes. Unit tests use stdlib, discover separately from backend pytest.
- A agreed FILE variables: NOTEBOOK_DATABASE_URL_FILE and NOTEBOOK_SUPABASE_ANON_KEY_FILE. Manifest output names match parent's /run/secrets/notebook_database_url and notebook_supabase_anon_key. UID ownership/rootless mapping must be preflighted before mounting 0600 files; no broadening permissions.
- Root compose is a local foundation only with localhost:18080, no configured Supabase or secrets. Public origin defaults to production domain; set an explicit local origin for local browser auth testing once wired.
- Parent must add true release pipeline, upstream Supabase stack, migrations, isolated worker/parser, egress policy, final-digest scans/daily rescans and rollback/restore evidence before production. No remote configured or altered.
