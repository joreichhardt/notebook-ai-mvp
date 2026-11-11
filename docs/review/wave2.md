# Independent wave 2 review

Candidate: `0469c19` (A2 invitation/membership RPCs, D2 UI, C personal credentials). Reviewed 2026-11-11. No production access or concurrent PostgreSQL mutation.

## Findings

1. **P1 — Check per-secret deletion results before discarding its reference.** `backend/app/connections/vault.py`, `BitwardenStore.delete`, checks only `result.success`. Installed `bitwarden-sdk==2.1.0` defines `ResponseForSecretsDeleteResponse.data.data` as a list of `SecretDeleteResponse` with `id` and optional `error`. A successful request envelope can contain a failed item. The current method returns normally; router then deletes connection metadata and returns 204, leaving the credential with no cleanup reference. Reproduced with the actual installed SDK response parser using `{success:true,data:{data:[{id:<requested>,error:"Permission denied"}]}}`: envelope true, item error present, `store.delete` returns None. Require response data, the requested ID, and absent per-item error; otherwise use the existing redacted 503 / disconnected cleanup-pending path. Add tests for item error, missing item, wrong ID and successful deletion.
2. **P2 — Capability fetch cannot recover through visible retry.** `frontend/src/App.tsx` fetches capabilities once in an effect with empty dependencies and suppresses errors. If initial discovery fails transiently, `config` stays undefined and login is hidden. The visible retry only increments the session effect's counter, not capability discovery. Preserve a separate capability error/loading state and retry the fetch; distinguish service failure from a successful response explicitly disabling login. Parent independently observed the same failure and assigned owner fix.

## Other checks

- A2 SQL RPCs have fixed `pg_catalog` search_path, qualified application tables, PUBLIC execution revoked and a separate NOLOGIN owner. Admission and current inviter authority checks exist. Workspace row locking serializes owner membership mutation and last-owner preservation; invitation consumption locks user/invite. No additional concrete authorization bypass identified by this read-through.
- Invitation token is retained only in React state and removed from URL; UI sharing component is keyed by workspace, avoiding cross-workspace leftover invitation/member state.
- Provider UI accepts only configured catalog entries. Server checks connection owner/admission, keeps secret values/references out of metadata and validates model selection from provider response.
- DNS connection pinning, SDK deadlines, DB/remote orphan reconciliation, new-account registration and full generation/embedding adapters remain documented release gates, not newly discovered defects here.

## Verification

- Actual installed SDK schema inspection and synthetic deletion-result reproduction above; no real Bitwarden access.
- `uv run pytest backend/tests/providers backend/tests/auth/test_security.py -q`: 26 passed; no PostgreSQL integration invoked.
- Parent owns real PostgreSQL and integrated browser checks; this report does not claim those were rerun independently.

## Fix verification

Verified follow-up candidate `e228dbc788f469e518d8ed9f0f087e4c478c18f5`.

- Finding 1 closed: deletion requires a successful envelope, present data, exactly one returned item matching the requested secret ID, and `error is None`. Every other outcome is redacted to 503 and therefore preserves the router's disconnected/cleanup-pending reference. Seven regression variants use actual installed SDK response types, including per-item failure, missing data, wrong ID, duplicate items and success.
- Finding 2 closed: capability discovery has explicit pending/error state and reruns with the shared retry counter. Both anonymous and authenticated paths show a recoverable discovery error, rather than treating failure as disabled login. Aborted responses do not overwrite current state.
- `uv run pytest backend/tests/providers -q`: **18 passed**.
- `npm test --prefix frontend`: **17 passed**, including transient capability failure/retry and pending-discovery states.
- No PostgreSQL mutations or external provider/Bitwarden calls performed. The documented product and production release gates remain unchanged.
