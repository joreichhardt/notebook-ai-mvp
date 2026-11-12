# F3a independent security / integration review

## Candidate and verdict

Reviewed **`a44d4c28457c33b8f5714ca87c5de009c6b1eebd`**, relative to
`06c56b60db1f53ebfa9655a39cbf174418ea0c29` (also the merge-base), in
`/home/jre/orca/workspaces/gemini-nb-clone/notebook-seam-review-v3`, branch
`notebook-seam-review-v3`. Initial tracked tree was clean; preexisting `.pi/`
was preserved and not staged. Review was performed personally, without helpers.

**One Medium/P2 persistence-integrity defect confirmed (F3a-01). Fix before
building actor-owned embedding execution on this schema.** No additional
confirmed credential disclosure, cross-workspace read, generic definer privilege
escalation or endpoint escape was identified in the inspected changes. This is
not a product acceptance, live-provider approval or deployment security approval.
Document/chat routes remain disabled; the finding is a real database-seam defect,
not a demonstrated browser exploit against an enabled ingestion route.

Read `agent.md`, `AGENTS.md`, `multi-agent.md`, the full product specification,
contracts, decisions, integration log and A3/C3 handoffs. Inspected migration 0003,
ORM and lease helpers, execution service, provider protocols/transport, relevant
baseline RLS/grants, changed router/vault boundaries and main's DB integration tests.
No implementation, shared configuration or existing tests were changed.

## Standards axis

No separate documented-standard violation confirmed. The changes use a restricted
runtime role, parameterized SQL, qualified objects and a fixed definer search path;
PUBLIC execute is removed from the new definer. The one privileged lifecycle role
is narrow and cannot be assumed by `notebook_app` in the fresh database tested.
Credential values remain inside the execution/vault boundary; no fallback account,
proxy inheritance or intentional TLS bypass was introduced. Scoped ownership and
engineering handoffs accurately distinguish the delivered seam from later work.

A3 explicitly documents that lease helpers are cooperative internal primitives,
not an unforgeable SQL publication capability. That distinction is essential:
an authorized editor can insert chunks directly, and the runtime can set its own
actor context. Neither is a newly discovered browser bypass in this BFF design;
the caller and future worker must remain trusted, never expose generic SQL/context
selection, and use the fenced transaction protocol.

## Spec axis — F3a-01: referenced index profile can be replaced in place

**Severity: Medium / P2. Confidence: reproduced on PostgreSQL 16.15.**

### References

- `docs/architecture/contracts.md:16`: index profile is **immutable** and notebook-scoped.
- `backend/migrations/versions/0003_documents.sql:90-91`: job/profile FK is
  `DEFERRABLE INITIALLY DEFERRED`.
- `backend/migrations/versions/0003_documents.sql:106-107`: profile immutability
  trigger covers **UPDATE only**.
- `backend/migrations/versions/0003_documents.sql:165-166`: owner/editor DELETE
  policy and SELECT/INSERT/DELETE grants also apply to `index_profiles`.
- `backend/migrations/versions/0003_documents.sql:157`: job write authorization
  compares provider identity, not an immutable model/revision/dimensions snapshot.
- `backend/app/db/models.py:247-252`: ORM reproduces the deferred reference.
- Reproduction: `tests/security/seam_v3/test_database_boundaries.py:83`.

### Reproduction / impact

1. Fixture owner has an active personal connection and private queued job pointing
   at a shared profile `(id=P, provider=fixture, model=original, revision=v1, dimensions=3)`.
2. Connect **directly as notebook_app**, setting transaction-local identity to a
   different admitted editor of the same workspace. Assert that this actor cannot
   SELECT the owner's job or connection.
3. In one transaction, DELETE profile P, then INSERT profile P in the same scope,
   same provider, but `(model=replacement, revision=v2, dimensions=7)`.
4. `SET CONSTRAINTS ALL IMMEDIATE` and commit both succeed. The UPDATE trigger never
   runs; the deferred FK sees the replacement row with the same identity.
5. The owner's unchanged private job now joins to `('replacement', 'v2', 7)` rather
   than `('original', 'v1', 3)`.

