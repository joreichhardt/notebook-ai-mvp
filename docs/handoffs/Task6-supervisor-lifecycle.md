# Task6 — S1 local lifecycle/observation acceptance

## Status and ownership

**Implemented and independently accepted for bounded LOCAL S1 only.** Reviewer203a5b83 found no code issues; follow-up **e8632244-aa21-4381-ab8b-f8a1f0dfab90** accepted the exact diagnostic reconciliation and parent replay gate with no issues. Parent independently authenticated source/guard hashes before and after **103passes/1.26s,309passed phases,zero I/O attempts,100joined executors**. No new native acceptance or blanket lint clearance follows. Base `integration/notebooklm` / `5f337cc3e98a87a6f21cf24398c3c98fc956d76a`. [N0](Task6-native-5f337cc.md) was independently accepted before this explicitly assigned S1. This candidate is NOT S2, a native packet, runtime assembly or readiness/activation approval. No commit/staging performed.

Writer changed only:

- `backend/app/workers/delegation.py`;
- `backend/app/workers/text.py`;
- new `backend/tests/ingestion/test_text_supervisor_lifecycle.py`;
- this new handoff;
- new private `.pi/task6-supervisor-lifecycle/` evidence.

Existing shared/index/plan/status documents, old `.pi/` evidence/packets, SQL/migrations/grants, test_delegation.py and test_worker.py are unchanged by this writer. Main owns shared documentation and the independent review gate. Parent approved the exact API before production/new-test edits, then narrowly approved terminal-pulse retention, the observer exception privacy boundary and the late-cancellation admission correction.

## Implemented API and invariants

`BoundedThreadRunner.snapshot() -> RunnerSnapshot` returns a frozen value:

- `generation: int`: process-local monotonic runner lifetime number, never reused by close;
- `sequence: int`: increments only for actual executor submission;
- `closed: bool`, `pending: bool`: pending reads the retained actual concurrent Future, not asyncio waiter state;
- `phase: Literal['idle','submitted','running','completed','closed']`;
- `submitted_at`, `started_at`, `completed_at`: monotonic `float | None`.

A threading lock protects metadata. The existing single executor invokes a wrapper recording actual callback entry and finally-return/raise. No arguments/results/exceptions/grants/IDs/tokens enter snapshots. Callback completion does NOT imply future quiescence, successful DB operation or known COMMIT; a deterministic test holds the real future after callback return to prove this distinction. Close latches closed before waiting, remains false on unresolved actual work within the requested <=3s bound, and never clears the only pending future or spawns a replacement. Late completion can fill its own timestamps without reopening the closed generation. Existing `__call__` and `close` signatures remain unchanged.

`TextWorker(..., progress_observer: Callable[[TextProgress], None] | None = None)` is optional and allowed only with an operation runner. Noncallable/coroutine/async-generator configurations are rejected. `TextProgress` is frozen with only:

- `phase: Literal['ingestion_claim','pulse','publish','finish']`;
- `started_at: float` (actual method-attempt entry);
- `committed_at: float | None` (None means entry, NEVER committed health).

Callbacks run synchronously in the delegated operation thread and must return None; they must be trusted, quick and non-awaiting. Readonly thread-safe `last_progress: TextProgress | None` is stored **before** callback invocation; `observation_failed: bool` is a private sticky latch exposed readonly. No callback can clear it through return values. The observer is not an authorization source and these records are not a readiness implementation.

Actual `_claim/_pulse/_publish/_finish` call entry observation before opening their transaction; commit observation occurs ONLY after `_session` has exited its transaction AND session contexts successfully. Flush, SQL RETURNING, executor admission and coroutine scheduling do not count. A no-work claim has no committed ingestion-claim observation. Existing short-transaction quota retry behavior, SQL, fencing and authorization are untouched.

Observer Exception/injected CancelledError/non-None return fail closed with fixed `ObservationUnavailable('observation_unavailable')`, never a retryable DB/lease/Storage error. Returned coroutine objects are closed, never awaited. SystemExit/KeyboardInterrupt are not swallowed. Entry failure prevents DB opening; nonterminal postcommit fault prevents further Storage/publication/finish. A terminal postcommit observer fault keeps real worker `succeeded`/`failed` truth and internal committed progress, but the consumer rejects with fixed `DelegationUnavailable` before acknowledgment. Future cycles refuse the sticky fault. No raw observer error is logged or placed in observations.

A late pulse still executes ordinary SQL/authorization and can raise LeaseLost, but cannot replace a committed publish/finish progress record or invoke a pulse observer after that terminal record. Only a fresh actual ingestion-claim entry replaces it (with committed_at=None until its own successful context exit). This prevents late watchdog cleanup from erasing terminal truth; it does not extend a lease or create health.

Consumer admission now returns existing `busy` while the actual shared runner future is pending, including after a cancelled consumer waiter. After pending=false it freshly reads the sticky fault before claim. This prevents a previously enqueued claim from slipping past a late terminal observer failure. Ordering has no await between these checks. It depends on the existing trusted single-consumer, one-loop, shared-runner ownership contract; it is not cross-process scheduling authority.

