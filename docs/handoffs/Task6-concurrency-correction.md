# Task6 local concurrency correction — review candidate, native HOLD

## Authority and boundary

Parent explicitly adopted the independent review's **ALTERNATIVE**: two concurrent
**direct fixed-SQL claims**, not concurrent repository claims. Production repository,
0006/0007 migrations and all 23 delegation cases are unchanged. The final validation
still calls the fixture's real `DelegationRepository.validate` after contenders finish.
See [review](../review/task6-concurrency.md) and the immutable local diagnosis
`.pi/text-delegation-concurrency-diagnosis/REPORT.md`.

The native e54c767 concurrency failure's actual cause remains **UNKNOWN**. This
corrects the demonstrated P1 source-level false-negative only; it is neither native
acceptance nor SQL repair. Old source/evidence archives were not edited or rerun.

Sole source-writer scope: `tests/security/text_delegation/`, this new handoff and new
`.pi/text-delegation-concurrency-correction/` scratch. Parent additionally approved
**only** duplicate-object-key rejection at the two operator report-ingress sites:
parse dependencies once with existing `unique`, and parse each test report line
with `unique`. No operator command, mutation, cleanup or budget behavior changed.
`prepare.py`, `receive.py`, Compose, phase cases and Node exclusions are untouched.
Shared README/contracts/CONTINUE/plan/log/review documentation remains parent-owned.

## Implemented

- `concurrency.py`: small direct contender and lifecycle-aware collector. Independent
  engine/connection/transaction per contender; exact direct current=session scheduler,
  non-super/non-bypass role and READ COMMITTED checks; startup connect3/statement3/
  lock1 and pool1 remain in the packet adapter. Two open transactions and actual
  positive distinct backend PIDs, `Barrier(2, timeout=5)`, exactly one 60-second claim
  per contender, no retry.
- Only actual SQLAlchemy `DBAPIError` caught immediately around the exact fixed
  claim execute can become busy, using existing `classify_quota_error`. Plain driver
  exceptions, arbitrary `DelegationUnavailable`, wrong state/message, result decoding
  errors and busy at any other stage remain failures.
- Grant/idle publish only after confirmed commit AND connection close; busy only
  after full rollback AND close. Engine disposal and runner closure are distinct.
  Failed commit stays unknown despite later rollback. Failed rollback is not retried.
  First failure stage survives subsequent cleanup failures.
- One shared 12-second collection deadline, then concurrent runner-close budgets3.
  Timeout fails, cancels only waiters, and does **not** assert thread death, rollback
  or connection closure. Unknown/unreturned observations stay unknown. Custom DB
  executor threads may outlive collection and Python process exit may wait for them;
  outer packet process/container lifecycle bounds remain necessary. No threadkill claim.
- Accept exactly unordered grant+idle or grant+busy, with correct job, distinct actual
  PIDs, persisted matching ticket fence and fresh executable real-repository validation.
- Closed diagnostic v1: fixed stage/outcome/transaction enums, strict booleans, counts,
  lifecycle confirmations. No raw PIDs, URLs, SQL, params, errors or capabilities.
  `run_packet.py` producer and `protocol.py` consumer reject malformed evidence.
  Missing/failed diagnostics can remove success, never override pytest failure.
  Strict duplicate JSON keys now reject at operator ingress and phase-file loading.

## Actually executed LOCAL checks

All commands ran in the local workspace; no SSH/network/container/database/auth/
secret access, staging, commit, archive, transfer, native retry or `run_packet.main`.
Python test commands used `env -i`, no DB context, disabled plugin autoload/cache,
`-B`, and packet/backend PYTHONPATH. Synthetic tests additionally deny real connect,
DNS, subprocess and engine creation. No application DB fixtures were run.

1. Initial TDD collection RED: new helper absent (`ModuleNotFoundError`), recorded in
   scratch `red.txt`. This is an implementation-start RED, **not** the behavioral
   contention proof.
2. Existing immutable diagnosis already demonstrated actual old test-function
   grant+busy RED before durable checks (8 scenarios, not rerun here). New controls
   execute real repository sanitization for the same exact typed busy path and
   assert its legacy `DelegationUnavailable`; the corrected helper/collector accepts
   permitted pairs in both winner orders. A real Python nonblocking gate held until
   the competing attempt drives the new helper/collector to GREEN with one durable
   synthetic winner. This is a local countermodel, not PostgreSQL evidence.
3. Duplicate-ingress TDD: two new controls driving actual operator `execute` against
   fake commands were RED (`0 != 1`) before the approved change; now GREEN. Evidence
   `duplicate-red.txt`. Dependency top-level and nested diagnostic duplicates covered.
