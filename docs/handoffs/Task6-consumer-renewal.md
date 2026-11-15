# Task6 — S2a consumer-owned renewal

## Current status

**S2a independently accepted LOCAL ONLY.** Parent authenticated the final three source hashes and unchanged guard before/after its own combined run: **190passed/3.09s,570passed phases,zero external I/O,186joined executors**, exact worker case membership/order. Final static reviewer **f9064aab-99ad-40c3-bb13-ce078deda561** found **no issues**: original P1/P2 and invalid-attempt timestamp finding resolved; terminal truth/current-cycle fencing and normal/exceptional cleanup correct. Reviewer did not execute tests. Parent evidence: `.pi/task6-consumer-renewal-parent/timestamp-{tests.log,phases.json,replay-summary.json,diagnostic-assessment.json}`. Active LSP confirmed3paths with0visible findings, but2old Protocol dispositions remain filtered; exact raw17widget findings plus separately visible BLE188 were individually source-adjudicated. Parent Ruff default+explicit selections each exit1 solely BLE188, format3/diffcheckPASS; **not blanket lint-clean**. No new marks/suppressions/policy edits. S2b supervision/readiness and all native/runtime gates remain open.

**Preserved implementation/review history.** Retry-observation fix implemented and worker-local190-case replay complete. Addendum b6c758ff confirmed original P1/P2 fixed but blocked the155-pass candidate because aggregation hid invalid actual retry timestamps. Retained worker98794757 resumed71e611fd with strictly read-only HEAD/source/guard/staging inspection, full review/CONTINUE read, then explicit Main approval for per-event validation and the minimal terminal-truth seam below. Earlier155/142 evidence is historical, not proof of current bytes. Those corrected-source gates are now complete as recorded above; earlier review blocks and intermediate failures below remain historical evidence, not current failures. Reviewed implementation base is `b92883da2619263329150e65e39ebff87de722a0`; Main records the subsequent integration commit in CONTINUE. No native rollout is included. Only `delegation.py`, the approved narrow `text.py` observation seam, NEW `test_text_consumer_renewal.py`, this handoff and private `.pi/task6-consumer-renewal/` were owned. Shared dirty docs/evidence and existing tests are preserved; Main owns shared documentation.

The original Pi worker `448f3c83-54da-424d-9b3c-758315a7fbec` reached its **1800000ms local harness timeout after52 turns**. Main confirmed failed/zero active capacity and no matching local S2a pytest processes. The retained worker resumed with a strict read-only hash/status/source check and explicit Main finish-only release. No recovery production/test edits or deadline changes were made. All52 prior artifacts remain, with original bytes recorded in `recovery-summary.json`; no historical failure was relabeled. `consumer-design.md` had mode0664 and was restored to0600 during recovery; its bytes remained unchanged. This local orchestration recovery is not runtime/process-stop evidence.

## Retry-observation fix — current candidate

### Approved minimal semantics

- Private lifetime `_last_observed_at`, protected by the existing worker lock, validates **each actual producer event before** late-pulse ignore, last-record replacement or first-entry aggregation. Both times must be finite strict int/float, never bool; an actual commit cannot precede its own entry; each event must not precede the previous valid actual event. The watermark does not reset on retry/new claim, so100→100.1→100.05 cannot hide behind first100. Future entry1000 followed by109 is detected at the actual commit, without inventing an independent clock reading.
- Invalid entry latches `observation_failed` and raises fixed `ObservationUnavailable` before Session/terminal SQL opens. Invalid nonterminal commit likewise fails closed. Rejected values never enter public progress or caller callbacks; prior valid metadata remains. Valid per-attempt objects/order and original terminal-entry deadline remain unchanged.
- **Explicitly approved new read-only Python seam:** `TextWorker.terminal_committed`, a locked bool backed by `_terminal_committed`. Only `_commit_progress` after a successful terminal context exit sets it, before reading the clock; only a valid actual new ingestion-claim entry resets it. `_start_progress` and `_commit_progress` explicitly tell `_observe` entry versus commit: an actual `None` commit reading cannot masquerade as entry. No HTTP/config/factory/runner API was added.
- A malformed terminal commit timestamp latches the fault but returns the actual committed worker outcome normally. The truth bit remains true, metadata stays at the last valid observation (unknown commit timestamp stays `None`), and the invalid event is not delivered to the observer. No fabricated timestamp, clamp, or conversion of a known terminal commit to unknown merely because of these malformed readings. Consumer snapshots combine the existing **current-cycle terminal-record identity** with the truth bit, so known exit is visible even while the async worker response is still held.
- Consumer faults latch lease failure on both ordinary and exceptional final cleanup paths before phase classification, preserve caller cancellation, refuse ack and further admission. After stopping/joining renewal, any still-pending actual runner Future also forbids ack and retains uncertainty/slot ownership. This does not reject the independently owned renewal before its normal join. An unresolved late pulse can coexist with known terminal truth; its own pending outcome remains uncertain.
- Default Identity observation behavior, original first-entry deadline fix, direct whole-clock check, transaction policy/classifier, SQL, shared runner ownership and existing52/103 tests remain unchanged.

