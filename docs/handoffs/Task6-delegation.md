# Task6 — first durable exact text-job delegation slice

## Status and scope

**Implemented, uninstalled, NOT DB-accepted; Task6 is not complete.** Implementation base: `integration/notebooklm`, `ac817f881250`. Main prepares a reviewed immutable snapshot for isolated acceptance; a source commit is not DB acceptance. No remote, SSH, containers, local PostgreSQL, credentials, provisioning, capability switch, startup assembly or deployment was executed. No Combined/D4/7a986f1 rerun. Existing user documentation and `.pi/` were preserved. Main owns shared contract/index/plan updates and independent review.

Independent static review found one deterministic current-head assertion defect; `backend/tests/auth/test_documents.py` now expects0007, while historical pinned fixtures remain unchanged. Main independently passed37delegation/text tests (1.51s) and source Ruff. The separately reviewed [native packet](Task6-packet.md) has78independently passing unit tests (0.24s); PostgreSQL acceptance remains UNRUN. Actual concurrent schedulers, process restart and wire-COMMIT ambiguity are not proven by these local results.

Authority: [approved boundary](../architecture/contracts.md#task6-durable-text-job-delegation--approved-implementation-boundary). Main additionally approved the optional `TextWorker` operation-runner seam via supervisor reply; default verified-Identity behavior and `dispatch_one(Identity, ...)` remain unchanged.

### Actual changed files

- `backend/migrations/versions/0007_text_dispatch.py`
- `backend/migrations/versions/0007_text_dispatch.sql`
- `backend/app/workers/delegation.py`
- `backend/app/workers/text.py` — explicitly approved optional seam only
- `backend/tests/ingestion/test_delegation.py`
- `backend/tests/documents/test_delegation_database.py`
- `backend/tests/auth/test_text_delegation_migration.py`
- `docs/handoffs/Task6-delegation.md`

## Implemented boundary

- Migration0007 follows0006. A private RLS/FORCE RLS ticket is inserted by a fixed trigger only on canonical `stored -> finalized`; job/actor/scope come from persisted canonical rows, purpose is always `text_extract_v1`. No enqueue RPC, historical backfill or actor input. Finalized replay is a no-op for issuance. Ticket insertion failure rolls back finalization.
- Separate `notebook_text_scheduler` and `notebook_text_dispatch_owner` roles are created NOLOGIN/NOSUPERUSER/NOBYPASSRLS/NOINHERIT, with preexisting-role safety checks. No passwords or memberships are assigned. Definer gets narrowly column-scoped source reads, RLS restricted to finalized canonical text uploads, ticket INSERT and only delivery-state UPDATE columns. It has no source/job mutation grants or secret/provider-table grants. Its schema CREATE grant is revoked after function ownership transfer.
- Only claim/validate/renew/ack functions are granted to scheduler; PUBLIC EXECUTE is revoked. Owner-only eligibility/issuance helpers are not scheduler APIs. Definer paths use `pg_catalog` and qualified tables. Ordinary app raw-job RLS/grants are unchanged.
- One claim per call, current admission/editor/source, TXT/MD, no model options, sticky0006 cancellation, fresh delivery UUID, 1–300s lease, five-second post-expiry backoff. Claim/validate/renew/ack take existing quota gate first and never lock/write source/job rows, avoiding a reverse source/job lock sequence. Existing worker retains source/job/quota transitions.
- **Main clarified retry scope:** ingestion execution attempts remain capped at5, never reset. Delivery retries have **no lifetime count cap**; bounded means one claim per call, finite lease and backoff. No invented dead-letter transition. Terminal publication can therefore reconcile after repeated scheduler crashes. Exhausted running jobs are not returned as executable; their preexisting nonterminal metadata is not rewritten.
- Revoked/cancelled/stale/exhausted jobs are excluded from selection. A delivered but quota-blocked ticket moves behind other eligible work until its lease/backoff expire. Running recovery requires ingestion expiry independently of delivery expiry. Terminal success can be validated/acknowledged without invoking worker/Storage again. Ack and renewal match all immutable grant fields plus live delivery token; old/replayed grants cannot ack/renew.
- `JobExecutionGrant` is not authentication. Repository freshly validates the exact grant before `TextWorker.run_one`. Repository verifies direct `notebook_text_scheduler` current=session principal, nonsuper/non-bypass, and READ COMMITTED. Actual execution remains direct `notebook_app` with existing fresh admission/RLS/ingestion lease checks. No Identity is constructed.
- Factories are trusted injectors: pool acquisition <=1s, connect <=3s, startup statement <=3s and lock <=1s must actually be configured, including before role checks. Methods reinforce transaction statement/lock settings. Numeric declarations do not prove arbitrary factory behavior, and DNS/TLS/OS scheduling is not a total wall-clock guarantee. No production factories were installed.

## Cancellation and thread ownership

The optional worker path uses one shared `BoundedThreadRunner` for scheduler transactions, claim, pulse, decode/chunks, publish and finish. The underlying concurrent future stays fenced after a waiter is cancelled. New operations wait for that actual future, rather than merely for cancellation of an asyncio wrapper. No DB transaction spans Storage awaits. The default worker path preserves its previous synchronous publication/cancellation boundary.

Delegated cancellation propagates `CancelledError`, does **not** launch cancellation `_finish` or delivery ack, and does not assert rollback. A publish/finish may commit after the waiter is cancelled. Later durable terminal reconciliation handles that outcome. A watcher failure while publication is in flight waits for publication's actual result; terminal success is not relabelled as lease loss solely because a late heartbeat sees a terminal job.

`runner.close(timeout<=3)` rejects new work and reports `False` if an operation is still outstanding. It does not kill the thread or pretend quiescence; supervisors must retain unresolved shutdown as an operational failure and allow durable recovery. Lifetime is loop-local. Each consumer admits one delivery at a time. No automatic polling/delivery-heartbeat loop is installed; long execution can outlive the delivery lease and return `lease_lost` on ack even after successful publication, then reconcile on the next eligible delivery without another Storage read.

## Actual local evidence

Only lightweight checks, without database fixtures, were run:

```sh
.venv/bin/pytest -q backend/tests/ingestion/test_delegation.py backend/tests/ingestion/test_text.py
# 37 passed in 1.34s (25 new delegation cases + 12 existing parser cases)

.venv/bin/pytest -q backend/tests/ingestion/test_delegation.py
# 25 passed in 0.63s

.venv/bin/ruff check backend/app/workers/{delegation,text}.py \
  backend/migrations/versions/0007_text_dispatch.py \
  backend/tests/{ingestion/test_delegation.py,documents/test_delegation_database.py,auth/test_text_delegation_migration.py}
# All checks passed

.venv/bin/ruff format --check backend/app/workers/{delegation,text}.py \
  backend/migrations/versions/0007_text_dispatch.py \
  backend/tests/{ingestion/test_delegation.py,documents/test_delegation_database.py,auth/test_text_delegation_migration.py}
# 6 files already formatted
```

Changed Python files also passed `python -m py_compile` using `.venv/bin/python`. `git diff --check` passed and `git diff --cached --name-only` was empty. Initial exploratory `python` failed because only `.venv/bin/python` is available; the corrected commands passed. Initial lint/type findings were corrected before final checks.

New unit cases exercise exact/fresh validation, direct-principal transaction checks, transaction exit/commit failure before execution, sanitized generic failures, finite method budgets, one outstanding delivery, default/optional worker paths, cancellation during publication and failure-finalization, watcher failure before publication result delivery, unresolved shutdown, outstanding-future bounds through repeated cancellation, and terminal no-I/O reconciliation.

### Active diagnostics and scoped false-positive ruling

Active Pi-lens Python diagnostics ran on edits. Actual type errors were fixed; new unit/migration test files received `Python clean`. **Do not report all diagnostics clean:** the lens retains `python-sql-injection` flags even on fixed literals and bound parameters. Main inspected the implementation and explicitly adjudicated these patterns as SQL-injection false positives, without approving all SQL logic or disabling rules. No scanner suppressions were added for SQL.

Reported message: `Potential SQL injection sink — use parameterized queries (identifier)` (automated reminder sometimes omits `(identifier)`); recommendation: `Replace dynamic execution/string interpolation with parameterized or allowlisted operations.` Reported anchors:

- `backend/migrations/versions/0007_text_dispatch.py:15`: fixed adjacent, version-controlled SQL loader via `op.execute(text(Path(__file__).with_suffix('.sql').read_text()))`; no caller-controlled path/DDL.
- `backend/app/workers/delegation.py:121,129,130,131,133`: literal direct-principal SELECT, literal SET LOCAL statement/lock timeouts, literal SHOW isolation, and private execution of fixed function-call SQL with a separate bound-parameter mapping.
- `backend/app/workers/text.py:105,109,157` in the edit-time report: existing literal/bound actor-lock/count statements and terminal helper call; these SQL operations were not changed by this slice.
- `backend/tests/documents/test_delegation_database.py`: edit-time lines47,135,205,207,211,216,246,285,287 (line numbers move after formatting). Fixed test statements/bound function/privilege queries, plus fixture-only `psycopg.sql.SQL(...).format(sql.Literal(material))` for synthetic-role PASSWORD DDL, which cannot use ordinary bind placeholders. Later fixed test statement allowlist is also not caller-supplied SQL. Parent should record exact retained diagnostic IDs using its diagnostic tooling; child has no `lens_diagnostics` tool. This is not a blanket disposition for arbitrary interpolation.

## Authored, explicitly UNRUN database gates

`test_delegation_database.py` contains 23 parameterized cases; `test_text_delegation_migration.py` adds one migration case. They were **not collected/executed against any database locally**. They reuse existing upload/migration fixtures and require a **new isolated kube1 snapshot at0007**, not an accepted historical image run.

Coverage authored: issuance/job rollback and exactly-once replay; failure of ticket INSERT aborting source/job publication; no historical backfill; direct scheduler/app principal isolation; owner ACL/RLS configuration and app denial of ticket reads/scheduling/role assumption; forged actor/scope/purpose/fence; revocation then restoration; current/deleted source; duplicate delivery; real delivery+backoff vs ingestion expiry; terminal publication before process death without second Storage read; attempts cap/replay; quota-blocked-actor fairness to another actor; and privileged SET ROLE rejection.

The isolated DB fixture temporarily enables synthetic LOGIN on the scheduler role and removes it/password in teardown. This is test code only, never executed here; never run it against a shared/production DB. PostgreSQL roles are cluster-wide: run these cases serially in their dedicated fresh cluster, not alongside another scheduler fixture. Storage is synthetic, not a new Storage-policy or live-provider proof. SQL syntax/runtime behavior, concurrency/lock races, actual ACL/RLS enforcement and true wire-COMMIT ambiguity still require independent review and kube1 evidence. The migration's role setup permits reuse only of safe NOLOGIN roles without memberships, allowing isolated migration databases on that same dedicated cluster.

## Next step / future supervisor API suggestions

1. Main independently reviews SQL/ACL/RLS, optional worker cancellation semantics, tests and diagnostic dispositions. Preserve all preexisting user changes. Update shared index/contract/plan separately; this handoff is not an activation decision.
2. E/Main arrange a **new** isolated kube1 snapshot and run the new DB gates plus relevant default-worker regressions. Include genuine competing schedulers, revocation/finalization/publication interleavings and connection failures, not just single-threaded success. Do not rerun Combined/D4/7a986f1 acceptances.
3. Only after acceptance, design a supervisor around explicit `claim`, `validate`, `renew`, `acknowledge`, `ExactTextConsumer.run_one` and `runner.close`. Add finite polling/backoff, actual delivery-heartbeat coordination, cancellation/shutdown ownership, runtime startup assembly, restart/outage proof and operational visibility for persistently ineligible/exhausted/unacknowledged tickets. Current consumer hides its active grant; future heartbeat ownership needs an explicitly reviewed API rather than introspection or an invented Identity.
4. E separately owns runtime role/connection provisioning and secret delivery. No runtime definer membership, credential reuse or generic actor parameter. Keep the capability disabled until those gates pass.
