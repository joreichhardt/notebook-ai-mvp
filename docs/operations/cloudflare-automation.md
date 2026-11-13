# Notebook: guarded Cloudflare CLI

## Scope and current status

`python3 -m deploy.cloudflare MODE` is a standalone operator entry point, **not** a
release pipeline. No new Actions workflow, automatic rollout or production
credentials are installed. Main owns release integration and approval.

The tool owns only the existing `note.yokoma.de` route on tunnel
`4c7b0b51-fdb2-4890-83ec-a4ca3fe01cb4` (`mailcow-yokoma`) and a new dedicated
Notebook Access application. Existing DNS is reconciled by exact validation:
exactly one proxied CNAME to this tunnel, with the reviewed record ID. Missing,
duplicate or changed records stop execution; the tool does **not** adopt,
create, replace or delete DNS records. Initial DNS bootstrap was already done.

Only the Notebook ingress `service` changes between `http_status:503` and the
explicit private-IP HTTP origin. All other ingress rules and configuration
fields are retained. Unknown overrides, route shadowing, identity drift,
noncanonical DNS aliases, overlapping Access apps and unexpected policies stop
execution. No unowned app/policy is adopted or removed.

### Concurrency is an operational gate

Cloudflare's configuration PUT replaces the **whole** configuration. A fresh
read/version/fingerprint check is **not atomic CAS**. The operator must arrange:

1. One coordinated writer host/user and fixed lock pathname
   `/run/lock/notebook-cloudflare-tunnel.lock` for **every writer of this tunnel**,
   including automation for unrelated hostnames.
2. No concurrent Cloudflare Dashboard, API, Terraform or other-host writes
   throughout inspection, activation, verification and recovery. This exclusion
   also covers Access application/policy changes.
3. Explicit `--exclusive-writer` acknowledgement. It records an operator assertion,
   not a technical distributed lock. If exclusion cannot be guaranteed, do not write.

The CLI uses nonblocking `flock`, checks ownership/mode/link count and never
unlinks the lock inode. A notebook-specific lock that other tunnel writers ignore
is insufficient. A dedicated tunnel is a possible future architectural decision,
not created by this tool. Lock provisioning/permissions require a coordinated host
setup; do not loosen permissions to make a command succeed.

## Protected input and credentials

All paths below are proposed operator paths, not files supplied by the repository.
Keep the parent directory private (0700), files 0600, owned by the executing user.
The settings, token and state reader rejects symlinks and nonregular files. Never
print inputs, command traces, API bodies, cookies, redirects or bearer values.

`--settings /etc/notebook/cloudflare/settings.json` contains:

- `hostname`: `note.yokoma.de`
- `tunnel_id`: the fixed tunnel UUID above
- `account_id`, `zone_id`, `dns_record_id`: verified 32-character lowercase hex IDs
- `origin`: actual connector-reachable private IP and port, e.g.
  `http://10.20.30.40:18080` (example only; never assume this is the live address)
- `release`: exact 40-character lowercase Git SHA for the approved deployment
- `access.email`: **the sole user-authorized owner identity**, entered through
  protected operator configuration; no real email in Git
- `access.team_domain`: verified `<team>.cloudflareaccess.com` login hostname

`--token-file /run/notebook-cloudflare/api-token` contains only the dedicated
Notebook Cloudflare API bearer token, resolved from Bitwarden. It needs only the
reviewed zone DNS read, account tunnel read/edit and Access app/policy read/edit
permissions needed by the API calls. Account-scoped tunnel permissions may reach
other tunnels; document this residual privilege and use least-privilege scoping
supported by Cloudflare. No personal Excalidraw credentials, service tokens or
full-project secret dumps. Do not copy the legacy homeserver admin token into
Notebook automation.

Use the pinned `bws` version and explicit project/server under the project's
secret-provisioning policy. Bootstrap token remains outside Git, only in the
retrieval process. This CLI does **not** retrieve secrets or pass them to app
containers. The release owner must bind the targeted retrieval step to this CLI,
validate inputs before changes, keep the runtime token file private and remove
it after the operation. At Bitwarden outage or reboot with unavailable runtime
credentials: fail before writes, leave the existing route and Access unchanged;
no empty fallback. Rotate with controlled provisioning, revalidation and removal
of old runtime material. This lifecycle is not live-tested here.

`--access-state /etc/notebook/cloudflare/access-state.json` is created atomically
and privately on first preparation; existing state is never overwritten. It
contains app/account/hostname IDs, not email or tokens. Back it up privately.
After ambiguous POST or state-save failure, stop and reconcile actual ownership
with Main; do not blindly retry, adopt or delete a possibly created application.

## Modes and ordered release gates

From the repository root, first use `--help`. Supply protected settings and token
paths (and `--access-state` whenever settings contain `access`). No shell snippet
in this document should be run as a production rollout without Main coordination.