### Actual RED/GREEN and coverage

New35 cases: publish/finish ×14 malformed actual retry sequences (28), four healthy two-/three-attempt controls, two `None`-commit pending-response snapshots, and one cancelled actual watcher/pulse-Future fence. Negative sequences include entry99/NaN/None/bool/string/infinity; three attempts100→100.1→100.05; entry1000→commit109; entry100.2→commit100.1 despite first100; and commitNone/NaN/bool/string/infinity. Controls assert no rejected callback/metadata, no new DB work on rejected entry, real post-exit truth on bad commit, sticky sanitized faults, no ack/new claim and readonly truth property.

All retry cases invoke actual `_publish`/`_finish`, actual decorator/classifier and actual bounded runner. The canonical typed psycopg55P03/`quota_gate_busy` synthetic rollback/session close precedes real wrapper backoff; unchanged three-attempt/250ms admission uses sane100→100.1→100.2. Only `text.monotonic` producer readings supply malformed samples; there is no fabricated progress or `__wrapped__` bypass. Healthy109 commits retain the exact observed per-attempt objects and ack normally.

| Artifact prefix | Actual calls / duration | Other phases / executor joins | Classification |
| --- | --- | --- | --- |
| `timestamp-fix-red` | **30failed +4passed /3.69s** | 68passed /34joined | Genuine RED:20 actual forbidden `confirmed` acks,8 missing producer stickyfaults,2 missing known-exit snapshots before async reply |
| `timestamp-fix-green` | **14failed +20passed /1.95s** | 68passed /34joined | **Misnamed file; NOT GREEN.** Early entry exception bypassed the post-result consumer branch and left `lease_failed=False`; source/logs retained |
| `timestamp-fix-selected2` | **35passed /0.94s** | 70passed /35joined | Actual GREEN after Main-approved final-cleanup latch; new late-pulse control has GREEN coverage, no claimed prepatch RED |
| `timestamp-fix-new` | **87passed /2.40s** | 174passed /86joined | Entire final new file |
| `timestamp-fix-combined` | **190passed /2.80s** | **380passed /186joined** | Final87 + unchanged103, all570 phases passed, zero skips/xfails |

Every run used the unchanged pre-collection guard, scrubbed `env -i`/HOME `/nonexistent`, disabled plugin autoload/cache/bytecode and20s command bound; no command timed out, no deadline was enlarged. Zero external-I/O attempts for all runs; all setup/teardown phases passed. Every barrier is released in finally; Delivery joins its owned tasks/runner and asserts no fake-clock waiters; guard joins every captured executor thread. The late-pulse case uses the actual watcher, actual cancelled waiter and genuinely pending Future; the separate pending-response tests explicitly prove the runner Future already settled. Neither is described as simultaneous SQL on the shared runner.

`timestamp-fix-summary.json` verifies membership/order and per-node setup/call/teardown exactly once. Old52 test source bytes are an exact prefix; final combined membership is new87 then unchanged103. All104 preexisting private artifacts retain bytes/hashes/modes, including previous155/142 histories, first timeout and incorrect-fixture versus genuine13-case RED records. Private directory/files remain0700/0600. All new artifacts use exclusive `timestamp-fix-` names; source snapshots are DATA only.

