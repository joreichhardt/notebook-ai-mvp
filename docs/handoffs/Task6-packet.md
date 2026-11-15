# Task6 — isolated text-delegation acceptance packet

## Status / authority

**Native gate FAILED/HOLD; local quota-ACL correction reviewed.** Native `13fb823` passed all six phases1/23/1/20/21/37, then default regression recorded1498PASS/1QuotaACLFAIL/40research skips. Actual cause remains UNKNOWN. Operator reported cleanup0/0/0 and credentials removed; no independent success-after certification. [Quota-ACL correction and evidence](Task6-quota-acl-correction.md). Earlier [fixture correction](Task6-worker-fixture-correction.md) is retained separately. A new immutable snapshot requires parent review; any further remote attempt requires explicit user approval. `ac817f8` remains refused because it lacks delegation changes. No deployment or capability activation is authorized.

New packet: [`tests/security/text_delegation/`](../../tests/security/text_delegation/). Application scope remains [Task6-delegation](Task6-delegation.md). Main's mid-task extension authorized exactly one existing-file fix: `backend/tests/auth/test_documents.py:190`, the **current-head** assertion `0006` → `0007`. Deliberately version-pinned historical fixtures were preserved. All other existing application/migration/test/documentation changes and `.pi/` were left untouched; nothing was staged or committed. Main owns shared index/plan/log updates. This review-correction pass changes **only** this handoff and `tests/security/text_delegation/`; the earlier current-head fix and all other dirty files remain untouched.

## Current local correction candidate — not yet native-accepted

The original committed e54c767 attempt passed native migration1/delegation23, then failed concurrency1; following regressions were UNRUN. Its source/runtime/evidence remain immutable. A demonstrated test false-negative is corrected locally with direct fixed-SQL contenders and exact quota-busy lifecycle handling, **not concurrent repository coverage**; production app/SQL and23delegation cases stay unchanged. [Correction handoff](Task6-concurrency-correction.md), [source diagnosis](../review/task6-concurrency.md). Main independently passed220local tests/17.14s, Ruff/format9 and diffcheck; independent implementation review e9e68065 found no issues (packet-only approval, no native acceptance). Active LSP7:5confirmed,2unconfirmed (timeout/silent server), no visible findings—not a blanket clean verdict.

Latest quota-ACL-correction collection is **1815 total =1775 selected +40 exact Node exclusions**, across73files. Actual guarded collection adds only236local ACL regressions/1file; all prior1539cases/72files retain membership/order. Six phase lists and Node exclusions are byte-unchanged. Default **1735 passes +40 reviewed skips** is a forecast, not a run result. Parent focused464tests passed/30.35s; independent grant-option re-review found no issues. No production SQL/grants/application changes. Earlier1579/1539 figures describe13fb823,1571/1531describe32c4c68; still earlier figures below describe e54c767 preparation. Historical failed attempts are never rerun or overwritten.

## Packet and evidence contract