- `inspect` / `dry-run`: read-only API validation; output only mode, version,
  fingerprint, tunnel health and `placeholder`/`origin`. `dry-run` performs no
  readiness/public check and grants no release approval. With Access configured,
  prepare it first or use `access-inspect` for diagnosis.
- `access-prepare --exclusive-writer`: requires the existing 503 placeholder,
  checks overlapping apps, creates a dedicated whole-host self-hosted app with
  one exact-email Allow policy, saves ownership and verifies control-plane state
  plus unauthenticated public denial. Repeated preparation inspects only.
- `access-inspect`: checks app ownership/scope, one exact-email Allow policy,
  no preflight/WARP bypass, no other policies, then DNS/TLS/Access redirects.
- `probe --connector-pid PID`: no API token required; enter only the network
  namespace of a verified cloudflared executable and probe the actual origin.
  Main must independently identify this PID as the connector for this tunnel
  and approve the namespace probe. It requires appropriate namespace privileges;
  the CLI never escalates. No Docker exec and no connector environment/arguments
  are read. A PID's executable name alone does not prove tunnel ownership.
- `activate --exclusive-writer --release-approved --expected-fingerprint HASH
  --connector-pid PID`: exact Main-approved release and reviewed current snapshot;
  Access inspection and public denial precede the origin readiness probe. Healthy
  tunnel and exact readiness are mandatory. Repeated activation rechecks probes
  but does not issue a PUT if unchanged.
- `verify`: DNS, TLS and current public behavior, without writes.
- `rollback --exclusive-writer --expected-fingerprint HASH`: freshly inspect and
  set only the owned service to 503, leaving DNS, Access and foreign rules intact.
  Repeated rollback does not write. No old whole-tunnel backup is replayed.

A failed public check after a confirmed activation triggers fresh-state rollback
and another public check. An ambiguous PUT, drift, unexpected owned service or
failed readback **does not** trigger a blind compensating write. Inspect first,
review a new fingerprint and coordinate recovery. An error may mean the server
accepted the write; never treat the previous state as assured.

## Probe contract and evidence limits

The proposed **future** internal readiness contract is:

- `GET /api/health/ready`: 200, JSON exactly
  `{"status":"ready","release":"<approved full SHA>"}`, `Cache-Control: no-store`.
- `/`: 200 HTML containing an HTML element.
- `/rest/v1/`, `/storage/v1/`, `/auth/v1/admin/users`, `/studio/`, `/pg/`,
  `/api/docs`, `/api/openapi.json`: 403 or 404, not SPA fallback HTML.

Main confirmed on 2026-11-13 that current `50b16dd` has only liveness, **no real
readiness/release endpoint**. Do not alias liveness, manufacture proxy JSON or
weaken the probe. Main must implement dependency-aware readiness. A local HTML
probe is not an upload/chat/OAuth/streaming acceptance test.

Public probes require normal system DNS **and** Cloudflare DoH A answers, normal
TLS validation/SNI for the system-resolved route and every returned A address.
They follow no redirects. With Access configured, the tested root/readiness/admin
paths must return the configured Access team's HTTPS login redirect. This proves
unauthenticated denial, **not authenticated public application readiness**. A
503 rollback remains behind Access: anonymous clients see login, not the 503.
Control-plane inspection proves the configured placeholder, not authenticated
edge propagation. IPv6 edge connectivity is not exhaustively tested.

Before calling the preview usable, separately run a coordinated browser test
with the authorized owner: Cloudflare login, app login/admission, frontend,
ready/release, required Auth callbacks and a representative upload/chat flow;
verify an unauthorized identity is denied. Do not introduce a service-token
bypass or broad policy to automate this. Access never replaces app auth, RLS,
CSRF, invitations or readiness. Whole-host Access can affect OAuth callbacks and
must be tested explicitly; no unreviewed path exception.

## Verification and official references

Synthetic tests (no network/secrets):

```sh
python3 -m unittest discover -s deploy/tests -q
uv run --frozen ruff check deploy/cloudflare deploy/tests/test_cloudflare.py
uv run --frozen ruff format --check deploy/cloudflare deploy/tests/test_cloudflare.py
```

Reviewed official API reference and Cloudflare Python SDK source on 2026-11-13:

- https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/methods/create/
- https://github.com/cloudflare/cloudflare-python/blob/main/src/cloudflare/types/zero_trust/access/application_create_params.py
- https://github.com/cloudflare/cloudflare-python/blob/main/src/cloudflare/resources/zero_trust/tunnels/cloudflared/configurations.py

Tunnel `update` is documented as replacement, not a patch; its declared request
has `config`, no version/CAS precondition. Access documents inline policies or
reusable links as mutually exclusive. The generated inline-policy schema omits
some policy fields; this CLI uses explicit inline name/decision/include and
verifies the resulting app/policy via GET. Actual acceptance and enforcement
remain a **live integration gate**, not proven by the synthetic API fixture.