| Final source | Bytes | SHA256 |
| --- | ---: | --- |
| `backend/app/workers/delegation.py` | 28580 | `d3a7f8f1cb29ce944fed53581ecb48ffa12a2619f90125f15df01ac87c9935ca` |
| `backend/app/workers/text.py` | 18450 | `484f41be41fade170335d224961eb57f6513420801b1aa59737b1c37ef6f920b` |
| `backend/tests/ingestion/test_text_consumer_renewal.py` | 61904 | `68ec58e39d37feda715130679a3303580fcbf4fc231a32b38e82f0c47d451474` |

Hashes are unchanged across final tests (`timestamp-fix-final-{before,after}.sha256`). Combined phaseJSON SHA256 `d7cdb013ef5f846f89fca0da7cf638ec3c155ffb3d3af8660ea7d783ec74c70c`; guard still `4d1e74bbed072bf7a8209e8d5e4ed718e7d8f1f6c7a0161d0da5f0bddc8c3033`. Minimal delta and final DATA snapshots are retained. Test commands use the existing prefix below, targeted `-k actual_retry_timestamp`, full-file selection, then the same S1 file + regression selectors for combined.

### Exact diagnostics and remaining gate

Scoped Ruff default and explicit `E,F,I,B,BLE`, always `--no-cache`, each exit1 **only** for observer-boundary BLE001 now text188. Format-check3files and diff-check PASS. Only appended test formatting was applied, explicitly `--no-cache`; original52 prefix remained unchanged. Early test diagnostics1288/1392 reported missing `terminal_committed` before its implementation; Main inspected the actual readonly property127 and authorized those two now-obsolete identities only. No marks/suppressions/disposition changes were made.

Final raw tree-sitter advisories `cq:b27006b7a237` text394 and `cq:5cf726a32256` text409 say unreachable-except. Main independently confirmed each is the first typed QuotaUnavailable handler in its try and identical to accepted S1; narrow continuation approved. BLE188 was separately source-adjudicated as the external observer privacy/cancellation boundary, not lint-clean. All5delegation+9text execute/add/flush ASTs remain identical to pristine S1; current fixed/bound SQL sinks delegation229/237/238/239/241 and text240/244 were individually reconciled. No blanket diagnostic waiver: the subsequent final Parent active/raw gate and reviewer addendum are recorded in Current status above.

No shared docs, old tests, transaction/classifier, SQL, runtime/startup/health, staging or commit changed. No native DB/Storage/container/SSH work. The earlier documented S2a coverage limits below remain; **20 DB worker cases, N1/N2 and native/wire-COMMIT remain UNRUN**. This focused evidence covers malformed clock return values and event ordering, not a new general clock-exception watchdog or S2b supervision.

## Historical original P1/P2-fix evidence

**Minimal production delta from the reviewed candidate:** `_observe` keeps the existing first terminal record through retry entries, and combines that first `started_at` with a later actual committed timestamp. Only an actual new ingestion-claim entry resets it. Known terminal commit is not erased by another entry. `last_progress` and caller observer receive the unchanged actual per-attempt objects/times/order. No public field/callback/runner API added. `_execute` directly compares the clock to the original whole deadline immediately after wait wakes; existing timer-result/error handling and joins remain.

New13 controls were explicitly required by Main's P1/P2 fix assignment, not scope expansion:

- Publish/finish × on-time109 / late110.05: actual `retry_transaction` decorator, actual `_publish`/`_finish`, real shared runner, synthetic transaction first raises canonical typed psycopg55P03/`quota_gate_busy` through the real classifier. Rollback/context exit/session close occur before the real wrapper's backoff seam admits the second attempt at100.1, within its unchanged250ms budget. Actual observer sequence remains `(100,None),(100.1,None),(100.1,commit)`. Terminal aggregate retains100;109 can ack,110.05 cannot and committed truth remains known.
- Publish/finish × renewal false/error ×107/110.05: terminal Future really settles first, then only its async response is held, allowing renewal on the same runner. Failure at107 gets original deadline110, not110.1; at110.05 gets no new settlement wait. **This response-delay control is not evidence of simultaneously pending SQL and renewal on one runner.** It explicitly asserts runner not pending after renewal failure. Existing pending-Future controls remain unchanged.
- Actual ingestion claim returns None after fake whole deadline101 while expired fake sleepers have not resumed. Direct clock enforcement returns `lease_lost`, latches failure/uncertain, and performs no read/ack instead of accepting `not_claimed` as settled.