- `prepare.py`: reads only a full committed Git revision through `git archive`, never uploads/copies the worktree. Pins `tar.umask=0022` (Git's default archive here was0664, not0644). Produces exclusive private `source.tar`, `source-manifest.json` and a separate `receive.py` copied from the committed archive; prints named archive, manifest and receiver SHA256 anchors. Manifest binds full SHA, every archive file's SHA256 and mode. Rejects symlinks/special files, duplicate/unsafe members, `.pi/`, `.git/`, missing delegation/packet files and the old base. Parent independently retains/reviews **all three anchors**, not a checksum file accompanying the transfer.
- `receive.py`: standalone stdlib trust boundary, authenticated by trusted host `sha256sum` against the parent-retained receiver anchor **before its first execution**. Never use the unverified archive copy to authenticate itself. Authenticates received archive and manifest bytes before tar parsing or extraction, validates all member paths/types/modes/hashes, writes only into an exclusive private snapshot, then independently verifies extracted files without importing them. Bounds: uncompressed archive256MiB, manifest1MiB, <10000 files/<20000 members. Failure leaves a HOLD, never permission to execute or reuse a partial snapshot.
- `operator_packet.py`: only kube1, real/effective UID/GID1001, explicit `unix:///var/run/docker.sock`; scrubbed environment, private empty Docker client HOME/config, no inherited Docker contexts, registry auth, proxy, DB or BWS variables. Rootful socket use is **not** rootless proof. Full source/mode/hash/owner/regular-file manifest verification before mutation and after successful tests; unexpected files fail closed.
- Namespace `notebook-test-text-delegation`; source `/home/jre/notebook-tests/text-delegation/<fullSHA>`0700; exclusive sibling `.runtime-<fullSHA>`0700, evidence0600. Busy namespace or existing runtime/evidence is a refusal, not permission to reuse/down another run. Capacity gate: ≥4GiB available RAM/disk, load1≤6. Historical capacity is not live evidence.
- Permanent `.operator.lock`: owner1001, regular, nlink1,0600, `O_NOFOLLOW`, nonblocking exclusive `flock`, inode rechecked after acquisition. **Never unlink/replace it.** Lock stays held across preflight, pull, creation, tests, credential deletion and cleanup. Separate full SHAs cannot race in this namespace.
- Compose shell expansion uses `$$((i+1))`/`$$i`; only intended `${TD_*}` values are Compose substitutions. Lightweight tests model the packet's narrow interpolation contract and assert the complete resulting entrypoint, labels and snapshot mount. **Not native Compose rendering**; actual rendering/bootstrap remains a kube1 gate.
- Existing PostgreSQL16 and Python3.12 image digests are reused, no build. DB70:70 / runner1001:1001; read-only roots, dropALL, NNP, default seccomp (kernel mode2 checked), no ports/socket/production mounts. Actual UID/GID/CapEff/NNP/seccomp, memory/swap/CPU/PID settings, tmpfs options and mounts are checked. DB:1CPU/512MiB/no swap/128PIDs; runner:1CPU/1536MiB/no swap/128PIDs. Explicit bounded tmpfs: DB512MiB data +16MiB socket +16MiB temp; runner1GiB executable work +64MiB temp. All tmpfs nosuid/nodev; only runner work executable. No named/anonymous data volume is intended or accepted.
- DB only joins internal `database`; runner initially joins only `dependencies` for bounded `uv==0.9.9` bootstrap / `uv sync --frozen` using `uv.lock`. Full committed source is copied into bounded runner scratch, including Dockerfile recipes inspected by existing **unit** tests. Before any runner credential delivery, operator disconnects dependency egress and connects only the internal DB network, verifying both containers' actual network membership. No real Storage service or live provider is started.
- Dedicated fresh cluster, host **and local** SCRAM init, no trust fallback. Random synthetic admin/app material is generated only during a future run, host0600 and container-private0600 files/passfiles. stdin delivery avoids earlier packets'0444 cross-UID bootstrap mounts; no password in Docker argv/config environment, evidence or SQL output. Official PG password-file initialization is used. Role password DDL uses `psycopg.sql.Literal`, never concatenation. Passfile wildcard is **database only**, exact `db:5432` and exact roles: migration fixtures create separate databases and reconnect as `notebook_app`. Scheduler and IO fixture credentials retain their own0600 files. Never run against a shared cluster.
- Every subprocess has a timeout; operator output is memory-bounded and raw stderr discarded. Only closed-schema reports, fixed operation failures, validated resource IDs and exit/status fields reach evidence. Runner child exit, exact collected descriptors, every setup/call/teardown, no xfail and expected outcomes are independently checked; exit0 alone cannot pass. No raw exceptions, parameter values, SQL, document text or credentials are recorded.
- Cleanup inspects project **and full-SHA** labels, deletes exact owned container/network IDs, then independently requires0/0/0. No namespace `down`, prune, global stop or image deletion. Unknown IDs/labels or volumes are a HOLD, not a broad cleanup fallback. Catchable failures/timeouts attempt cleanup while retaining the lock; runtime evidence/source remain. Evidence-write failure is sticky and cannot prevent ownership-checked exact-ID removal; each host credential/config deletion receives an independent attempt, including when resource cleanup fails. Any evidence or deletion failure forces operator failure even if resources reach0/0/0. Evidence is **not guaranteed on a failed disk** and cannot certify successful acceptance; deletion failures require manual same-lock recovery. No broad fallback cleanup. SIGKILL/host failure cannot certify cleanup: Main must inspect retained evidence/owned resources and arrange same-lock, exact-ID recovery, **not rerun tests or delete the lock/evidence**. This packet supplies no blind resume.

## Reviewed tests / what a future PASS would mean

Lists are source-named in `cases.json`, `regression_cases.json`, `regression_files.json` and `node_exclusions.json`. Counts below are derived from actual **collection only**, not invented acceptance counts. Pinned pytest9.2 parameter indices are used instead of exporting arbitrary raw parametrized IDs. Any later source/test/lockfile revision requires rechecking these lists and re-review before committing/archiving; runtime never repairs them.

Serial phases after fresh Alembic0007 and direct-principal/SCRAM checks:

| Phase | Required all-pass / no skip, xfail, missing setup/call/teardown |
| --- | ---: |
| Migration0006→0007 / no historical backfill |1|
| New delegation database cases |23|
| Packet-owned competing scheduler case |1|
| Existing TextWorker regressions |20|
| Existing Identity dispatch regressions |21|
| Delegation unit / text parser regressions |37|

The worker count includes both parametrized `switched_engine` fixture principals. Scheduler concurrency uses two independent engines/connections and actual open transactions, distinct backend PIDs and a bounded barrier before competing claims; it requires exactly one live grant and persisted matching token. It is authored, **not executed**. Default execution is strictly serial: migration fixtures recreate/reuse cluster-wide roles in separate databases; scheduler fixtures must finish NOLOGIN teardown before the next migration fixture. No xdist.

SQL/RLS/ACL/atomicity/revocation/lease-expiry/terminal-recovery evidence comes from the real0007 database tests plus existing worker/dispatch cases, with **synthetic Store only**. No real Storage policy, external Auth/provider, actual scheduler-process restart, total shutdown/quiescence, wire-COMMIT ambiguity, polling/delivery-heartbeat supervisor or startup assembly is accepted by this packet. Capability stays disabled.

### Default Python/DB regression subset — not an all-no-skip/full-suite claim

Main approved excluding **only40 exact Node-dependent wrapper cases**, all enumerated raw nodeids in `node_exclusions.json` and future evidence. The pinned Python image has no Node. All four functions in `backend/tests/storage_real/test_stage_b_wrapper.py` require Node (including one independent-cache opt-in). No broad `storage_real` exclusion. These40 cases remain pending a separate same-snapshot **Node22 wrapper-only unit** lane; do not run actual accepted StageB/Combined/D4 operations.

Current reviewed default collection: **1454**, **40 Node deselected**, **1414 selected**. Required selected outcomes: **1374 passed +40 explicitly expected skips**:

-7 B4 real-DB opt-in cases (different packet's explicit gate not enabled);
-30 F4 real-DB opt-in cases;
-1 real Storage PhaseA opt-in;
-1 real Combined/B5 live-flow opt-in;
-1 source-research case requiring separately supplied immutable upstream caches.

These deliberate external/research skips are distinct from Node deselections. Their exact setup/call/teardown shapes are checked; every other selected case must pass all three phases. No delegation/migration/concurrency DB skip is legitimate. Evidence retains exact selected descriptors, exclusions, phases, outcomes, xfail flags and exits; reviewer can derive counts. Existing accepted packet directories contribute **fake/unit protocol tests only**; their live opt-ins remain absent. **Never rerun accepted Combined/D4/7a986f1 image acceptances or overwrite their evidence.**

## Future preflight / run procedure — NOT executed here

1. Main reviews application/migrations plus this immutable packet, the one-line current-head fix, test lists, resource/credential/cleanup boundaries and live serial capacity. Commit all approved files first. Resolve a full new40-character SHA; never use a dirty-tree upload or `ac817f8`. Packet review is not a production activation decision.
2. Local archive-only preparation from the reviewed committed packet:
   ```sh
   umask 077
   SHA=<reviewed-full-new-commit>
   .venv/bin/python tests/security/text_delegation/prepare.py "$SHA" /private/new-text-delegation-packet
   # Independently retain/review archive_sha256, manifest_sha256 and receiver_sha256.
   ```
   Output must be a new directory. This helper does not transfer files or access kube1. Main may use existing authorized transfer tooling **after approval** to copy only these three artifacts; no worktree, `.pi/`, `.env`, production auth/config or credentials. The separately transferred receiver is the reviewed committed file, not code extracted from received tar bytes. Its expected digest must come from Main's independently retained local preparation/review record, **never** from the incoming archive/manifest/checksum file.
3. Mandatory trusted receiver bootstrap on kube1, real/effective UID/GID1001. Confirm `/home/jre/notebook-tests/text-delegation` is nonsymlink, canonical, owned0700 (create only if absent; do not chmod/adopt an unsafe existing directory). Keep the incoming directory canonical, private0700 and owner1001, with received files regular/nonsymlink, nlink1,0600. Do **not** extract with `tar`, create `<SHA>`, import `protocol.py` or invoke the received operator yet. Set `SHA`, `ARCHIVE_SHA256`, `MANIFEST_SHA256`, `RECEIVER_SHA256` from Main's independently retained approval/anchors and `INCOMING` to the private transfer directory. Using trusted host shell/coreutils/Python only:
   ```sh
   set -eu
   test -f "$INCOMING/receive.py"
   test ! -L "$INCOMING/receive.py"
   test "$(stat -c '%u:%a:%h' "$INCOMING/receive.py")" = '1001:600:1'
   printf '%s  %s\n' "$RECEIVER_SHA256" "$INCOMING/receive.py" | sha256sum --check --status
   env -i PATH=/usr/local/bin:/usr/bin:/bin \
     /usr/bin/python3 -I -S "$INCOMING/receive.py" \
     "$SHA" "$ARCHIVE_SHA256" "$MANIFEST_SHA256" \
     "$INCOMING/source.tar" "$INCOMING/source-manifest.json" \
     "/home/jre/notebook-tests/text-delegation/$SHA"
   ```
   Stop on **any** failure. `-I -S` prevents incoming/current-directory or site module shadowing of the trusted stdlib receiver. Only receiver exit0 plus `TEXT_DELEGATION_RECEIVER_VERIFIED` permits proceeding. It checks both retained artifact anchors before tar parsing, validates every member before creating the snapshot, then verifies actual extracted files/modes. Snapshot root stays0700; source regular files0644/0755; manifest0600. No code from the snapshot is executed during receipt. A partial snapshot is HOLD, not a retry target. This bootstrap ordering is mandatory even when transfer tooling reports success.
4. From that exact snapshot, with Main's slot/approval, use a scrubbed Python launch (operator scrubs all its own children too):
   ```sh
   env -i PATH=/usr/local/bin:/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
     /usr/bin/python3 tests/security/text_delegation/operator_packet.py preflight "$SHA" "$MANIFEST_SHA256"
   # Only after reviewing successful preflight and immediately rechecking Main's slot:
   env -i PATH=/usr/local/bin:/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
     /usr/bin/python3 tests/security/text_delegation/operator_packet.py run "$SHA" "$MANIFEST_SHA256"
   ```
   Preflight is read-only to Docker; it briefly creates a private Docker-config directory under the namespace lock. Before first native run Main must also check actual Compose rendering on kube1 in the same scrubbed environment/private config: the **loaded/runtime model** must preserve literal `$((i+1))`/`$i`, while labels/mount resolve to the approved SHA/snapshot. Compose2.40.3 re-escapes every dollar in normal serialized `config` output, including JSON, so that output correctly contains `$$((i+1))`/`$$i`; compare the complete escaped serialization, not an incorrectly single-dollar output expectation. See the [independently reviewed adjudication](../review/task6-compose-rendering.md). Serialized output is not runtime shell execution evidence. The local interpolation-contract test is no substitute; do not dump raw Compose config into evidence. `run` itself checks `compose config --quiet` before pulling. Run repeats manifest/capacity/busy checks. No Docker context changes or raw Compose config/log dumps. The fixed daemon socket must exist/accessibly match the approved host; no alternate socket fallback.
5. Independent acceptance requires operator0 **and** all phase reports/actual exits, exact collection and expected skips/deselections, hardening/internal-network checks, unchanged source, cleanup0/0/0 and credential removal. Retain manifest/evidence hashes and UTC times. Any error, disconnect, existing runtime or nonempty namespace is HOLD: inspect the original run, never blindly launch another. Never change isolation to obtain green results.

## Actual lightweight local validation

No fixture or DB test body ran locally. Current correction-pass evidence:

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q tests/security/text_delegation/test_text_delegation_packet.py`: **78 passed** (latest0.21s). Previous48-pass claim was historical and did not catch the three review defects. New coverage: narrow Compose interpolation/negative dollars; archive/operator/protocol/manifest tampering rejected before parsing/extraction; safe-member/type/path/mode/hash checks; receiver never executes embedded code and rechecks extracted files; failed emits at each owned removal/final cleanup; independent host deletion despite cleanup/unlink/rmtree/emit failure; full mocked operator lifecycle fails on evidence open/source/cleanup/credential/final-report I/O errors while retaining lock and attempting cleanup. No Docker subprocess runs in these tests.
- Scrubbed `pytest --collect-only --import-mode=importlib -p no:terminal -p no:cacheprovider -p anyio.pytest_plugin` over `backend/tests tests/security`: six focused lists matched exactly (1/23/1/20/21/37), default1454/selected1414/Node40, expected skip40, required passes1374. `regression_cases.json` regenerated only for the30 added packet unit cases; all non-packet descriptors, file inventory and Node exclusions unchanged. No test fixtures executed.
- `.venv/bin/ruff check --no-cache tests/security/text_delegation` and `.venv/bin/ruff format --check tests/security/text_delegation`: **passed**,7 files formatted. These are local static checks, not native acceptance. Initial format check found one unformatted test file, corrected with scoped Ruff formatting.
- Scoped Python files compiled in memory (no runtime execution); handoff local link targets checked. `git diff --check` and `git diff --cached --name-only` checked; no staging/commits.

Main separately reported37 delegation/text tests passed1.51s and source Ruff clean, with no native DB execution. Those source tests were not rerun here.

### Scoped diagnostic dispositions

No guard disabling or inline suppression. Main read and adjudicated the exact `python-sql-injection` message **“Potential SQL injection sink — use parameterized queries (identifier)”**, recommendation **“Replace dynamic execution/string interpolation with parameterized or allowlisted operations.”**, as false positives at:

- `run_packet.py:127–130`: fixed `ALTER ROLE notebook_app PASSWORD {}` with `psycopg.sql.Literal` of private synthetic material, correct DDL quoting;
- `test_text_delegation_packet.py`: mocked Python `operator.execute(...)` entrypoints, not SQL. Parent re-read and ruled the new lifecycle invocation (reported773 before formatting; earlier refusal reported384) the same scoped false positive; fixed arguments and monkeypatched command boundary. Equivalent line shifts are covered, not dynamic SQL;
- `test_scheduler_concurrency.py:46`: literal `SELECT pg_backend_pid()` with no interpolation/input.

Transient unknown-symbol/type/AST/lambda lint findings were fixed. Do not claim all active diagnostic output clean: child lacks diagnostic-enumeration tooling; Main will actively check final files. These rulings are narrow, not approval of unrelated SQL, concurrency behavior, secrets or the whole packet.

## Parent checkpoint — reviewed snapshot / access blocker

Reviewed source snapshot: **e54c76720e5c74d028836f16eaf1a58737a21418**. Independent re-review73283534 confirmed all three P1 fixes with no remaining findings in scope; Main independently passed78packet tests/0.24s, Ruff and format8. Prepared263committed files; archive/manifest/receiver anchors independently matched. The first authorized SSH preflight exited255 at2026-11-14T17:21:05Z because Cloudflare requires interactive owner authentication. No remote transfer/execution is confirmed, and capacity, namespace and native Compose rendering remain unverified. No retry or remote cleanup was performed. Access restoration is an owner step; never transmit credentials/auth URLs through chat. Resume only through fresh preflight authorization; native0007 remains UNRUN. Exact retained anchors and sanitized local evidence path are in [CONTINUE](CONTINUE.md).

Independent local Node wrapper-only check against unchanged snapshot sources: **39passed/1skip/2.09s**, Node22.22.2, scrubbed environment, no DB/network/container. The single skip requires the separately supplied pinned-library research cache. This covers local wrapper VM/synthetic-file behavior, **not** the pinned Node container, real Storage or accepted Combined operations. The packet's40Node exclusions remain unchanged; the39local passes and1remaining research gate are reported separately.

## Open gates / next owner

Main: independently re-review the three P1 corrections (Compose contract, authenticated receiver bootstrap, evidence-independent cleanup), then review/commit immutable new SHA and lists; add shared index/plan/log links separately. Then approve one serial kube1 packet run and independently review actual evidence. Native0007 SQL/RLS/ACL/atomicity/revocation/recovery/concurrency acceptance remains blocking. Separate Node wrapper-only unit lane and later supervisor/startup/restart/wire-failure acceptance remain open. Missing kube1 access/capacity/image availability blocks native steps only; never fall back to laptop containers or production credentials.
