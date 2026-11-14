# Task6 — supervised text worker and trusted startup plan

## Status, authority and evidence boundary

**LOCAL DESIGN ONLY — no runtime implementation, installation or capability approval.** Source inspected: `integration/notebooklm` at **e54c76720e5c74d028836f16eaf1a58737a21418**, including existing uncommitted status documentation. This document is the only new file in this assignment. Existing files, prepared archives and `.pi/` evidence are not changed. Main owns any later index/contract/plan updates and acceptance; E remains the sole runtime-role/BWS provisioning writer.

The first Cloudflare SSH preflight remains a retained exit255/access-blocked attempt. The user subsequently confirmed direct LAN access `jre@kube1` with their existing key. Newly authorized preflight and read-only diagnosis established the enumerated model-render checks after independently adjudicating a false single-dollar-output expectation in the launcher. A later separately authorized native e54c767 attempt passed migration1/delegation23 but failed concurrency1; the remaining regressions were UNRUN. Native cause remains unknown. The demonstrated concurrency-test false-negative is corrected in a reviewed local packet, not production SQL. **native0007 acceptance remains FAILED/HOLD**, and corrective PostgreSQL execution is unrun. This design does not retry a command, authorize DB work, or alter the [prepared acceptance packet](../handoffs/Task6-packet.md); see the [current handoff](../handoffs/CONTINUE.md) for current evidence and authorization. Historical local unit results and earlier Combined/D4/7a986f1 image acceptances are not supervision evidence and must not be replayed or relabelled.

