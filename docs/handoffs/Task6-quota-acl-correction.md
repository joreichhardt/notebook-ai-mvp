# Task6 — current-head quota ACL correction

## Historical native result: HOLD

The single approved native attempt for `13fb8234a1750d5a70850644fb350b46d7327dc4` ran on kube1 on 2026-11-15, 13:24:00.428250Z–13:30:03.478494Z. All six phases passed with every setup/call/teardown: migration1, delegation23, concurrency1, worker20, dispatch21 and focused37. Worker case17 passed in this run; the previous setup failure's cause remains unknown.

Default collection matched1539selected/40Node exclusions. Actual calls: **1498 passed,1 failed,40 explicit research/opt-in skips** (39 setup skips and1call skip); all1539teardowns passed. Failed case133 maps to `backend/tests/auth/test_quota.py::test_gate_and_mirror_privileges_are_exact`. No exception or assertion location was retained, so its actual native cause remains **UNKNOWN**. Later assertions in that function are not independently certified.

Parent independently verified the closed25-row local result and hash: `.pi/text-delegation-native-13fb823/run-result.json`, SHA256 `0484f9e175bf5e76488b954547fa9eeba6daf4bd7134aa9620b92618462fc055`. Remote evidence remains below `/home/jre/notebook-tests/text-delegation/.runtime-13fb8234a1750d5a70850644fb350b46d7327dc4/evidence.jsonl`,25rows/538146bytes/SHA256 `b12630014b4cd09a418adc20c82d5ea85eefd8272f8032db096a6051f4815769`.

Operator reported exact-owned2container/2network cleanup, own namespace0/0/0 and credential/config removal. Success-only independent source/namespace/credential postchecks did not run. This is not independent cleanup certification. No retries or additional SSH/diagnostic/cleanup roundtrips occurred.

## Source mismatch and narrow correction

Migration `0006_quota.sql` authorizes five effective gate EXECUTE roles. Migration `0007_text_dispatch.sql:19` intentionally adds `notebook_text_dispatch_owner`, the restricted internal owner of four gate-calling SECURITY DEFINER routines. The current-head quota test still expected only the original five roles. A conforming six-role result therefore fails; an obsolete five-role result passes.

The correction adds **only that single named owner** to the exact sorted expectation. Original five roles, query, strict equality, four later privilege denials, five mirror-column denials, fixtures and skips remain unchanged. No grants, production SQL, application behavior, historical revision fixtures or runtime capabilities change. This source proof does not establish which assertion or driver operation failed natively.

The existing delegation ACL case is strengthened without adding a native phase case:

- exact seven role flags; scheduler LOGIN allowed only during its existing fixture;
- no membership edges touching either new role in either direction;
- schema USAGE required, CREATE denied; gate allowed only for owner, denied for scheduler;
- exact owner, SECURITY DEFINER and fixed path for all four routines;
- exact effective table/column privileges across seven tables and both roles, including forbidden writes and scheduler column grants;
- explicit absence of table/column **WITH GRANT OPTION** authority, including otherwise authorized ordinary privileges.

Table grants satisfy corresponding column privilege checks; column-only grants do not imply table grants. Effective checks include PUBLIC. The roles own routines, not these tables. Expectations target the packet's fresh PostgreSQL16 database and existing migrations, not arbitrary upgraded/template databases. New native assertions remain **PostgreSQL-UNRUN**.

## Durable local proof and reviews

`tests/security/text_delegation/test_quota_acl.py` contains236cases executing two actual source-named assertions against closed catalog doubles. Only bounded, hash-authenticated functions are compiled; modules, fixtures and URL configuration are not imported. Restricted globals and active I/O denial precede execution. Expected rejections must end at the actual source assertion and exact query position; double/scaffold failures do not count. All seven quota query/driver errors propagate unchanged. Balanced contexts are synthetic accounting, not real connection/transaction proof.

Controls cover six-role acceptance, missing/foreign/effective-inherited/scheduler roles, original privilege/column denials, role flags, membership endpoints, schema/gate, four routine authorities, table/column grant drift, missing required columns and additional grant options. Column expectations are independently parsed from fixed0007grants.

Evidence chronology is deliberately separate:

- Diagnosis25countermodels/50actual-function invocations:2successes and48expected assertion rejections;13denial probes, zero unexpected I/O. Not50ordinary passing pytest cases.
- Quota correction: selected authenticated-original **RED1** at assertion199 → corrected **GREEN234**. Initial225 and later9column controls are retained separately; no full234-case RED claim.
- Grant-option correction: selected **RED2** before new assertions → final **GREEN236**, all708phases passing. RED and final test bytes differ as disclosed. Intermediate235/1 and collect-only/zero-call artifacts are preserved and are not successful GREEN evidence.
- Parent independent focused rerun after final grant-option changes: **384 passed in32.88s**; after manifest integration **464 passed in30.35s** (236new +115concurrency +8worker-fixture +80packet +25delegation), scrubbed environment, no DB URL, bytecode/cache disabled.

Independent reviews:

1. `153ddb72-e310-4f0c-98f4-ea93470d8935`: source mismatch confirmed; required proportional owner/scheduler ACL coverage.
2. `4da42457-2459-410f-b52f-cc9b2eef131e`: minimal correction and ordinary matrix correct; identified missing grant-option coverage.
3. `b56e7f45-6398-44a6-89d0-bbd562323d44`: **no issues; prior P2 resolved**. Static PostgreSQL assessment, not native execution.

Ruff retains the narrowly reviewed S102 at the trusted two-function AST execution seam; no suppression, alternate execution API or blanket lint-clean claim. Formatting14files and diff checks passed. Final parent active diagnostics:3changed files, zero visible findings,2confirmed clean/1unconfirmed silent push-only response—not a blanket clean verdict.

Private evidence directories: `.pi/text-delegation-quota-acl-diagnosis/`, `.pi/text-delegation-quota-acl-correction/`, `.pi/text-delegation-quota-grant-option-correction/`. Tool-generated documentation-output spills under `/tmp` and own scratch permission corrections are disclosed in their retained reports; no absolute exclusive-write claim or old-cache cleanup is made.

## Remaining gates

Guarded full collection and two-manifest refresh are verified: **1815 unique collected =1775selected +40 exact unchanged Node exclusions,73files**. All previous1539cases/72files retain membership/order; only236cases/1file were added. Six separate phase collections match existing1/23/1/20/21/37lists exactly. Zero fixtures/test bodies/I/O ran during collection. Parent independently checked manifest membership/order and phase/exclusion byte identity. Worker focused464tests passed in28.416s; parent separately passed464 in30.35s. Evidence: `.pi/text-delegation-quota-acl-collection/REPORT.md`. **1735passes +40research skips remains a forecast**, not a default/native result.

No further SSH, diagnostic, native acceptance or cleanup is authorized by this correction. Future authorization must name the new immutable snapshot/runtime/evidence and reacquire the **same preserved permanent lock inode66306:48129473**, never replace/delete it. Preserve every previous source/runtime/evidence directory. Native acceptance remains HOLD; runtime supervision/heartbeat/startup, real Storage and dependency outage/restart gates remain unfinished. No deployment, capability activation, Access-policy change or BWS provisioning is implied.