Default no-observer/Identity behavior remains unchanged, including its synchronous publication and external-cancellation finish boundary. Normal consumer result vocabulary is unchanged. External task CancelledError still propagates; delegated cancellation starts no conflicting finish/ack.

## Actual deterministic test evidence

Local project `.venv` Python3.12.3, Ruff0.17.4. Synthetic DB/session/lease helper doubles exercise the **actual TextWorker method bodies and `_session` context exit ordering**; no real Storage/DB/network/subprocess is used. These are NOT wire-COMMIT, process-stop, RLS or real driver acceptance.

| Stage | Actual result | Meaning |
| --- | --- | --- |
| Original runner RED | 4FAIL/0.49s | Missing snapshot API on original working production bytes |
| Runner GREEN | 4PASS/0.30s | Actual thread entry/return, pending slot after cancelled waiters, sticky closed generation |
| Original observer RED | 51FAIL+4PASS/3.24s | Missing observer API on original text.py; NOT51 behavioral failures |
| Observer GREEN | 55PASS/0.65s | Context exits, observer-fault matrix, default-path controls |
| Additional lifecycle GREEN | 64PASS/0.87s | Terminal cancellation/late watcher and next-claim observations |
| Prior integrated candidate | 95PASS/1.24s; repeat95PASS/1.12s | Earlier candidate before admission-race correction; not final-byte acceptance |
| Admission behavior RED | 8FAIL/1.16s | Real preceding candidate:6 new-cycle waiters queued behind pending work;2 fault errors instead of required pending busy |
| Admission behavior GREEN | 8PASS/0.43s | publish/finish × observer fault/no fault × callback-still-running/future-held-after-return |
| **Final candidate** | **103PASS/1.22s** | **78new +25existing DB-free regression cases** |

Final actual collection103, setup103PASS/call103PASS/teardown103PASS; **zero skips/xfails, zero external-I/O attempts,100 test executors explicitly joined with no live owned threads**. Every barrier case releases and joins in finally. Existing source-named terminal-write cancellation and delayed-watcher-return regressions remain untouched and pass. New tests additionally use actual methods for both publish and finish races, fail observer before/after commit, preserve terminal progress across successful/failed late pulse, distinguish callback completion from actual future settlement, refuse next claim/ack after sticky failure and retain nondelegated cancellation behavior.

Only10 explicit DB-free function nodeids from `test_delegation.py` were selected (25 parametrized cases), **not37**. `test_worker.py` has no DB-free cases under its scene fixture: all20 native worker cases are **UNRUN for S1**, not collected/skipped as proof. N0 executed prior code only. No historical fixture/skip contract or old acceptance manifest was modified.

Final logs, actual103 nodeids and309 phase records:

- `.pi/task6-supervisor-lifecycle/final2-green.log`;
- `final2-collected-nodeids.txt`, `final2-phases.json`, `final2.sha256`;
- `regression-nodeids.txt` and `COMMANDS.md` (exact scrubbed invocation/replay);
- `guard.py` (audit I/O denial + pytest DB denial + explicit executor join/phase records).

All test invocations used env -i, PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, PYTHONDONTWRITEBYTECODE=1, project Python -B and pytest `-p no:cacheprovider -p guard`. Earlier guard revisions lacked final phase recording/counts; their original logs are retained, and final guard hash is not retrospectively attributed to them. Original RED byte snapshots and hashes are retained as `.source` data, never executed/imported. Earlier failures/evidence were not replaced.

## Final source/evidence hashes

| File | SHA256 |
| --- | --- |
| delegation.py | `ff4c38c9534b04213bf9acb3690d92c956b7713eca3f048462c63078c940a4a4` |
| text.py | `4ac013d00bd4ab84e947b1421de63fd06dd31853f8fb766e8e5849b65b4c101d` |
| test_text_supervisor_lifecycle.py | `b95142628b2b202847247821ca1abb798c4f751bd6b0bb7af13d49c14f78dc75` |
| guard.py | `4d1e74bbed072bf7a8209e8d5e4ed718e7d8f1f6c7a0161d0da5f0bddc8c3033` |
| final2-phases.json | `e45cc1df84fccfc5a64fdcca96a2901731e18c1cc1f481fa60baecf09ae70b77` |

Original unchanged test hashes: test_delegation.py `021bed4aaa9a1599c3eb21faf50716678cf6904769de16cf590c967b55a69476`; test_worker.py `2329c10080b488c125ad48a91a0964ca591c38d873474ddd36d6b41d33680c07`.

## Static/tool findings — not blanket clean

Explicit Ruff `format --check` **with --no-cache** passed; this is formatting evidence, not a successful lint exit. Ruff check `--no-cache --select E,F,I,B,BLE` **exit1**: exactly `text.py:140 BLE001 Do not catch blind exception: Exception`. Parent read/adjudicated ONLY this external-observer privacy boundary: arbitrary callback failures and injected CancelledError must sanitize; SystemExit/KeyboardInterrupt stay uncaught. No noqa/suppression was added. Diffcheck passed; nothing staged.

