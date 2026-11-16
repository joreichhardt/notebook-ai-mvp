"""Review gate only: never edits Cloudflare, routes, services or readiness.

Evidence is trusted operator input, not an automatic attestation. Hashes reference
private sanitized acceptance artifacts which the reviewer must independently read.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

from deploy.poc_setup import SetupError, private_value

AUTH_IMAGE = (
    "supabase/gotrue@sha256:1736a63078f5922b198c4cbe50f80ab9a2d3b54fe8b7b6cfb2e9dc5dbbc12c6b"
)
DB_IMAGE = (
    "supabase/postgres@sha256:f371b5f3f2ac0a05703f33d6e6134515fb2498cab708fb948a0aeb7481467c00"
)
REQUIRED_CHECKS = frozenset(
    {
        "github_ci",
        "github_build_scan_publish",
        "image_provenance",
        "real_email_confirmation",
        "first_admin_admission",
        "https_login_session",
        "workspace_notebook_reload",
        "restart_persistence",
        "logout",
        "bad_password",
        "unconfirmed_denied",
        "no_admission_denied",
        "origin_csrf_denied",
        "cross_user_isolation",
        "production_ready_503",
        "documents_chat_disabled",
        "consumer_secret_permissions",
    }
)


class PublicationError(Exception):
    """Fixed non-secret diagnostic only."""


def propose(evidence: dict, *, approved_commit: str, live: dict, now: int | None = None) -> dict:
    try:
        if not re.fullmatch(r"[0-9a-f]{40}", approved_commit):
            raise ValueError()
        if set(evidence) != {
            "profile",
            "hostname",
            "commit",
            "completed_at",
            "first_admin_email",
            "checks",
            "images",
        }:
            raise ValueError()
        if (
            evidence["profile"] != "login-workspace-v1"
            or evidence["hostname"] != "note.yokoma.de"
            or evidence["commit"] != approved_commit
            or evidence["first_admin_email"] != "johannes@yokoma.de"
        ):
            raise ValueError()
        now = int(time.time()) if now is None else now
        completed = evidence["completed_at"]
        if type(completed) is not int or not -5 <= now - completed <= 3600:
            raise ValueError()
        checks = evidence["checks"]
        if set(checks) != REQUIRED_CHECKS:
            raise ValueError()
        for result in checks.values():
            if (
                set(result) != {"passed", "artifact_sha256"}
                or result["passed"] is not True
                or not re.fullmatch(r"[0-9a-f]{64}", result["artifact_sha256"])
            ):
                raise ValueError()
        images = evidence["images"]
        if set(images) != {"api", "frontend", "auth", "db"}:
            raise ValueError()
        for name in ("api", "frontend"):
            if not re.fullmatch(
                r"ghcr\.io/joreichhardt/notebook-ai-mvp-" + name + r"@sha256:[0-9a-f]{64}",
                images[name],
            ):
                raise ValueError()
        if images["auth"] != AUTH_IMAGE or images["db"] != DB_IMAGE:
            raise ValueError()
        expected = {
            "status": "ready",
            "profile": "login-workspace-v1",
            "release": approved_commit,
            "documents": False,
            "chat": False,
        }
        if live != expected or live["documents"] is not False or live["chat"] is not False:
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PublicationError("PoC publication evidence rejected") from None
    return {
        "hostname": "note.yokoma.de",
        "service": "http://127.0.0.1:18191",
        "httpHostHeader": "note.yokoma.de",
        "profile": "login-workspace-v1",
        "commit": approved_commit,
        "action": "proposal-only",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-file", type=Path, required=True)
    parser.add_argument("--approved-commit", required=True)
    args = parser.parse_args()
    try:
        evidence = json.loads(private_value(args.evidence_file))
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            response = client.get(
                "http://127.0.0.1:18191/api/poc/ready", headers={"Host": "note.yokoma.de"}
            )
        if response.status_code != 200:
            raise PublicationError("PoC live readiness rejected")
        result = propose(evidence, approved_commit=args.approved_commit, live=response.json())
    except (SetupError, PublicationError, httpx.HTTPError, ValueError):
        print("PoC publication rejected; no route changed.", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
