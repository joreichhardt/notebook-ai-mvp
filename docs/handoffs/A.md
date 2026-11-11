# A — Auth/data foundation

Baseline: 0c75d3c89fe50bd576fe123ec831d59eaa073556. Branch agent/auth-data.

Implemented BFF password login through Supabase Auth, server-side /user verification including confirmed email, secure host-only HttpOnly access-token cookie, Origin validation on login and double-submit CSRF on writes. No refresh token persisted: expiry requires re-login. Session returns identity only, not admission or workspace data.

Routers to include (each includes /api): app.auth.router.router, app.workspaces.router.router, app.notebooks.router.router. Session/login returns csrf_token; frontend sends X-CSRF-Token. Workspaces list and notebook list/create/rename/delete check admission and membership; writes require owner/editor. DB connection must be notebook_app, NOSUPERUSER/NOBYPASSRLS, with transaction-local identity. RLS independently enforces admission, ownership of membership visibility, notebook workspace and writer roles.

Settings: NOTEBOOK_PUBLIC_ORIGIN, NOTEBOOK_SUPABASE_URL (gateway base), NOTEBOOK_SUPABASE_ANON_KEY[_FILE], NOTEBOOK_DATABASE_URL[_FILE]. Secret-file failures produce generic 503. Secure cookies require HTTPS, including local browser tests. Restricted DB role password must be provisioned out of band. No runtime service_role key. No new dependencies.

Migration: NOTEBOOK_MIGRATION_DATABASE_URL=... uv run alembic -c backend/migrations/alembic.ini upgrade head. Migration credential is separate; migration creates notebook_app with no initial password, tables/policies/grants. Never pass migration URL to API.

Verified initial migration on isolated local PostgreSQL16 at 127.0.0.1:55439, data /tmp/notebook-auth-pg. 10 tests passed using NOTEBOOK_TEST_POSTGRES_URL=postgresql://jre@127.0.0.1:55439/postgres uv run pytest backend/tests/auth -q. RLS tests cover cross-workspace visibility, admission denial despite membership, viewer write denial, owner write, inability to grant own admission, cleared transaction identity. Other tests verify CSRF, secure cookie flags, invitation token hashes, foreign membership, unavailable config. Ruff check passed. PostgreSQL test is skipped unless explicit isolated URL supplied; SQLite test is only domain policy unit test.

Pending: full invitation registration/acceptance, bootstrap administrator, membership administration, OAuth/PKCE, recovery/refresh, rate limiting, remaining RAG/provider schema and migrations, storage policies. Invitation schema/hash primitives do not constitute a working invitation flow. No live Supabase or real account test; production deployment not ready. No network/host changes beyond isolated local test cluster.

Sources: https://github.com/supabase/auth (password grant, user and logout endpoints); https://supabase.com/docs/reference/python/auth-getuser (server validation). Upstream errors and passwords are never returned to clients. Root should retain redacted validation handler and no-store middleware.

Session recovery fix: GET session maps only upstream 401 to user:null and expires both cookies; 403/503 remain errors without deleting sessions. Logout always expires cookies after valid CSRF: successful upstream or invalid token => 204; unavailable revocation => 200 with local_logout:true, remote_revoked:false and explicit revocation_unconfirmed detail. This permits frontend local logout without claiming remote revocation. Six regression cases added; 16 tests pass including PostgreSQL.
