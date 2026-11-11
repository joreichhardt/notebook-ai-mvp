# Independent foundation review

Reviewed candidate: `e4ee1ecb0530d6636767a81bd4a280c5eab2528e` on 2026-11-11. Scope: implemented auth/RLS, notebook API/UI, CSRF, Bitwarden provisioner and foundation CI. Explicitly pending product functionality is outside this defect list; this review does not approve production release.

## Findings

1. **P2 — Invalid session prevents re-login.** `backend/app/auth/router.py` GET session propagates upstream 401 without clearing its cookies. `frontend/src/App.tsx` initial session error renders only retry, suppressing the login form. A revoked token therefore traps the user until cookie expiry/manual deletion. Reproduce with a session cookie and mocked Supabase identity returning HTTPException(401): GET session returns 401 with no Set-Cookie deletion; retry has the same result. Clear invalid local session state and expose anonymous/login state, while preserving distinct service-outage errors.
2. **P2 — Logout retains local credentials on upstream failure.** The logout route awaits remote revocation before deleting either cookie. A revoked token (401) or auth outage (503) prevents local logout. Reproduced with valid CSRF and mocked upstream 401: response 401, no deletion cookies. Always clear local cookies after validated logout intent; report remote revocation status honestly and avoid claiming success if it could not be confirmed.
3. **P2 — CI omits existing frontend and RLS tests.** `.github/workflows/ci.yml` only builds frontend, never invokes its nine tests. Backend pytest silently skips the RLS test without NOTEBOOK_TEST_POSTGRES_URL. Provision a disposable PostgreSQL service, apply migrations with a separate admin URL, run RLS checks explicitly, and invoke npm test.

These were sent to the main agent before report completion. Fix verification must refer to the later integration commit, not this baseline.

## Verification

- `uv run pytest -q`: 12 passed, 1 skipped (intentional review avoidance of concurrent mutation of parent's PostgreSQL test instance).
- `python3 -m unittest discover -s deploy/tests -q`: 9 passed.
- TestClient reproductions of findings 1 and 2 with synthetic tokens; no real credentials accessed.
- Read-through confirmed normal notebook operations are app-admission/membership gated; SQL RLS uses transaction-local identity; supplied credentials are absent from normal API responses; bws subprocess environment strips unrelated inherited values and results are staged privately.
- CI RLS setup requires a migrated isolated database and an admin test connection permitted to SET ROLE notebook_app. Migration URL uses SQLAlchemy's `postgresql+psycopg://`; psycopg test URL uses `postgresql://`.

No additional concrete implemented-scope blocker identified. Real Supabase auth, Bitwarden connectivity, full browser-auth flow and production security gates remain unverified as already documented in handoffs.

## Fix verification

Verified follow-up candidate: `7752e2f42fd8b181e7eaf07067477ad16a1c6b67`.

- Finding 1 resolved: session 401 deletes both cookies and returns `{user: null}`, which selects the existing login form. 403/503 remain distinct failures rather than false anonymous success.
- Finding 2 resolved: validated logout deletes both cookies on success, already-invalid session and remote outage. Outage response explicitly reports `local_logout: true`, `remote_revoked: false`; it does not claim remote revocation. Frontend accepts the successful local logout. The UI currently does not display the optional remote-revocation detail; this is a follow-up usability improvement rather than retained local credential exposure.
- Finding 3 resolved in workflow definition: pinned disposable PostgreSQL service, explicit isolated test URL, migration before pytest, and `npm test` before frontend build. A hosted Actions run remains unverified; local code review is not a workflow execution claim.
- `uv run pytest backend/tests/auth/test_security.py -q`: **15 passed** including six session/logout regression cases. No PostgreSQL mutation performed during this follow-up.
- `docker compose config --quiet`: passed; tmpfs is now a single quoted mount specification and frontend is attached to both internal and edge bridge. Actual container HTTP verification is owned by the main agent, not repeated here.

All three findings are closed for this foundation candidate. This does not change the documented incomplete product/release gates.
