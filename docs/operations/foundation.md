# Deployment foundation — not a production release

The integration Compose file starts only API and static frontend. It binds localhost:18080 on the development host; use a distinct project/port for parallel tests. It intentionally contains no Supabase, credentials, tunnel, migration runner or release workflow. `/auth/v1/` returns 503 until its audited route allowlist and Supabase integration exist. Do not use this file to claim a live deployment.

Verified 2026-11-11: API image builds from the lockfile, imports as UID 10001 with read-only root filesystem, dropped capabilities, no network and no-new-privileges. This is not a rootless-daemon test. Parent's kube1 inventory reports Docker 29.1.3, Compose 2.40.3+ds1-0ubuntu1, no rootless marker, no current Supabase container, occupied mailcow ports 80/443, and an installed bws CLI. Existing services remain untouched.

## Bitwarden provisioning

`deploy/provision_secrets.py` uses bws 2.1.0 (version checked), an explicit HTTPS server and project UUID, and only declared secret IDs. Source: [Bitwarden CLI](https://bitwarden.com/help/secrets-manager-cli/). No `secret list`, shared app account or credential output. The local bootstrap token file must be owned by the invoking user and mode 0600 or stricter. It is passed only to bws child processes, never to application containers. Use a dedicated read-only machine account for this project/environment. Operator must install and checksum-verify bws separately; the script does not auto-download executables.

Prepare a private directory on tmpfs, with capacity for the required secrets. Substitute paths and UUIDs, not secret values, in the manifest. Under the same deployment lock used for rollout:

```sh
python3 deploy/provision_secrets.py --manifest /etc/notebook/secret-ids.json \
  --token-file /etc/notebook/bws-token --destination /run/notebook-secrets/RELEASE_ID
```

The parent directory must exist, be owned by the invoking user and have mode 0700. Every destination is new and immutable. All values are fetched/validated into a private staging directory; partial failures remove staging and preserve existing releases. A successful same-filesystem rename publishes the complete release. No stdout/stderr from bws is forwarded, including on errors. Tokens/config/state live in a private temporary home during the call. Run with core dumps disabled; Python cannot guarantee memory zeroization.

Mount only each service's needed files. API names agreed with A: `NOTEBOOK_DATABASE_URL_FILE=/run/secrets/notebook_database_url` and `NOTEBOOK_SUPABASE_ANON_KEY_FILE=/run/secrets/notebook_supabase_anon_key`; public origin and internal Supabase URL are non-secret configuration. File ownership must be adapted to the rootless UID mapping before mounting; never make the containing directory world-readable. Rotation creates a new release and recreates affected containers with the new mount source; existing bind mounts do not automatically switch. Retain old files until rollback is no longer needed, then remove them. Reboot clears tmpfs: bootstrap retrieval must succeed before startup. An outage blocks a new rollout/restart; it does not erase already mounted running secrets.

Personal provider secrets need a separate, narrowly authorized write lifecycle. This provisioner is only for infrastructure configuration, not the application's user-facing connection API.

## Required next release gates

- Dedicated rootless daemon, user-service restart/linger, volume permissions and effective cgroup limits. Do not stop or migrate the existing rootful daemon. [Docker rootless docs](https://docs.docker.com/engine/security/rootless/).
- Pinned official Supabase Docker stack and per-service hardening compatibility; database migrations, Auth hook, SMTP/OAuth, private storage policies and isolated integration tests.
- API/worker/isolated parser wiring, external provider access and controlled egress.
- Git remote and trusted runner; registry push, immutable runtime digest release manifest, serialized migration/rollout, backup preflight, readiness, schema-compatible rollback and public smoke test.
- Every final runtime digest (including Supabase/proxy/tunnel) scanned; daily production-digest re-scan workflow after the release manifest exists. CI currently scans foundation code/images only. No successful local Trivy scan is claimed.
- Backup database/storage and configuration consistently; encrypt off-host backups, separately protect bootstrap credentials, and restore into isolated new volumes. A written plan is not a tested restore.

## Images and CI

Docker base index digests were resolved from registries on 2026-11-11. API uses Python 3.12 slim Bookworm with UV only in build stage; runtime retains the upstream shell/package tooling pending a validated distroless replacement. Frontend uses Node only for build, nginx unprivileged UID 101 for runtime. These runtime-minimization gaps are release blockers, not approved exceptions.

Actions are commit-pinned. PR CI has contents:read only, hosted runners, no production secrets, and no deployment job. Trivy action 0.35.0 and CLI v0.69.3 are pinned; HIGH/CRITICAL vulnerabilities/misconfigurations and secrets of any severity fail CI, including scanner errors. Image SBOMs and IDs are artifacts. Fresh scans do not constitute a penetration test. [Trivy image CLI](https://trivy.dev/docs/latest/references/configuration/cli/trivy_image/).
