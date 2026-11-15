# Task6 — historical worker fixture correction

## Native baseline remains HOLD

The one authorized native attempt for `32c4c686095407221fab64631fb82ed118667eff` ran on kube1 on 2026-11-14, 22:12:27.274685Z–22:13:45.836440Z. Migration 1, delegation 23 and concurrency 1 passed. Worker collected 20 cases: 19 calls passed; case 17 failed during setup; all 20 teardowns passed. Dispatch, focused and default regression were not run.

Case 17 is `backend/tests/ingestion/test_worker.py::test_pdf_job_has_explicit_unsupported_failure_without_io`. No raw exception or SQL was retained; the actual native cause remains **UNKNOWN**. Operator-reported cleanup was own namespace 0/0/0 with credentials removed. Success-only independent postchecks were not executed. This is not independent cleanup certification.

Local retained result: `.pi/text-delegation-32c4c68-resume/run-result.json`, SHA256 `8f9f7c69ccf1d2d2dadc06be8c7d803559c2d4caf5780d4240b06f6965af3473`. Remote retained evidence: `.runtime-32c4c686095407221fab64631fb82ed118667eff/evidence.jsonl` below `/home/jre/notebook-tests/text-delegation`, 22 closed rows, SHA256 `6773ae3755e3d6d6724df12f2110c34313f41e282f4a163660ba2ea2e8ec6553`.

## Proven source defect and narrow correction

The packet explicitly uses pytest importlib mode. The historical fixture's `__import__(__name__)` returns the top-level `backend` package rather than its dotted worker test module. Patching that object's URL either raises before fixture yield or, if the attribute exists, silently targets the wrong namespace.

The sole existing-source change replaces that expression with `monkeypatch.setitem(globals(), "URL", legacy_url)`. Revision `0005`, four frozen lease hooks, all 20 worker cases and their order, PDF assertions, skip marker and packet import mode are preserved. No production application, SQL, privileges or readiness configuration changes.

This source defect does **not** establish historical native causation. CREATE, migration and DROP failures remain compatible with the retained setup-only outcome.

## Durable local regression evidence

`tests/security/text_delegation/test_worker_fixture.py` adds eight parametrized cases: importlib/prepend × success/test-call failure/scene-setup failure/database-DROP failure. Public pytest collection imports the actual worker module; a miniature module re-exports its actual fixtures for real pytest scheduling. Original worker bodies do not run.

Tests check isolated scene routing, hook lifetime, cleanup order, URL/hook restoration and a subsequent non-PDF case. Scoped import/environment isolation preserves already collected modules, parent-package attribute, skip markers, `sys.path` and `PYTEST_CURRENT_TEST`. A private synthetic environment mapping exercises incoming database settings without installing a DB URL in the process. Actual DB, network and subprocess entry points are denied; helper dependencies are doubles.

- Same regression bytes, original-source RED: 4 importlib failures / 4 prepend passes, at the wrong-namespace boundary.
- Corrected-source GREEN: 8 passes; zero unexpected I/O.
- Independent parent focused checks: **228 passed in 21.80s** (new 8 plus prior correction/packet/delegation controls), scrubbed environment, no DB URL, bytecode/cache disabled.
- Changed-source Ruff/format and diff checks passed. Parent active diagnostics found no errors; one informational spelling finding in an unchanged worker comment remains. No blanket all-artifacts-clean claim.
- Independent diagnosis review `de6fbf87-b7c3-4b24-9944-be3d2bc5b01a` confirmed the defect and required actual scheduler coverage.
- Independent implementation review `2dbb0170-dba7-4bd6-91e7-8093a041818a` found no issues; collection integration remained pending at that review.

Evidence is retained under `.pi/text-delegation-worker-setup-diagnosis/` and `.pi/text-delegation-worker-fixture-correction/`. Development scaffold failures are retained separately and are not valid RED evidence. The diagnosis worker's possible ignored Ruff-cache side effect was disclosed and preserved; no cache cleanup or old-byte equality is claimed.

## Remaining gates

Collection refresh is verified locally: **1579 collected =1539 selected +40 exact Node exclusions**, 72 files. The old1531 selected cases and71files remain in exactly the same order; only8 fixture cases and1file were added to `regression_cases.json` and `regression_files.json`. Phase lists and Node exclusions remain byte-identical. Parent independently checked these before/after memberships and ordering. Collection-worker focused validation: **203 passed /20.665s**, zero denied I/O. Evidence: `.pi/text-delegation-worker-fixture-collection/REPORT.md`. Expected **1499 passes plus40 research skips** remains a forecast, never a native result.

No further SSH, diagnostic or native run is authorized by this local correction. A future explicit approval must name the new immutable snapshot/runtime/evidence and reacquire the **same preserved permanent namespace lock inode `66306:48129473`**, never replace or delete it. Preserve both old source/runtime/evidence directories. No deployment, capability activation, BWS provisioning or accepted older acceptance rerun is implied.

Task6 supervisor/heartbeat/startup, real Storage and dependency outage/restart acceptance remain unfinished. Readiness and capabilities remain closed.
