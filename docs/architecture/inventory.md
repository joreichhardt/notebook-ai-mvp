# Read-only inventory · 2026-11-11

- Repository initially only empty commit plus untracked requested instructions. No Git remote configured. Baseline 0c75d3c89fe50bd576fe123ec831d59eaa073556.
- kube1 reachable over existing SSH. Docker 29.1.3; Compose 2.40.3+ds1-0ubuntu1.
- Inspected sudo Docker daemon reports AppArmor, default seccomp and cgroup namespace, no rootless marker. Rootless target is not yet verified/provisioned; this is a release blocker, not an accepted exception.
- Approximately 26 GiB available RAM, 1.7 TiB free root filesystem at inspection.
- Existing services include Mailcow, STAMPF, YOKOMA, Excalidraw and legal MCP. No running Supabase container found in this daemon. Ports 80/443 already belong to Mailcow. Existing services remain unchanged.
- bws executable exists at /usr/local/bin/bws. No secret values or authentication files inspected. Application project, machine account and secret mapping not verified.
- No public DNS/tunnel, runner, SMTP or Google OAuth configuration for this application has been verified. Existing unrelated tunnels do not establish authorization/configuration for note.yokoma.de.