This defeats immutable index identity and alters the effective options of another
actor's retained job without access to that job or its credentials. Future code
following the documented pattern of resolving a job's profile could consequently
use the owner's connection for a different model/revision than originally selected,
or misinterpret profile compatibility. **No such model call or vector corruption
was demonstrated: orchestration/vector storage are not enabled.** The proven
impact is persisted job-option integrity loss today. This is distinct from the
legitimate right to create a *new* profile with a *new* identity.

### Recommended fix / verification

A owns the correction. Prefer removing ordinary runtime DELETE on immutable
profiles (retain authorized notebook teardown through the existing parent cascade),
or enforce non-deferrable `ON DELETE RESTRICT` while a job references the profile.
Do not rely solely on an UPDATE trigger or on application convention. Keep model,
revision and dimension changes as new identities. Maintain ORM/migration parity;
use a new corrective migration if 0003 has already been applied outside disposable
fixtures. No broad privileged cleanup RPC is needed for this fix.

Rerun the strict-xfail reproduction with `--runxfail`: it must pass because the
replacement is refused and the owner's job still resolves the original identity.
Then remove the xfail marker and add coverage for supported notebook cleanup.
Do not relax the assertion or allow silent job deletion as a substitute.

## Checks and limitations by boundary

| Boundary | Assessment / evidence |
|---|---|
| Definer grants / role escalation | New catalog regression confirms fixed `pg_catalog` search_path, dedicated owner, no PUBLIC execute, no schema CREATE/row INSERT/filename UPDATE for lifecycle role, and direct-login runtime cannot SET ROLE to lifecycle or membership role. Not a certification of arbitrary preexisting production role grants. |
| Workspace / historical sources | Composite FKs scope document/version/chunk/job/profile links. SELECT policies only expose active current sources; job history remains actor-private. Reviewed A3 tests for foreign scopes, stale versions, tombstones, revoked admission/membership and connections. These existing broad tests were not unnecessarily rerun. |
| Lease / publication | Reviewed atomic SKIP LOCKED claim, attempt ceiling, fresh tokens, expired heartbeat refusal, source-before-job locking and terminal token/clock checks. New two-connection test proves publication's document lock blocks tombstoning, then a committed tombstone prevents stale lock/finish. Full production worker composition is absent, so this does not prove end-to-end publication. |
| Personal credentials | Main's two real-DB tests rerun: same-workspace foreign ownership denied through reused pool, admission removal observed, disconnect during secret resolution prevents adapter creation. New tests revoke admission or connection *during* a synthetic stalled embedding operation; default fresh DB polling cancels it and closes the operation. No Bitwarden or external provider call. |
| DNS / TLS / endpoint | Inspected numeric-only dial, all-address validation, exact configured host/port match, peer equality, original-host TLS with CERT_REQUIRED, no redirects/proxies/retries/fallback and rejection of mapped/link-local destinations. Existing controlled-transport/TLS tests reviewed, not rerun; no new live egress/firewall proof claimed. Internal endpoint opt-in remains an operator trust decision. |
| Decoding / errors | New byte-at-a-time fixtures cover split UTF-8/CRLF, malformed UTF-8, unpaired surrogate text, invalid Anthropic type, infinite/bool embeddings and deeply nested JSON; all malformed cases return sanitized 502 and close response. Correct fragmented text yields exactly delta then done. Existing byte/frame/output limits and compressed-response rejection inspected. |
| Revocation / deadlines | Fresh connection/admission checks and cancellation are implemented, not instantaneous revocation of already-sent data. Workspace/version/lease checks remain the future caller's responsibility. `execution.py:114-119` resolves DB/SDK work before the adapter's deadline (`adapter.py:81`); synchronous to_thread work cannot be force-killed. This is already disclosed in C3, not a new hidden finding: add end-to-end admission/SDK/DB timeouts and capacity budgets before route enablement. |

## Minimal safe next seam (recommendation, not implementation)

1. **Correct F3a-01, then one actor-bound text-only upload/job slice.** Derive actor
   only from verified identity, persist it immutably with the job, and dispatch
   server-generated IDs bound to that durable actor. Start with explicitly
   actor-scoped dispatch, not a global job scanner using a superuser/service role
   or client-supplied actor. Design/review durable dispatch and restart recovery
   separately before enabling an unattended global consumer; no arbitrary SQL,
   membership issuance or worker-wide BYPASSRLS shortcut.