Child has no active-LSP/diagnostics tool in its allowlist. Parent final-byte active diagnostics on all3 changed Python paths (6000ms) reported **zero visible diagnostics,3checks confirmed, BUT2 findings filtered by inherited disposition/policy/inline-ignore with unknown identities/provenance**. This is NOT clean for those locations. Earlier parent checks had1confirmed/2unconfirmed; superseded, not silently relabelled. Writer never called a suppression/mark tool, added policy/ignores or deleted diagnostic/cache state. Parent found no repo `.pi-lens.json` or new ignores; four existing delegation noqa comments remain unchanged.

Automatic write-check notices additionally withheld8 findings (delegation5/text3), identities inaccessible to the child. Preserved notice:

> [pi-lens automated check — not a user request] Address 🔴 blockers before continuing; ℹ️ advisories are informational only.
>
> 📋 Some findings were withheld from the assistant context. Use lens_diagnostics with scope="all" or filePath="backend/app/workers/delegation.py" (also: backend/app/workers/text.py) to inspect the full set. withheld=8, blocker=8. Relevant prior checks: backend/app/workers/delegation.py has 5 additional blocker finding(s); backend/app/workers/text.py has 3 additional blocker finding(s).

The8 were not inferred from counts. Parent subsequently recovered the exact read-only child-widget snapshot: tree-sitter `python-sql-injection` at delegation180/188/189/190/192 and text189/193, plus RuffBLE001text140. All14execute/add/flush sink ASTs match accepted5f337cc; parent and independent re-review inspected fixed statements, bound parameters and all four private fixed-SQL repository callers. No SQL/privilege change or new suppression occurred. Five inherited advisories also received narrow source review: prior-owner/quiescence-only runner pass handlers107/137, and reachable specific-before-broad exception clauses134/198/306(cached consumer position now310).

The2 filtered findings are separately identified, not confused with those8: exact strict anchors **dd:8200e77fff44** and **dd:74d654a609b8**, ast-grep/no-ellipsis-body at text53OriginalStore.read and text66OperationRunner.__call__. Parent recomputed identities from actual source spans/tool/rule/message using the installed FNV/SHA identity algorithm and matched dispositions created on2026-11-14, before S1. They are intentional typing.Protocol signatures. Existing disposition-store hash remained unchanged; no new marks, policy or inline ignores. This resolves the earlier unknown-identity limitation without making a blanket clean claim.

Full extracted evidence: `.pi/task6-supervisor-lifecycle-parent/diagnostics-reconciled.json`, SHA256`e127f0ecfd4d683217bf1ad86286249336312eeec929f880a05ac581dd0d605b`. Separate read-only re-review e8632244 accepted these individual rulings. The original notices and caches remain retained.

Unrequested tool side effects were disclosed immediately: initial combined documentation read spilled to retained `/tmp/pi-bash-7c91da36d2d792b3.log` (docs only). Pi-lens auto-sorted imports in two new private `.before.py` reference copies; they are NONAUTHORITATIVE references, not original RED bytes. Parent checked identical non-import AST and imported names. Automatic tool Ruff cache flag is unknown; every explicit worker Ruff command used --no-cache. No cache cleanup or guard bypass occurred.

Private reference-copy static warnings retained: `delegation.before.py` lines120/128/129/130/132 and `text.before.py` lines104/108, rule/message “Potential SQL injection sink — use parameterized queries (identifier)” at db. Parent specifically read/adjudicated those unchanged fixed SET/SHOW/principal checks and private fixed-query/bind-parameter call sites. No SQL change/DB execution followed. Protocol ellipsis declarations are intentional contracts, not implementation placeholders. Private artifact directory/files are normalized to0700/0600 at handoff; no old artifact permissions changed.

## Remaining gates / next step

Main accepted S1 after source review, guarded replay and independent implementation/diagnostic re-review. Parent evidence: `.pi/task6-supervisor-lifecycle-parent/phases-second-invocation.json` and `/tmp/task6-supervisor-lifecycle-parent-tests-2.txt`; final hashes stayed unchanged. Parent's first preparation invocation failed before pytest because it incorrectly expected25function nodeids instead of10selectors expanding to25cases; zero test calls, retained `/tmp/task6-supervisor-lifecycle-parent-tests.txt`. Correct replay produced103passes and one guard-preimport assertion-rewrite warning; ordinary assertions and explicit I/O denial remained active. Parent Ruff exit1 is exactly the approved visible BLE001; format3/diffcheck passed.

Next: Main explicitly assigns reviewed **local S2** APIs/tests for consumer renewal, supervised polling and truthful readiness. S1 acceptance itself does not implement or implicitly authorize those interfaces or any native run.

Consumer renewal/poller/supervisor, actual heartbeat/readiness, trusted startup, E-owned provisioning, real Storage/dependency/outage/restart, process exit and wire-COMMIT proofs remain open. All native runs require a separately approved new immutable packet; N0's273-file packet and all old runtime/source/evidence/locks remain untouched. Default readiness/capabilities/deployment are unchanged. See [reviewed supervision plan](../architecture/task6-supervision-plan.md) for later gates.