Actual sequence (all under unchanged pre-collection audit/DB-denial guard, no external I/O, all setup/teardown passed):

| Prefix | Calls | Other phases / joined executors | Interpretation |
| --- | --- | --- | --- |
| `fix-review-red` | 13failed /17.60s | 26passed /13joined | Four genuine P1 first-entry failures + one genuine P2 result failure; **eight fixture barrier timeouts are NOT defect RED** |
| `fix-review-red2` | **13failed /1.54s** | 26passed /13joined | Corrected genuine RED:2 first-entry metadata,2 actual late ack `confirmed` vs `not_attempted`,8 extended settlement,1 whole result |
| `fix-review-green` | **13passed /0.51s** | 26passed /13joined | Targeted correction GREEN before formatting |
| `fix-review-new` | **52passed /1.11s** | 104passed /51joined | Entire new file, final formatted bytes |
| `fix-review-combined` | **155passed /1.99s** | **310passed /151joined** | Final52 + unchanged103; all465 phases passed, zero skips/xfails, exit0 |

The first fixture wrongly accessed `__name__` on the repository's `functools.partial`, aborting before terminal work. It exited normally below20s with all teardowns; no lingering matching pytest process. Main received HOLD and approved exactly the optional-name lookup and early task-error propagation before one corrected RED run. Original failing bytes/logs remain. RED1 and RED2 phaseJSON hashes happen to match because nodeids/phase outcomes match; their logs and source snapshots prove different causes and bytes. No failure was erased or relabeled.

`fix-review-summary.json` verifies exact node/phase counts; old39 test source bytes remain an exact prefix, added13 nodeids equal targeted selection, and final combined membership/order is new52 then unchanged103. All historical phase files and original52 artifacts retain hashes. Guard joins151 executors/threads; explicit test finally blocks release barriers, join owned children and leave no fake-clock waiters. This remains local synthetic DB-boundary evidence, not wire-COMMIT/native proof.

Final source hashes are unchanged before/after the full runs (`fix-review-final-{before,after}.sha256`), with final DATA snapshots and minimal diff retained:

| Source | Bytes | SHA256 |
| --- | ---: | --- |
| `backend/app/workers/delegation.py` | 28320 | `13472a09bf465b77d921f0b26f2bbd6288edf6c064572984b52a3e096b458156` |
| `backend/app/workers/text.py` | 17009 | `afa0035f03a67b54cb72097100519b942ebf4b95a9517554d4d29f1f3fc3eed6` |
| `backend/tests/ingestion/test_text_consumer_renewal.py` | 45997 | `768642ec90d091ce4fe4be105d0b83d6a5728b79564e8cde0f063b89c2d65845` |

Combined phaseJSON SHA256: `6483b25eb37625611503aa9dcdd4eef02a2c957956e026d78814bcc193546430`. Guard remains `4d1e74bbed072bf7a8209e8d5e4ed718e7d8f1f6c7a0161d0da5f0bddc8c3033`. `transactions.py`, quota classifier and old tests remain unedited. Command prefix/environment below is unchanged; selected runs add `-k 'terminal_retry or whole_deadline_checks_clock'`, full run selects the new file, combined appends S1 plus the same ten regression selectors. Each test invocation remained bounded by20s without domain-deadline changes; all new artifacts use `fix-` prefixes/private0600.

Final Ruff default and explicit `E,F,I,B,BLE`, both `--no-cache`, each exit1 solely for known observer-boundary BLE001 now `text.py:162`. First pre-format check reported text wrapping (exit1); source snapshot retained, explicit text-only `ruff format --no-cache` applied. Final format-check3files and diff-check PASS. Exact relocated SQL sinks text211/215 were re-read and individually authorized by Main; all5delegation+9text execute/add/flush ASTs remain identical to accepted S1. Automated withheld SQL notices are not a blanket clean diagnostic report; Parent active/raw gate remains required.