4. Final lightweight run:
   ```sh
   env -i PATH="$PATH" HOME=/nonexistent PYTHONDONTWRITEBYTECODE=1 \
     PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=backend:tests/security/text_delegation \
     .venv/bin/python -B -m pytest -q -p no:cacheprovider \
       tests/security/text_delegation/test_concurrency_correction.py \
       tests/security/text_delegation/test_text_delegation_packet.py \
       backend/tests/ingestion/test_delegation.py
   ```
   **220 passed**: 115 new concurrency synthetic cases, 80 packet unit cases
   (78 existing +2 new), 25 ingestion delegation lightweight cases. Scratch `green.txt`.
   Earlier incremental run: 217 passed before three final focused controls.
5. Ruff check and format check passed on the packet directory and scratch collector.
   In-memory `compile()` and `git diff --check` passed. Index empty verified.
6. Collection **only**, with scrubbed no-DB environment and connect/DNS/subprocess
   guards: scratch `collect_contracts.py` ran once to derive exact case keys and
   update only newly added synthetic cases/files. No accepted/native tests executed.

Coverage includes invalid outcome pairs, wrong job/fence/final state, real-repository
final validate, exact error provenance, role/isolation/PID/barrier errors, unknown
commit, rollback/connection-close/dispose failures, first-stage preservation,
publication ordering, no retry, collection timeout and unresolved/raising runner
close; producer and consumer malformed keys/enums/indices/types/counts/lifecycle
controls; actual pytest Reporter cannot hide failed/missing diagnostics or call failure.

## Derived collection contract (not a default-suite execution)

- Regression cases **1414 →1531**, delta **117** (115 new synthetic concurrency cases,
  2 duplicate-ingress cases); files **70 →71**.
- Existing case order preserved; no old cases removed/renamed. Node exclusions **40**
  unchanged. External/research skip contract **40** unchanged (39 cases in the four
  external files +1 named research-only case). Future default expectation would be
  1491 passes/40 reviewed skips, **not executed/accepted here**.
- Phase contracts byte-identical: migration1, delegation23, concurrency1, worker20,
  dispatch21, focused37. Scratch `collection.json`/`collection.txt` retain counts.

## Tooling dispositions and limits

Parent adjudicated the unchanged `run_packet.py::principals` fixed ALTER ROLE plus
`psycopg.sql.Literal` as an exact scanner false positive, without suppression or DDL
change. Parent independently confirmed active run_packet LSP:1 checked/0 diagnostics;
this is LSP evidence, not removal of the static finding. Parent also adjudicated
three synthetic `super().execute(query, params)` scanner findings: they dispatch
only to in-memory `Engine`, never a real SQL/driver base. Genuine Python typing
issues were corrected (explicit trusted synthetic Session casts, callable typing).
No broad scanner suppression or production change was made.

## Parent verification and independent review

Main independently executed the stated focused command: **220 passed in17.14s**, no DB/network/container context; Ruff and format9passed, diffcheck passed. Main independently compared the regression manifests against committed e54c767: all old selected cases/order preserved,117new cases exactly115helper/diagnostic+2JSON-ingress,1531selected+40excluded=1571total. Focused cases, Node exclusions, Compose, prepare and receiver remain byte-identical. Active LSP7:5confirmed/2unconfirmed (one timeout and one silent push-only server), no visible findings; not a blanket clean verdict.

Independent reviewer **e9e68065-074e-43a8-91e6-de2c34a6280f** read complete new files and baseline/diff: **no issues found, merge verdict OK for packet-only correction**. Verified transaction lifecycle, exact claim error boundary, closed diagnostics, unchanged operator behavior beyond approved report ingress and sufficient local controls. No additional local test gap identified. No commands were executed by the reviewer, and native cause remains unknown. Parent accepted this local correction only; no native run follows without a new snapshot and explicit authorization.

## Remaining gates

1. Local parent verification and independent corrective review completed as above.
2. Parent synchronized shared boundary/contracts/index/status documentation. This handoff
   does not authorize production activation.
3. Only after review: a **new** immutable snapshot/packet and separate explicit
   authorization for kube1. No old e54c767 source/runtime/lock/evidence retry.
4. Real PostgreSQL SCRAM, PIDs, SQL scheduling, commit/rollback/closure and the full
   packet/default regressions remain unrun for this correction. Native0007 stays
   FAILED/HOLD; no deployment/runtime/BWS/worker capability is enabled.