This proposal follows [contracts](contracts.md#task6-durable-text-job-delegation--approved-implementation-boundary), [Task6 delegation handoff](../handoffs/Task6-delegation.md), and [specification §§11–13](../product-spec.md#11-verbindliche-sicherheitsanforderungen-und-architekturreview): restricted direct principals, current actor authorization, bounded work, no credentials in logs, and separate implemented/tested/open claims. TXT/MD extraction needs no personal provider credentials. PDF/OCR, embeddings, model calls, cleanup workers, retry/delete/replacement routes, historical ticket backfill, public capabilities and deployment remain outside this slice. No new product question is needed.

All interfaces, numerical supervision policies and packet names below are **recommendations for review**, not APIs already implemented or additional implementation authority. Runtime work starts only after independent native0007 acceptance and explicit assignment of the next packet.

## 1. Actual source seams

Paths below are relative to the repository root; symbols, not moving line numbers, are the primary anchors.

| Existing source | Actual contract and constraint |
| --- | --- |
| `backend/app/workers/delegation.py::JobExecutionGrant` | Exact job/actor/workspace/notebook/version/purpose plus repr-hidden delivery token. A Python value is not authentication. No expiry timestamp is returned. |
| `DelegationRepository.claim/validate/renew/acknowledge` | Fresh committing scheduler transactions; direct `notebook_text_scheduler`, current=session, non-super/non-BYPASS, READ COMMITTED. Durations are integer1–300s; claim/renew default60s. All errors except cancellation are sanitized; no automatic repository transaction retry. |
| `SchedulerFactory(open,pool_timeout,connect_timeout)` | Validates declared finite pool≤1s/connect≤3s, not arbitrary factory behavior. Caller must enforce startup SQL≤3s/lock≤1s before the principal query; `_transaction` reinforces limits afterward. Returning inside the `with` still exits/commits before the async result is returned. |
| `BoundedThreadRunner` | One executor thread and actual `concurrent.futures.Future`; shared by repository and delegated worker. Shielding keeps the real pending operation after waiter cancellation. Subsequent submissions wait for it. `close(timeout<=3)` refuses new work and returns false on unresolved thread work; it does not kill threads. No public pending/phase/result observation API. |
| `ExactTextConsumer.run_one()` | Claims and validates internally; invokes one exact `TextWorker.run_one`; acknowledges terminal states; returns `idle/busy/not_claimed/yielded/lease_lost/succeeded/failed/cancelled` (cancelled is terminal reconciliation, not swallowed task cancellation). Active grant is private local state. No delivery heartbeat, poll loop or startup installation. `_busy` clearing does not imply the underlying runner is quiescent. |
| `backend/app/workers/text.py::TextWorker` | Direct `notebook_app`; `_claim/_pulse/_publish/_finish` open fresh actor transactions. Default ingestion lease30s, heartbeat5s, attempts≤5. Optional `operation_runner` covers DB, decode and chunk work; default Identity path is deliberately unchanged. `publishing` protects the existing late-watchdog/terminal-commit race. |
| `backend/app/db/leases.py` | Ingestion claim/heartbeat/terminal fencing, source/job lock order, DB clock expiry and atomic chunks+finish. Helpers do not commit. Quota gate remains authoritative. |
| `backend/migrations/versions/0007_text_dispatch.sql` | Tickets issued only at canonical `stored→finalized`. Scheduler has only four function entrypoints, no table/owner privileges. `claim_text_job` orders by ticket `available_at,created_at,job_id`, advances the delivered ticket to lease expiry+5s, and skips ineligible work. Delivery recovery of running jobs also requires expired ingestion lease. |
| `backend/app/documents/transactions.py::short_transaction` | At most3 whole short DB callback attempts, 250ms retry-start admission window,10/20ms backoff outside the transaction, only exact quota-gate-busy classification. Not a total callback wall-clock bound or ambiguous-COMMIT retry policy. |
| `backend/app/documents/storage.py::SupabaseStorage` | Real default backend, private bucket/exact generated key, fresh authorization before/after I/O,30s outer operation timeout, bounded responses, no redirects/env proxies. No synthetic fallback. `StorageError` currently permits terminal failure, not transparent outage replay. |
| `backend/app/health.py::Readiness` | Exactly five probes, per-probe2s cooperative timeout, literal true required; default503. `ready()` also requires fixed image SHA. No worker adapter or final worker-state recheck after the other concurrent probes finish. |
| `backend/app/readiness.py` | DB/Auth/private-bucket/assigned-secret-file read-only adapters only. DB probe is independent NullPool/direct app, catalog-presence check, not0007/ACL attestation. Synchronous probes gate outstanding operations internally but expose no close/join handle. |
| `backend/app/main.py`, `backend/app/capabilities.py` | Routes are mounted but no lifespan assembly installs readiness. Documents/chat remain false. Docker command is one Uvicorn process, without reload/workers flags; Compose is an unconfigured foundation, not production assembly. |

`dispatch_one(Identity,...)` in `backend/app/workers/dispatch.py` is **not** the unattended entrypoint. Do not invent an Identity from job UUIDs, maintain browser-token registries, or widen its actor-private selection to discover other users.

## 2. Dependency DAG and ownership

```text
Existing e54c767 packet + independent source review
                 │
Newly authorized access + Main-reviewed rendering diagnostic (outside this design)
                 │
Main fresh authorized preflight / serial kube1 slot
                 │
N0: native0007 packet accepted independently (not merely exit0)
                 │
        ┌────────┴─────────┐
        │                  │
S1: lifecycle/runner   E1: E-owned restricted principal,
observation + tests   connection/file provisioning design/implementation
        │                  │
S2: consumer renewal        │
+ supervisor/readiness     │
        └────────┬─────────┘
                 │
S3: trusted startup composition, still no capability opening
                 │
N1: new isolated supervisor DB/restart/wire-COMMIT acceptance
                 │
N2: actual assembled dependency/outage/restart + image binding acceptance
                 │
Separate security/release/Cloudflare/capability decisions (not granted here)
```

Local design is allowed before N0. S1–S3 implementation/installation is not pre-authorized by this document. E can review interfaces without provisioning; actual E operations require a separately assigned scope. The existing packet remains immutable: subsequent tests belong to a new reviewed snapshot and namespace, not modifications to e54c767 evidence.

| Owner | Exclusive responsibility |
| --- | --- |
| Main/integration | Approves API deltas, startup/lifespan wiring, readiness contract changes, exact snapshots, independent reviews, shared docs and slot allocation. Keeps capabilities off. |
| Assigned worker-code writer | Consumer lifecycle, runner ownership/status, text-worker observation seam, supervisor and source-named tests; no SQL/role/provisioning changes unless separately approved. |
| Data/migration owner | Any future change to0007 semantics, new read-only diagnostics or ticket-reconciliation policy. No such schema change is required by the recommended first supervisor. |
| E | Separate runtime scheduler/app credentials, protected service-specific files, BWS retrieval, role activation after migration, engine deployment inputs, process/container stop budgets and real isolation proof. Never gives the app migration/definer credentials. |
| Independent reviewer | Checks source and actual tests, cancellation/COMMIT and negative readiness controls; a writer's summary cannot satisfy the gate. |

## 3. Minimal runtime topology and proposed interfaces

**Recommendation:** one supervisor per application lifespan, one consumer and one shared runner, in the existing single-process Uvicorn deployment. This is the smallest composition with a genuinely in-memory, read-only worker readiness probe and no new IPC/auth protocol. Enforce the single-process/no-reload launch contract in the deployment packet. Do not claim cluster-wide singleton behavior: another replica would have its own worker, and SQL fences—not a Python lock—must protect delivery. A separately deployed worker would need a new authenticated, freshness-bound cross-process status channel and its own acceptance; defer that topology decision rather than adding a status file.

The app and scheduler **database principals remain separate even in one process**. This is not process-level isolation of their secrets; document that trust boundary. TXT/MD parsing is bounded trusted Python; it is not the future isolated untrusted PDF/OCR parser.

Proposed minimal APIs (names are proposed, not imports available today):

- `BoundedThreadRunner.snapshot() -> RunnerSnapshot`: immutable, loop-local observation of `closed`, actual pending state, operation sequence, submitted/started/completed monotonic times and sanitized phase. Never expose arguments, result, exceptions, actor/job IDs or tokens. Separate submission time from real thread entry and callback return; queued work is not progress. Add observation to the existing runner, not a second executor or unbounded submission queue. A late completion updates only its own sequence; it cannot revive a stopped generation.
- `ExactTextConsumer` gains an explicit validated policy and lifecycle observer; `run_one()` retains its no-argument, one-delivery contract and existing return vocabulary. **Consumer owns its grant and delivery renewal coroutine.** It never publishes the grant to supervisor/readiness or requires private-variable introspection. No general `run_grant` entrypoint is needed.
- `TextWorker` gains an optional non-awaiting, trusted progress observer for delegated operation only: fixed phase transitions, successful ingestion-claim/pulse commit and publication/finish settlement. Preserve default dispatch/Identity behavior. Observation is not authorization; do not infer commit from `flush`, SQL RETURNING, thread submission or pre-context-exit callbacks. Observation failures must fail closed without altering already committed DB state or escaping secrets.
- `TextWorkerSupervisor.run()` owns exactly one consumer task, schedules bounded sleep and observes lifecycle/task completion; `request_stop()` synchronously latches draining before any await; `close()` returns a structured `ShutdownReport`, not a success boolean equated with `Task.cancel()`.
- `worker_probe(supervisor) -> app.health.Probe`: read-only predicate over current task/runner/lifecycle state using monotonic time. No claim/renew/ack, user impersonation, network request, startup mutation or watchdog timestamp refresh in response to HTTP.
- Trusted `assemble_text_runtime(config)`/lifespan context (name to be reviewed) owns engines, runner, consumer, supervisor and all probe resources. Production constructs concrete implementations; fake factories remain explicit Python unit-test injection only, never environment-importable classes/ready flags.

Minimum `ShutdownReport` facts: supervisor/control tasks joined, worker/renewal/watcher tasks joined, runner actual pending settled, independent probe operations settled, and `outcome_unknown`. `quiescent` can be true only when all owned local work has actually stopped. `outcome_unknown` may remain true even after quiescence because a completed driver exception cannot prove COMMIT outcome. No report asserts remote Storage quiescence or DB rollback from coroutine state.

### Process/supervisor states

Process state and per-delivery phase are distinct. A timer cannot move either into a healthier state.

| State | Entry / permitted work | Exit |
| --- | --- | --- |
| `starting` | Configuration/schema/principal/resource validation; no claims until assembly prerequisites hold. Readiness false. | Validated runtime starts first actual claim cycle; invalid inputs → `faulted`. |
| `serving` | Exactly one idle-poll cycle or delivery at a time; bounded control-loop observation. | Shutdown → `draining`; task exception/deadline/invariant failure → `degraded` or `faulted`. |
| `degraded` | No new claim until prior work is settled; finite error backoff and a fresh ordinary cycle may establish recovery. Readiness false; do not keep renewing stuck work indefinitely. | Fresh successful cycle plus all health predicates → `serving`; unresolved thread/dead control task → `faulted`; stop → `draining`. |
| `draining` | Sticky; immediately readiness false and no claims. Bounded completion of current delivery, then cancel/join and close. | All owned local work settled → `stopped`; unresolved work → `faulted` with unresolved shutdown. Never return to serving in this generation. |
| `faulted` | Readiness false, claims disabled. No in-process replacement runner to evade a pending future. | Bounded teardown/operator process replacement only. |
| `stopped` | No owned work; readiness false even if last cycle succeeded. | New lifespan/generation with full validation only. |

Per-delivery phases: `claiming → validating → executing → acknowledging → settled`. `claiming→idle` is a successful no-work DB round trip, not execution success. `validating→reconciling→acknowledging` handles durable terminal state without `TextWorker` or Storage. `executing` carries actual `ingestion_claim/read/decode/chunks/publish/finish` observation, not guessed phase inferred from elapsed time. Any phase can become `uncertain` on cancellation/transport failure; no speculative finish/ack follows. Revoked/not-executable state settles locally without making it terminal in the DB. Every phase has a fixed deadline set on real entry, never slid by an unrelated ticker or renewal.

## 4. Polling, fairness and bounded backoff

Proposed first policy, to validate under isolated load before installation:

- Exactly one `run_one()` in flight. No `create_task` on each timer tick; no queued batch, actor cache or parallel consumer pool. `busy` when the supervisor believes it is idle is an invariant failure, not a healthy empty queue.
- Empty polls: stop-aware1s,2s,4s, then5s maximum; reset on delivery. Healthy idle requires a recent **committed** claim returning none, not only a live loop. Success/terminal ack permits next poll after at least100ms, with an async yield, preventing a hot terminal-drain loop.
- `not_claimed/yielded/lease_lost`: at least1s before the next independent claim, then bounded backoff on repeated nonprogress. Never call the worker again with the same grant. SQL advances the issued ticket to its current delivery expiry+5s, so other eligible tickets can run while it waits.
- Sanitized repository/dependency errors:1s,2s,4s,8s, then10s cap; bounded jitter e.g.±10%, clipped to the cap and minimum delay, with injectable clock/randomness in tests. Mark degraded immediately. No recursive retry, fixed-lifetime delivery cap, or fake dead-letter transition. A fresh poll is a new durable scheduling decision, not replay of the failed operation.
- Reset error backoff only after a committed successful scheduler cycle and no unresolved work, not just timer wakeup or network-connect success. Stop always interrupts sleep; monotonic oversleep is detected before starting work.
- Fairness uses existing SQL ordering and eligibility, not arbitrary app-side actor IDs. Excluded cancelled/revoked/stale work must not obstruct another ticket. A quota-blocked actor's claimed ticket moves behind work still available; running work is ineligible until its ingestion lease expires. No strict per-tenant weighted fairness or finite starvation bound under unlimited arrivals is promised by this ordering.
- Distinguish process health from queue completeness: a healthy worker may see no eligible work while exhausted, revoked or historic no-ticket jobs exist. Keep fixed local counters for outcomes/errors/unresolved shutdown; private queue-age/ineligible diagnostics need a separately reviewed narrow DB interface, not scheduler raw-table SELECT. No rows/tokens/document text in public readiness.

Do not weaken SQL backoff by resetting `available_at`, attempts, delivery tokens, sticky cancellation, or ingestion leases. No DB transaction spans sleep or Storage awaits. Existing `short_transaction` remains solely the recognized whole-short-DB quota retry mechanism; never wrap `run_one`, Storage read, publication with unknown COMMIT outcome, or HTTP uploads in it.

## 5. Three distinct heartbeats and publication reconciliation

| Signal / owner | What it establishes | What it does not establish |
| --- | --- | --- |
| Delivery lease / consumer via `repository.renew(grant,seconds=60)` | The exact live delivery fence is still accepted; successful result is observed only after commit. Proposed cadence15s; one renewal outstanding at most. | Ingestion authority, successful processing, actual worker progress, or absence of a late old process. |
| Ingestion lease / `TextWorker.watch` and authorization `_pulse` | Current app actor/source/job permission plus exact ingestion token; default5s cadence/30s lease. | Delivery ownership or physical thread/process termination after expiry. |
| Supervision progress / supervisor and actual operations | Current generation/task alive, deadlines respected, recent concrete DB/phase settlement. | Authorization, persisted lease extension or successful output. No synthetic heartbeat table/file/timer success. |

Both lease types remain DB-clock authoritative. Because the grant has no expiry field, record a conservative local delivery validity deadline using **operation start monotonic time + requested duration**, only when claim/renew returned successfully after commit. Never use response-receipt+duration (overstates validity during delayed replies). Late results received after that conservative deadline cannot establish health. This local deadline is a readiness/scheduling bound only; SQL still decides whether a token is valid.

Consumer starts renewal after committed claim, retains it through execution and terminal reconciliation, and stops/joins it before returning. Renewal and every worker operation use the **same existing runner**; no second scheduler thread to claim liveness while a publisher is wedged. The renewal loop sleeps after completion rather than accumulating ticks. Default ingestion heartbeat continues separately but also serializes on this runner. A slow operation can therefore delay both leases: that is a detected unhealthy condition, not a reason to widen concurrency.

Coordination rules:

1. Fresh exact validation precedes execution. Do not repeatedly validate an already-running ingestion job as an execution heartbeat: current SQL `validate_text_job` returns null for live-running work. Renewal accepts eligible live-running work and is the correct delivery operation.
2. During normal read/parse, delivery renewal false/exception/overdue makes readiness false and stops new work. Cancel the active await cooperatively and join subordinate tasks; delegated cancellation must not call `_finish('cancelled')` or ack. A running real DB operation remains owned until actual completion.
3. **Terminal publication/finish already submitted is special.** If renewal/watchdog fails while its actual thread is running, wait only within the fixed settlement budget for that operation's outcome; do not race it with a new finish/ack. The runner's true operation phase, not merely a consumer `executing` label, distinguishes this case. If it returns committed success, retain terminal truth. Failed delivery ownership may prevent ack but must not relabel the job failed or re-read Storage.
4. Once execution reports terminal success/failure, stop/join renewal before ack; use one ack after prior owned operations settle. If a renewal was already submitted, its actual future must settle first. Ack requires a still-live exact delivery token. A renewal racing terminal commit is not itself an error:0007 renewal eligibility includes terminal states; a renewal queued after ack can correctly return false and must not override an already observed ack success.
5. Ack false/exception means **unacknowledged or uncertain delivery**, not rolled-back chunks. Existing public `lease_lost` result can be preserved, but internal supervision records `terminal_observed_ack_unconfirmed` without storing the grant in telemetry. Do not force a second immediate ack or replay worker I/O after ambiguous failure.
6. Next eligible claim after delivery expiry/backoff observes current DB state. If eligible terminal, validate+ack without worker or Storage; if genuinely queued/expired-running and attempts<5, the ordinary fenced worker can execute. Both delivery and ingestion fences must permit it. Delivery retries remain unbounded over lifetime, one bounded attempt per cycle; ingestion attempts remain≤5.
7. Claim/renew/ack COMMIT ambiguity is not the same as known SQL quota busy. With no returned grant after claim, let durable expiry/backoff resolve it. With unknown renew/ack outcome, retain uncertainty and let durable later selection decide. Never manufacture tokens or assume transaction rollback from disconnect.
8. Revocation/deletion/sticky cancellation also removes terminal tickets from `text_dispatch_eligible()`. Such tickets may remain unacknowledged indefinitely; do not promise universal eventual ack, restore revoked authority, or widen eligibility to make a dashboard green. Reconciliation here is limited to still-eligible durable terminal jobs.

## 6. Deadlines, cancellation and honest shutdown

Initial **candidate detection budgets**, not measured performance guarantees: DB operation10s from admission (including queued wait, pool/connect/transaction/commit); decode/chunks each5s; Storage read30s per existing adapter; whole delivery120s without renewal resetting that limit. Record phase admission before awaiting the runner lock: a coroutine waiting to submit must not escape deadline accounting just because no new future exists yet. Delivery renewal15s/lease60s and ingestion heartbeat5s/lease30s remain as above. A phase whose result is late fails health even if a cooperative timeout could not stop it. Multi-statement transactions and three admitted quota attempts can exceed an individual3s SQL timeout: do not present these candidate wall-clock deadlines as driver preemption or derive a successful timing proof arithmetically. Native maximum-size tests must show acceptable headroom, or Main reviews explicit policy changes before installation.

Shutdown sequence:

1. Latch `draining` and invalidate readiness **synchronously**, before awaiting anything. Disable new poll admission and retain references to every owned task/future.
2. Interrupt idle/backoff immediately. Allow an active delivery up to a proposed10s grace to finish normally (renewal may continue only within that grace); no new delivery. Persistent/unhealthy work receives no endless lease extensions.
3. At grace expiry cancel the consumer wait, which cancels/joins its renewal and TextWorker coroutine children. Preserve `CancelledError`. This stops cooperative HTTP work but is not recall of requests already sent. Do not catch cancellation and report idle/succeeded.
4. Invoke `runner.close(timeout=3)` and account for actual outstanding future completion. False is a shutdown failure with unknown operation outcome, not a successful drain. Never spawn a replacement executor in the same process, dispose an engine while still owned by an unresolved operation, or clear the only reference to the future.
5. Close/join independent readiness probe operations and clients; remove the installed readiness instance even on partial startup failure. Dispose owned engines after actual users settle. Do not close the unrelated cached HTTP `app.db.session.engine()` as if it belonged to this runtime.
6. Record only finite status enums/counts/elapsed bounds. An unresolved shutdown keeps readiness false, requires non-success operational reporting and bounded process-level termination by E's service manager. `ThreadPoolExecutor.shutdown(wait=False)` does **not** promise interpreter exit: Python can wait for executor threads. Hard process kill proves only that process stopped, not that a DB COMMIT or remote request did not happen. Recovery still follows durable fencing.

A service-manager stop budget must exceed the reviewed grace+runner/probe teardown budgets and separately bound forced termination. Exact deployment values and proof of process exit are E-owned. No local thread-kill API or orphaned background task is an acceptable substitute.

## 7. Truthful worker readiness

The fifth probe must evaluate **current facts on each request**, not cache a prior true value. Proposed predicate:

- Expected generation is installed, state is `serving`, supervisor and required control tasks are neither done nor cancelled, and no stop/fault latch is set.
- No unexpected outstanding operation, expired phase/whole-delivery deadline, abandoned cancellation, unresolved shutdown or future completion from a previous generation.
- Recent actual scheduler evidence: committed claim/validate/renew/ack, not submission. Proposed age limit20s with idle poll≤5s and renewal15s. For active execution after ingestion claim, also require nonexpired conservative delivery validity and ingestion claim/pulse commit age≤15s, plus conservative ingestion validity computed from operation start+30s; before ingestion claim the phase deadline applies. During bounded terminal settlement a stopped ingestion heartbeat is expected, so use the fixed terminal-operation deadline instead of requiring a new pulse. The publish deadline must not be renewed by scheduler success.
- Control-loop acknowledgement within2s (proposed500ms observation cadence) **and** the above operation progress/deadlines. A ticker alone cannot establish health, refresh operation age, reset deadlines or mark a DB commit. `Task.done()==False` alone cannot detect a stalled task.
- Idle success requires at least one completed scheduler cycle in this generation. Startup cannot pass merely because tasks were created. Error backoff/degraded, draining, faulted and stopped always return false.

An operation that has not finished but remains inside its declared bound is in flight, not proven wedged. Instantaneous failure detection is impossible without an observation delay. The honest guarantee is: known-dead/draining returns false immediately when observed; stale or nonprogressing work cannot remain ready beyond its fixed freshness/phase bounds. A frozen process cannot serve a new200; its caller must enforce a timeout. A process whose HTTP loop still runs while its worker thread wedges must serve503 once the bound expires. Tests must cover both, rather than asserting impossible zero-delay detection.

**Final-response race:** current `Readiness.check()` can receive worker=true, then wait for another probe while shutdown begins. Recommend a second read-only worker/generation/deadline check after all five probes succeed and immediately before constructing200, with no intervening await after that final check. This requires a reviewed health integration seam; it cannot be solved by a cached bool or the current worker adapter alone. A concurrent system can only promise a valid observation at the response decision point, not future uptime. Do not expose phases, queue IDs, DB details or failure causes publicly; preserve exact200/503 shapes, fixed image release and `no-store`.

The other four real adapters remain mandatory even with documents/chat false. Worker probe does not fetch BWS, call models, or claim work on demand. Readiness can eventually be true for a healthy closed-capability runtime; that does not authorize route capability, public access, migrations or release.

## 8. Trusted startup and least-privilege provisioning

### Inputs and validation order

1. Trusted release launcher supplies explicit non-secret endpoints/versions and assigned absolute file paths. Reject incomplete configuration, conflicting inline/file sources, unsafe URL scheme/host/user/database, unsupported drivers, test transports/factory selectors and inherited BWS bootstrap. Missing configuration preserves unassembled default503; an explicitly configured but invalid runtime fails startup rather than partially serving ready. Never log Pydantic inputs, DSNs, SQL errors or tokens.
2. Validate trusted parent directories/readonly mounts and bounded regular consumer files, correct UID and0400/0600. Existing `secret_files_probe` is an ongoing check, not a complete secure config loader; `get_settings()` alone is not that loader. Read only enumerated service files. No secret search or global project injection. Production credential/TLS/file requirements are E-owned and must be verified before connections.
3. Build dedicated concrete scheduler and execution engines with psycopg3, READ COMMITTED, finite pool size (recommend1/max_overflow0 per engine for this single runner), pool≤1s/connect≤3s, startup SQL3s/lock1s, hidden parameters/no echo, and approved TLS options. Preserve explicit TLS settings; reject unapproved role/options overrides. Never claim a constructor's numeric fields prove a malicious factory is bounded.
4. Through those actual engines verify direct exact `session_user=current_user`, nonsuper/non-BYPASS, no unsafe role memberships/role-switch path, READ COMMITTED and effective startup budgets. Scheduler may invoke only the four bounded dispatch functions; execution uses direct `notebook_app`. Read-only startup validation must not call claim as a schema health check.
5. Validate the **release's expected migration head/function/ACL contract**, including0007, separately from generic DB availability. Catalog presence alone does not attest function bodies or RLS. Main/E's accepted migration manifest and real ACL negative tests establish content; startup can reject wrong head/missing signatures and unsafe effective privileges without migrations, grants or raw private-ticket reads. Determine the least-privilege migration-head query from actual ACLs; do not add a broad grant just to simplify validation.
6. Load `StorageConfig` explicitly; construct real `SupabaseStorage` through `TextWorker`, no synthetic store, user-selected endpoint or fallback. The worker needs neither `notebook_storage_io` nor personal-provider/BWS access. Validate actual private bucket/policy and permitted credential scope in native acceptance, not by a configured string alone.
7. Construct the one shared runner, repository and delegated TextWorker; instantiate consumer/supervisor and four real existing probes plus the worker probe. Start the supervisor only after nonmutating startup checks pass. Install `Readiness` only after ownership/cleanup is established; initial probe remains false until the first real cycle. On any partial failure unwind resources, stay not-ready and never leave an unobserved poller.
8. Lifespan shutdown uses §6. Restart creates a fresh generation and validates again. No persistence of ready booleans, grants in files, or inherited old task timestamps.

### Principal and credential split

| Principal/resource | Allowed | Explicitly excluded |
| --- | --- | --- |
| `notebook_text_scheduler` | E activates a **direct** runtime login after migration; only `claim_text_job`, `validate_text_job`, `renew_text_job`, `ack_text_job` execution and required schema/connect access. Separate credential/file/engine. | App/source/provider/secret/ticket table access, owner membership, superuser/BYPASS, generic actor execution, Storage-I/O capability. |
| `notebook_app` | Separate direct restricted execution connection, actor GUC set transaction-locally from freshly validated durable grant, existing RLS/admission/quota rules. | Scheduler/definer/storage-I/O role inheritance or SET ROLE from an admin login. |
| `notebook_text_dispatch_owner` | Existing NOLOGIN, fixed definer functions and narrow policies only. | Runtime login/password/membership, broad schema CREATE, runtime migration privileges. |
| Storage consumer credential | Only required service-specific Storage access, in assigned readonly private file; fresh actor/scope checks still mandatory. | Browser exposure, treating service token/RLS bypass as actor authorization, provider credentials. |
| E provisioning process | Pinned BWS CLI, explicit endpoint/project, minimum read account, protected bootstrap supplied only to fetch process; immutable complete validated release before activation. | Passing `BWS_ACCESS_TOKEN` to app, migration in normal startup, auto-LOGIN/grant repair by app, secrets in argv/logs/images. |

Current0007 requires preexisting scheduler/owner roles to be NOLOGIN and without memberships at migration application. Therefore migrate first, E explicitly activates only the runtime scheduler afterward; existing bootstrap fixtures are not production provisioners. Re-running role-creating migration against activated roles is not an allowed repair. Rotation/revocation and future migration ordering need an E runbook, not privileged fallback.

Current `deploy/provision_secrets.py` retrieves declared values atomically but does not create database roles, attest DB grants or validate consumer-specific formats/UID mapping. Runtime files must be consumer-owned while remaining inaccessible to unrelated services. Running valid mounted releases may survive a BWS outage; restart/new release requires prior successful complete provisioning under the existing contract, with no invented or empty fallback. DB/Auth/Storage outages independently fail readiness. No BWS availability call belongs in readiness.

## 9. Separate mismatches, limitations and defect candidates

These are source observations, not newly demonstrated native failures. No code fix was made.

1. **API gap:** `ExactTextConsumer.run_one()` hides its grant; an external delivery heartbeat cannot be implemented using the public consumer API today. Consumer-owned renewal avoids authority leakage and duplicated claim logic.
2. **API gap:** runner pending/phase is private and TextWorker ingestion progress/publication settlement is not exposed. `busy`, task liveness, renew success or a timestamp-only heartbeat cannot satisfy truthful readiness or publication-aware shutdown. Add narrow observation, not private inspection.
3. **Concrete readiness race:** current concurrent readiness aggregation does not recheck worker state after other probes complete; a future worker adapter alone cannot prevent a pre-drain true result producing200 during drain. This is dormant while startup is uninstalled. Require the final decision-point test in §7.
4. **Resource ownership gap:** `database_probe` and `secret_files_probe` retain synchronous work after waiter cancellation but return only callables. Their locks bound concurrent actual work, not complete application teardown. Assembly needs close/status ownership handles or must explicitly report unresolved probe work. A zero-worker-futures shutdown is not whole-process quiescence.
5. **Schema/startup mismatch:** `database_probe` checks objects through upload/quota, not0007 functions/tickets/head; `get_settings()` lacks scheduler/storage/version configuration and uses unbounded `Path.read_text()` without the readiness file checks. Neither should be advertised as sufficient trusted startup validation.
6. **Known semantics, not a repair mandate:**0007 terminal reconciliation is still subject to eligibility (including admission, membership, current version, noncancelled intent). Some terminal tickets cannot ack after revocation/deletion. Running attempts≥5 can remain nonterminal/ineligible. Preserve these facts; any diagnostic/terminalization policy needs its own reviewed DB change.
7. **Health vs success mismatch:** generic `DelegationUnavailable` masks SQL busy and other scheduler failures uniformly. First supervisor can back off/fail health safely without adding classifications; it must not infer permanent corruption from that one enum. `TextWorker` already turns `StorageError` into a fenced failed job when possible; outages are not a newly authorized auto-retry product feature.
8. **Timing limitation:** one shared runner intentionally serializes renewal, ingestion heartbeats and publication/parser work. Adding renewal can increase heartbeat wait time. No hard fairness/latency claim follows from driver timeouts. Native contention/max-size checks are mandatory before sizing thresholds or adding concurrency.

No independently reproduced new SQL/ACL defect is asserted here; native0007 remains the prerequisite. The final-response race and ownership gaps are prospective integration blockers, not evidence that the currently default503 application is returning false readiness today.

## 10. Outstanding technical decisions and recommendations

| Decision for Main/E review | Recommendation / reason | Must be resolved by |
| --- | --- | --- |
| In-process vs separate worker/status transport | Start one in-process supervisor in existing single-process app; no IPC, no heartbeat file. Explicitly accept shared-process secret trust boundary. Separate process needs another design. | Before S1/S3 scope approval |
| Delivery heartbeat ownership | Consumer owns grant and renewal; supervisor observes sanitized phase. Never expose token or build Identity. | S1 |
| Progress/phase API and late callback safety | Frozen snapshots + monotonic sequence/generation, real operation entry/settlement; minimal optional observer in delegated TextWorker path. | S1 independent review |
| Detection budgets/renewal cadence | Candidate values §§4–7; test max-size/lock contention, fail readiness rather than silently extend on lag. Do not raise leases or introduce a second runner to hide stalls. | Before S2 installation |
| `Readiness` final decision point | Add explicit current worker/generation check after aggregate success, no await between final check and response construction. Preserve five required probes and response schema. | S2/Main health review |
| Shutdown ownership of existing sync probes | Add explicit lifecycle handles while retaining callable probe interface, or a tightly owned adapter object; report unresolved actual threads. No general framework rewrite. | S3 |
| Startup schema/ACL attestation | Accepted migration manifest + least-privilege catalog/head validation + real negative ACL tests; inspect current head-query grants before choosing SQL. No new raw ticket access. | S3/E1 |
| Scheduler LOGIN and future migrations | E owns migrate-then-activate and rotation runbook, direct exact role credential, never runtime owner membership. | E1 before real assembly |
| Persistently ineligible/exhausted ticket visibility | Fixed local counters now; defer private DB diagnostics/repair to separately scoped data packet. No automatic terminalization/backfill. | Document before activation; not a blocker to bounded polling itself |
| Delivery loss during publication | Wait for actual terminal-operation settlement within fixed budget; retain committed result, unconfirmed ack as separate delivery fact; durable recovery otherwise. | S1/S2 race tests |

These are technical review choices, not requests for new user-facing product behavior. If implementation requires widening the approved SQL authority or changing failure/retry semantics, pause for Main's decision rather than silently choosing it.

## 11. Next small packets and exact acceptance gates

**None of the tests below were executed for this design.** Proposed new filenames are source-named suggestions; do not add tests to the frozen e54c767 packet or turn an expected skip into a pass claim.

### N0 — existing native0007 prerequisite (Main, already prepared)

Accept only the exact reviewed [Task6 packet](../handoffs/Task6-packet.md): independent receiver/archive/manifest anchors, authorized fresh preflight, actual native rendering/hardening/phase collection and outcomes, no skips in required DB phases, sanitized retained evidence, immutable source and scoped cleanup/credential-removal proof. Operator0 alone is insufficient. Current local wrapper39pass/1research skip is separate and not a pinned-container/dependency pass. No run or retry is granted here.

### S1 — lifecycle/observation and cancellation seam (after N0)

Narrow changes: `delegation.py`, optional delegated observation in `text.py`, new `backend/tests/ingestion/test_text_supervisor_lifecycle.py`, assigned handoff. No startup/SQL/provisioning changes.

Required local deterministic tests (synthetic events/futures, no DB/network):

- `test_runner_snapshot_tracks_actual_future_after_waiter_cancel`: cancellation keeps one real slot; repeated cancelled callers do not create executor work; late completion cannot revive stopped generation.
- `test_phase_progress_requires_context_exit`: pre-commit/flush/submitted events cannot refresh committed progress; callback errors and secret-shaped exceptions remain sanitized.
- `test_shutdown_reports_unresolved_thread_without_replacement`: barrier-held worker survives cancelled waiter; close reports unresolved within≤3s; no finish/ack/engine disposal/new runner; finally release/join test thread.
- Existing `test_cancel_during_terminal_write_never_starts_conflicting_finish_or_ack` and `test_watcher_failure_after_commit_before_publish_return_does_not_report_lease_loss` remain green, including default Identity-path controls.

Pass means unit ownership mechanics only. No real process-stop, RLS or wire-COMMIT claim.

### S2 — renewal, poller and truthful readiness (after S1)

Narrow changes: consumer-owned renewal, new supervisor module, read-only worker probe, Main-owned final readiness decision seam, `backend/tests/ingestion/test_text_supervisor.py` and `backend/tests/test_worker_readiness.py` (proposed).

Required local acceptance:

- Exact one consumer/one actual runner operation; no tick backlog, hot loop or nested I/O retry; bounded idle/error/lease-loss backoff and immediate stop-aware sleep cancellation with fake monotonic clock/jitter.
- All `run_one` results and cancellation mapped distinctly; unexpected busy is unhealthy; delivered-but-unclaimed work does not consume an app-side reset/retry.
- Renew starts only after committed claim, one coroutine, correct exact grant, no new renewal after settling/stop; queued renewal/ingestion/publication interleavings do not deadlock or mislabel committed terminal work.
- `test_delivery_renew_failure_during_publish_preserves_terminal_truth`, before-publish cancellation control, ack false/exception, ack-before-renew-false race, delayed replies beyond conservative lease bound.
- `test_worker_readiness_rejects_stale_dead_wedged_draining`: fake clock crosses each boundary, task death while heartbeat was fresh, live timer with blocked actual thread, control loop stall, error backoff, no first cycle, previous generation, stopped/closed runner. No periodic timer can keep health true indefinitely.
- `test_ready_rechecks_worker_after_slow_sibling_probe`: worker initially true, another probe blocked, drain/fault before sibling release →503; healthy control200; missing probe/release/false/literal-nonbool/no-store and caller cancellation regressions retained.
- Cooperative timeout tests cannot be named hard thread/process-kill proofs. All held threads released in test cleanup; no external I/O attempts.

### E1 + S3 — real configuration assembly, still gated off for public features

E owns provisioning inputs/role activation independently. Main wires reviewed lifespan with concrete factories; new `backend/tests/test_text_runtime_startup.py` (proposed) uses explicitly injected local fakes and deny-all DB/network/subprocess sentinels.

Required local checks: missing/unsafe/conflicting config, wrong direct principal, privileged SET ROLE, wrong isolation/ineffective timeouts, wrong migration head, overprivileged scheduler, invalid file/UID/mode/mount contract, inherited BWS bootstrap, partial initialization rollback, double lifespan/generation, no orphan tasks, no engine disposal during outstanding work, no synthetic production selector, no capability changes. Engine construction must demonstrate real passed connection/pool options rather than trust only `SchedulerFactory` numbers. Verify startup false until actual first-cycle observation; default unstamped/unconfigured application remains503. Fake probes prove composition, not real services.

### N1 — new isolated kube1 supervised DB/restart/wire packet

Requires a separately reviewed immutable snapshot/operator/serial slot and synthetic credentials only; no local PostgreSQL/containers. Reuse source-named native0007 tests as regressions, not old evidence labels.

Required **real PostgreSQL** cases:

- Restricted scheduler and app principal/ACL/RLS/role graph plus current member/admission/source revocation before claim, read and publish; no unauthorized Storage invocation.
- Two real schedulers, separate backend PIDs and barrier-controlled transactions: one live fence, bounded quota busy behavior, other eligible actor progresses; no fabricated same-connection concurrency.
- Worker process killed after claim, during read, after ingestion lease renewal, after publication before ack and after ack. Observe actual process exit and replacement generation; recovery before ingestion expiry denied; successful terminal publication reconciles with **zero additional Storage reads and unchanged attempts/chunks**. For nonterminal expired-running recovery, attempts increment once per actual claim and never exceed5.
- Actual wire loss at COMMIT, distinguishing server commit from response receipt: test-owned bounded fault transport/proxy around real connection, corroborate via independent DB session. Cases for ticket claim, renewal, publication/finish and ack, including committed-response-lost and rollback controls. A mocked raised exception or process death alone is not wire-COMMIT evidence. Keep fault layer isolated from foreign traffic and credentials/logs.
- DB outage/lock waits during renewal/publication; observe503 inside approved bound, no duplicated actual local thread work, no in-process replacement for wedged runner, restore then fresh validation/reconciliation. An unknown outcome never triggers unconditional replay.
- Max supported UTF-8 TXT/MD, parser/chunk and DB publication load, renew/pulse contention; confirm chosen phase deadlines and lease headroom without modifying DB clocks/expiry to manufacture pass. Real waiting expiry controls alongside deterministic unit timing.

Record per-case setup/call/teardown/exit, real child PIDs/termination, exact SHA/config policy and sanitized outcomes. All required cases pass, no skip/xfail, original failures retained, owned resource0/0/0 and credential deletion independently verified. No production process/role use.

### N2 — actual assembled dependency/outage/restart acceptance

Also kube1-only and separately authorized. Test actual restricted startup, private Storage, Auth version/gateway, assigned files/UID mapping, workers and image release binding. Real requests must show: all dependencies healthy →200; each mandatory dependency missing/unavailable/stale/unsafe or worker draining/dead/stuck →503/no-store; liveness remains only liveness. Restore must not trust old success or bypass startup checks. Readiness requests cause **no job claim/renew/ack, Storage object write, provider request or BWS retrieval** themselves.

Exercise process/container normal stop and forced termination with actual owned-resource inspection, restart from retained synthetic DB state, revoked scheduler credential, missing/rotated consumer files, and controlled provisioning failure/BWS outage behavior. BWS lifecycle proof requires E's specifically authorized real dependency step; synthetic CLI failure tests cannot stand in for it. Actual Storage read cancellation does not prove remote write quiescence; upload cleanup remains outside scope. Bind new final image/commit/digests and scan evidence; do not repeat accepted old image/Combined/D4 runs to pad evidence.

Independent reviewer must approve N1/N2 outcomes, limits and remaining security/release gates. Neither N0 nor a green worker probe enables documents/chat or public access automatically.

## 12. Evidence produced by this assignment

Performed: local Git/status/source/document inspection and static consistency/link/whitespace review of this new document. A stdlib-only check resolved all6 local links/anchors and checked balanced fences/no trailing whitespace; `git diff --check` passed and `git diff --cached --name-only` was empty. No tests were added or executed. No production code/test changes, dependency installation, SSH/network/auth/credentials, containers, DB, remote retry or nested agents. Existing prepared snapshot/evidence was neither read for secret values nor modified. No runtime success is claimed.

Independent read-only review **8ecdade3-cd10-455d-8e0e-0ee752b8ff94** found no issues after checking actual runner/consumer/TextWorker/SQL eligibility, readiness aggregation and startup/probe ownership seams. Verdict: **OK, design document only**; no runtime operations or tests performed by the reviewer. Main read the complete plan and added it to the shared documentation index; candidate policies remain uninstalled and require the stated native measurements and implementation review. Preserve the owner-access/native0007 gate; only after its independent acceptance assign S1 and E1 with explicit ownership. Until then, default readiness remains503 and capabilities remain false.