Two temporary fixture typing findings on the synthetic Session's test-only status were fixed at that injected adapter boundary with a documented `Any` cast; intermediate constant-getattr/setattr lint findings were removed, no suppressions. Two incorrect read-only lookup paths (`app/ingestion/transactions.py`, `ingestion/test_transactions.py`) produced inspection exit2 before locating the actual imported module; no tests then. No retry policy, classifier, SQL, old evidence, shared docs, stage/commit or native environment changed.

## Implemented contract

- Frozen `ConsumerPolicy`: lease60s (integer1..300), renewal15s (strictly less than half lease), scheduler10s (at most10 and less than lease), whole120s (at most120), terminal settlement10s (at most10). Values reject bool/nonfinite/nonpositive input. Lease300 and whole120 are deliberately independent.
- Frozen sanitized `SchedulerProgress` and `ConsumerSnapshot`; synchronous sticky `request_stop()`. Snapshots contain no grants, IDs, token, result or raw exception. Actual scheduler entry and **after all factory context exits** are distinct; SQL and repository public method signatures/authority are unchanged.
- One private grant and one renewal task/at most one outstanding renewal on the **existing shared runner**. Lease validity is admission/start + duration, not response/commit-receipt time, and is accepted only on a successful response. Renewal covers validation/execution/reconciliation; stop/join precedes at most one ack. Whole deadline never slides with renewal. Cancelled coroutines do not settle the underlying Future; actual pending runner work keeps admission busy.
- Delegated TextWorker always records internal observation, even without a caller observer. Separate read-only terminal entry/commit survives nonterminal progress. Caller observers are not replaced/wrapped; sticky observation faults and default Identity behavior remain unchanged. Current-cycle terminal association uses record identity, not floating-point timestamp equality.
- Cancellation, lease failure, unknown outcome, known committed terminal and acknowledgment are separate. External cancellation propagates even during repeated child joins; late callbacks cannot revive ownership. A known commit is retained even when ack cannot be confirmed. `request_stop()` refuses new claims but permits an already healthy cycle to finish within original bounds.

**Terminal10 is NOT a proactive healthy-publication watchdog.** After internal renewal/lease failure, terminal settlement waits only until the original actual terminal entry+10, clipped by whole deadline: failure at entry+7 has3 left, entry+12 has none. Ordinary late terminal results cannot authorize ack/healthy acceptance, but known terminal truth is not made unknown merely by latency. No new runner/wake/event API was added. S2b owns proactive healthy terminal, read/decode/chunks/control-loop/grace watchdogs, supervisor and readiness. These bounds are cooperative detection, not physical thread preemption.

## Historical pre-review recovery evidence

All four recovery runs used the unchanged audit/DB-denial guard **before collection**, scrubbed environment, plugin autoload/cache disabled, no bytecode, and new exclusive evidence paths. Every collected node had exactly one passed setup/call/teardown, zero skips/xfails, zero external-I/O attempts and exit0.

| Run / artifact prefix | Cases | Passed phases | Joined executors | Duration |
| --- | ---: | ---: | ---: | ---: |
| `recovery-targeted` — final two RED controls | 2 | 6 | 2 | 0.39s |
| `recovery-new` — entire new file | 39 | 117 | 38 | 0.84s |
| `recovery-s1` — unchanged78 + ten selectors expanding25 | 103 | 309 | 100 | 1.28s |
| `recovery-combined` — new + existing in one process | **142** | **426** | **138** | **1.73s** |

Each prefix has `.log` and `-phases.json` in the private directory. Combined collected membership/order equals new39 followed by existing103 exactly; existing103 equals the original `s1-first` membership/order. `recovery-summary.json` verifies all node/phase counts, hashes, source byte sizes, preserved original52 artifacts and private modes. Executor guard calls shutdown(wait=True) and verifies every captured thread dead. Tests explicitly join owned coroutine children; `Delivery` cleanup asserts no fake-clock waiters remain. This is not a process-wide async-task census or a physical native-worker kill proof.

