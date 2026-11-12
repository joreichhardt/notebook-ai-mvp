# F3a — independent integrated-seam review

## Delivery

- Reviewed candidate `a44d4c28457c33b8f5714ca87c5de009c6b1eebd` against `06c56b60db1f53ebfa9655a39cbf174418ea0c29`, not implementation worktrees.
- Branch/worktree: `notebook-seam-review-v3`, `/home/jre/orca/workspaces/gemini-nb-clone/notebook-seam-review-v3`.
- Report and regression commit: **`00c4d82926363e692413344444ef05f6a7a4d3f9`**. This handoff is a separate documentation commit; its SHA is in worker_done.
- Files: `docs/review/seam-v3.md`, `tests/security/seam_v3/test_database_boundaries.py`, `tests/security/seam_v3/test_protocol_boundaries.py`, this handoff only. Preexisting `.pi/` preserved/untracked. No implementation or existing test changes, nested agents, deployment or live credentials.

## Finding / disposition

**F3a-01, Medium/P2, confirmed:** an admitted same-workspace editor can DELETE/reinsert an immutable shared index-profile UUID with altered model/revision/dimensions while another actor's private job continues to reference it. The editor cannot read that job or its personal connection. Migration `0003_documents.sql:90-91,106-107,165-166` combines a deferred FK, UPDATE-only immutability and runtime DELETE/INSERT permission. Real PostgreSQL reproduction returns replacement metadata through the owner's original job.

This is a persistence integrity defect to fix before the next embedding/dispatch slice, not a claimed live credential leak or enabled HTTP exploit. A should remove runtime profile deletion or use an appropriate immediate/restrict reference, preserve immutable identity, and retest notebook cascade cleanup. Use a corrective migration if already applied beyond disposable fixtures. The test is a strict xfail for explicit tracking; `--runxfail` shows the red assertion and must pass after the fix, then remove the marker. No existing tests were weakened.

No additional confirmed cross-workspace/historical read, generic definer role escalation or endpoint escape was found. Known caller responsibilities (trusted dispatch, source authorization, fenced publication composition) and C3's pre-adapter SDK/DB deadline/capacity gap are explicitly retained as gates, not hidden or counted as newly discovered defects.

## Verification actually performed

Own `.venv` via `uv sync --frozen --quiet`; fresh isolated PostgreSQL **16.15** at reserved **55441**, unique `/tmp/notebook-f3a-pg.U6ywZV21/data`, temporary DB `notebook_f3a_review`. Fresh migration through 0003 succeeded. One initial initdb/log-path setup error was corrected before startup; details in report. Ports 5432/55439/55440 untouched.

```sh
NOTEBOOK_TEST_POSTGRES_URL=postgresql://jre@127.0.0.1:55441/notebook_f3a_review \
  .venv/bin/python -m pytest tests/security/seam_v3 \
  backend/tests/integration/test_execution_database.py -q --tb=short
# 13 passed, 1 strict xfailed (2.43 s)

# Explicit reproduction also run:
NOTEBOOK_TEST_POSTGRES_URL=postgresql://jre@127.0.0.1:55441/notebook_f3a_review \
  .venv/bin/pytest tests/security/seam_v3/test_database_boundaries.py::test_referenced_profile_identity_cannot_be_replaced \
  --runxfail -q --tb=short
# Expected red: 1 failed; replacement/v2/7 instead of original/v1/3.

.venv/bin/ruff check tests/security/seam_v3
# Passed.
git diff --cached --check
# Passed before report/test commit.
```

Additional passing checks cover definer grants, actual separate-connection publication/tombstone serialization, in-flight admission/disconnect cancellation using default fresh restricted DB lookups, and seven adversarial/fragmented protocol cases. Main's two DB integration tests were rerun; vault/provider interactions remain synthetic. Active LSP: both new test files confirmed clean, zero errors/inconclusive. No repeat of the full main suite/build/frontend/provisioner gates was needed or claimed.

**Cleanup done:** temporary DB dropped; own cluster stopped and `pg_ctl status` confirmed no server running. Synthetic cluster directory/logs retained locally. No shared environment mutations.

## Next dispatch recommendation

After the profile fix, review and build one actor-scoped text-only upload/job slice with durable actor binding, fresh authorization and fenced same-transaction publication. Private Storage must use real Supabase policies and exact server-generated object keys, compensation and narrowly scoped cleanup work recorded before tombstoning; do not reopen historical rows or introduce generic privileged worker SQL. Provision bounded no-network/no-credentials/no-socket parser isolation before PDF. Keep dispatch recovery, parser limits, aggregate quotas, source/lease cancellation, model compatibility and Storage proofs as explicit enablement gates. Full rationale and concrete sequencing: `docs/review/seam-v3.md`.

**Review task complete; product acceptance remains ungranted and F3a-01 remains open for A/main.**