2. **Keep trusted I/O separate from private Storage and untrusted parsing.** Private
   original bucket, server-generated exact object keys, stream actual upload byte
   limits, idempotent create/compensation, current-membership BFF reads and at most
   60-second signed downloads. Test against the real Supabase Storage schema,
   not invented application tables. Capture narrowly scoped cleanup work before
   tombstoning makes keys invisible; do not reopen historical-source SELECT access
   merely to let cleanup find objects. Service storage credentials stay only in
   the trusted I/O boundary, not parser or browser.
3. **Provision parser isolation before accepting PDF.** Preprovisioned restricted
   parser service/pool or equivalent sandbox: no network, DB/Storage/model secrets,
   Docker socket or host paths; bounded read-only input and bounded output, hard
   wall time/CPU/RAM/PID/tmp limits. Validate returned text, page count, offsets,
   hashes and aggregate size as untrusted data. TXT/MD can be a first honest slice;
   do not enable a PDF control until this isolation is verified.
4. **Trusted worker composes the seams.** Fresh short restricted transactions before
   claim, model call and publication; heartbeat loss cancels work. Never hold a DB
   transaction during parser/provider I/O. Match actual embedding model/dimensions
   against immutable profile using the actor's own connection. Lock source/job,
   insert chunks, finish LAST in the same transaction and rollback all output on
   failure. Add deletion/version/revocation/reclaim races, storage compensation,
   aggregate quotas and restart tests before capability enablement.

These are next-step acceptance boundaries, not claims that dispatch, private
Storage policies, cleanup, isolated parser, retrieval/citations or global quotas
already exist. No privileged bypass is proposed as a substitute.

## Actual local verification

Own `.venv` created with `uv sync --frozen --quiet`. Reserved 55441 was checked
free before starting a unique loopback PostgreSQL **16.15** instance at
`/tmp/notebook-f3a-pg.U6ywZV21/data`, database `notebook_f3a_review`. Trust auth was
used only for this temporary local fixture. Initial initdb attempt failed because
its redirected log occupied the proposed data directory; using a separate `data/`
subdirectory corrected that setup error. Fresh Alembic upgrade through 0003 then
succeeded. Ports 5432, 55439 and 55440 were untouched.

```sh
NOTEBOOK_MIGRATION_DATABASE_URL=postgresql+psycopg://jre@127.0.0.1:55441/notebook_f3a_review \
  PYTHONPATH=backend .venv/bin/alembic -c backend/migrations/alembic.ini upgrade head

NOTEBOOK_TEST_POSTGRES_URL=postgresql://jre@127.0.0.1:55441/notebook_f3a_review \
  .venv/bin/python -m pytest tests/security/seam_v3 \
  backend/tests/integration/test_execution_database.py -q --tb=short
# Final: 13 passed, 1 strict xfailed in 2.43s.

# Explicit red proof was also run (before the final test additions):
NOTEBOOK_TEST_POSTGRES_URL=postgresql://jre@127.0.0.1:55441/notebook_f3a_review \
  .venv/bin/pytest tests/security/seam_v3/test_database_boundaries.py::test_referenced_profile_identity_cannot_be_replaced \
  --runxfail -q --tb=short
# 1 FAILED: actual ('replacement','v2',7), expected ('original','v1',3).

.venv/bin/ruff check tests/security/seam_v3
# All checks passed.
git diff --check
# Passed (tracked diff); added files also checked after staging.
```

The strict xfail is an explicit open defect, **not a passed acceptance test**; it
will XPASS-strict/fail when the fix lands until its marker is removed. No existing
test was weakened. Active LSP probes on both new test files finally confirmed two
clean files, zero errors and zero inconclusive results. Initial import resolution
and type-narrowing issues in the new tests were corrected without ignores or
shared configuration edits.

The temporary database was dropped, this instance was stopped, and `pg_ctl status`
confirmed **no server running**; its synthetic data directory/logs remain locally.
No production credentials, deployments, shared environments or live providers were
used. Main's previously reported 154 backend / 17 frontend / 9 provisioner results
were reviewed as supplied evidence, **not claimed as rerun here**.

**Axis totals:** Standards: 0 confirmed violations. Spec: 1 Medium/P2 defect,
immutable-profile replacement. Remaining unimplemented systems remain acceptance
gates, not fabricated findings against this intentionally limited milestone.