Combined phaseJSON SHA256: `cdcdfd10b1d14ef7c9b51a5666c7dd210ead602482cf602a71ae0237fc7e7e00`.

Unchanged source hashes before/after all recovery tests (`recovery-before.sha256` / `recovery-after.sha256`):

| Source | Bytes | SHA256 |
| --- | ---: | --- |
| `backend/app/workers/delegation.py` | 28229 | `8dda8fc6b62063a7c6b63abfcd3bba7a38d9b9ad6093e24da29ab8aea143e484` |
| `backend/app/workers/text.py` | 16367 | `2a5513b1f9f169a61f80a059f7d5b7f5f588369422e01b441a7d2e3950bae575` |
| `backend/tests/ingestion/test_text_consumer_renewal.py` | 35488 | `ad6007c5ad5a0352302cb8a9c58f1f99939fac5165860d59beff006ae8a7a55a` |
| unchanged S1 guard | 2618 | `4d1e74bbed072bf7a8209e8d5e4ed718e7d8f1f6c7a0161d0da5f0bddc8c3033` |

Exact executed common command prefix (each invocation used the stated20s local tool bound, not a consumer policy override):

```bash
env -i PATH=/usr/bin:/bin HOME=/nonexistent \
  PYTHONPATH="$PWD/.pi/task6-supervisor-lifecycle" \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -B -m pytest -p no:cacheprovider -p guard \
  --lifecycle-evidence=.pi/task6-consumer-renewal/<prefix>-phases.json -q --tb=short <selection>
```

Selections: final two exact nodeids `test_repeated_external_cancel_during_final_renewal_join_releases_admission` / `test_settled_phase_timestamp_is_actual_post_ack_exit`; whole new file; `test_text_supervisor_lifecycle.py` plus `mapfile -t nodes < .pi/task6-supervisor-lifecycle/regression-nodeids.txt` and `"${nodes[@]}"`; combined new file followed by that existing selection. All logs redirected into the same new prefix. `umask077`; directory0700/files0600. Snapshotted `.source` files are DATA only and were never executed.

## TDD provenance — actual outcomes, not inferred

All historical phaseJSON files retain actual exit codes, zero I/O and joined executors. Source/test byte snapshots, hashes and byte counts are retained/hashed in `recovery-summary.json`.

| Stage | Actual RED | Subsequent actual GREEN |
| --- | --- | --- |
| API | `api-red`:3failed on pristine S1 API | `api-green`:3passed |
| Renewal integration | `renewal-red`:1failed+3passed, missing constructor injection API | `renewal-green`:4passed; not a full behavioral RED |
| Initial race matrix | Misnamed `races-red`:**21passed, NOT RED** | Historical characterization only |
| Timer-join cancellation | `join-red`:1failed because external CancelledError swallowed | `join-green`:22passed |
| Queued renewal vs terminal truth | `extended`:1failed+36passed; normal successful worker misclassified unknown solely because renewal occupied runner | `queued-green`:37passed |
| Final repeated cancellation | `final-join-red`:1failed/2other phases passed; busy admission leaked after second cancellation during final join | `recovery-targeted`:control passed, full39/combined142 passed |
| Settled timestamp | `phase-red`:1failed/2other phases passed; timestamp still pre-ack | `recovery-targeted`:control passed, full39/combined142 passed |

The last two source fixes preceded the harness timeout. The previous37 GREEN was for older bytes; **recovery results established GREEN for that earlier candidate, subsequently blocked by P1/P2 review**. Historical `queued-green` SHA `8b06b1edc9aad231fc328fc0bee978f224598b5ac176c7f1ff9a9708f1e82975`, `final-join-red` SHA `24f73d5c895c0990062af0b1f71c9d73a949ac6a9f4dd9efe7026ca0c9c9b368`, `phase-red` SHA `4a58465bbc92360e561e2a9b3b32924d62dc4cd9fbc2b20b0234b1dedc91faaf` remain unchanged.

## Required matrix mapping / limits

New source-named tests cover:

