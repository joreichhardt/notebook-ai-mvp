# Security status

This is an implementation foundation, not an approved production system.

Trust boundaries: browser → BFF; BFF/worker → restricted PostgreSQL + Supabase; parser → no network/credentials; deployment → Bitwarden; personal model connection → owner-scoped provider. Browser input, documents, model text, CI PRs and external endpoints are untrusted. Workspace membership never grants another user's credentials.

Primary risks and required evidence:
- Cross-workspace reads: API authorization plus RLS tests with a non-owner/non-bypass role.
- Secret exposure: targeted bws retrieval, private atomic release files, redacted errors, no application inheritance of bootstrap token. Negative provisioner tests cover project mismatch, fetch failure, path traversal and token permissions.
- Malicious uploads/SSRF: bounded isolated parser and provider endpoint allowlist, still pending integration.
- Supply chain/deployment: pinned inputs, hosted PR checks, mandatory scans, trusted release runner and digest-bound rollout. Production rollout remains absent until gates are satisfied.
- Host compromise: non-root containers and rootless daemon are separate requirements. Rootless remains unverified on kube1.

Incident response: stop new affected jobs, revoke impacted personal connections or machine-account tokens, rotate dependent secrets, preserve sanitized audit metadata, identify scope, then restore only into a verified isolated environment. Do not publish secret values in issues, scan artifacts, screenshots or videos.

No security exception has been granted. See docs/operations/foundation.md for open gates and recovery requirements.