- Policy independent whole/lease and invalid inputs; frozen sanitized snapshot without timer-refreshed progress.
- Actual repository factory exit vs entry; no caller callback needed for terminal record; exact grant renewal/shared thread/no backlog; admission before a queued runner operation (101+60=161, not actual commit104+60).
- False/error/timeout before publication: no conflicting finish or ack, pending future remains busy; late committed claim/renew replies cannot extend ownership from receipt.
- Both publish/finish timeout at original terminal entry+7 and +12; ordinary late healthy committed publication retains truth but denies ack; whole delivery stays40 through repeated successful renewals.
- Reconciliation ack false/error/success independent from terminal truth; queued renewal false/error/success after actual publication must settle before ack, no post-ack renewal.
- Cancellation during claim/validate/renew/ack with actual unresolved executor then late commit; no same-call replay; timer-join and repeated final-join cancellation; ordinary new selection after quiescence rather than replaying private grant.
- Current-cycle terminal identity against stale previous terminal at identical clocks and real new terminal at identical clocks; backwards/nonfinite/future-domain rejection; sticky stop with healthy active completion; post-ack phase entry time.

The unchanged103 include observer faults/stickiness/privacy, real pending-future fences, terminal-vs-late-pulse truth, default Identity, exact grant fresh validation, terminal reconciliation, outstanding-delivery serialization and cancellation of publish/finish. They were not edited to accommodate S2a.

**Explicit coverage limits for reviewer:** no separate new control forces a renewal tick while validation/reconciliation is held (implementation starts renewal before validation, but dedicated timer-race proof is absent). Settlement controls prove original +7/+12 cutoffs; no dedicated new control releases terminal successfully inside the remaining3s or clips settlement to a shorter whole deadline. Policy300/120 is constructor-tested, not a300-second runtime test. These are not claimed as observed outcomes; do not silently widen scope to add them during finish-only recovery. Parent/reviewer decides sufficiency/follow-up. Synthetic transaction exits are not PostgreSQL wire-COMMIT or actual Storage evidence.

## Diagnostics and operational boundaries

- `ruff check --no-cache` scoped to the two production files/new test: **exit1**, only known `text.py:150` BLE001 observer privacy boundary. Separate explicit `--select E,F,I,B,BLE` scope has the same exit1/finding. No new suppressions/marks. Logs are `recovery-ruff-{default,explicit}.log`.
- `ruff format --check --no-cache` scoped3files: PASS; `git diff --check`: PASS. Both recorded. No recovery formatter mutation.
- Exact narrow previously adjudicated SQL findings: delegation229/237/238/239/241 fixed principal/SET/SHOW/private query+bound parameters; text199/203 bound claim advisory lock/count. AST comparison confirms all5delegation and9text execute/add/flush calls identical to pristine accepted S1. This is NOT permission to execute SQL, suppress rules or call the full tree clean.
- Automated recovery tool notices still withheld5delegation/3text diagnostics with a10s analysis timeout; child has no active lens tool. Parent owns final fresh LSP/raw-identity reconciliation. Earlier cached advisory identities and legitimate Protocol signatures remain as described in the [S1 handoff](Task6-supervisor-lifecycle.md), not blanket-waived new findings.
- First new-test write auto-sorted an import; tool's cache flag was unknown. Every explicit Ruff command used `--no-cache`. No dependency/cache/lock/native packet cleanup or existing evidence edits.
- Docs-only automatic spills `/tmp/pi-bash-87d83591fbae7bf4.log` and `/tmp/pi-bash-9049a2b5180b3811.log` preserved. Inspection exit1 for absent `backend/pyproject.toml` (root config then read), absent `backend/tests/conftest.py`, and recovery no-match process grep are not test failures. Original worker harness timeout is retained as an orchestration failure.

No SQL/role/health/startup/config/runtime/capability changes, external I/O, native run, staging or commit. **Existing20 DB worker cases, N1/N2, real DB/Storage/process-stop/wire-COMMIT remain UNRUN for S2a.** Next: Main independent combined replay, exact diagnostics reconciliation and reviewer gate; only Main updates shared docs/accepts S2a or authorizes subsequent work.
