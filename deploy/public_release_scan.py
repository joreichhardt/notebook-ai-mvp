"""Check the exact Notebook public-release exceptions; private gates are unchanged."""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from deploy import test_auth_scan, test_db_scan, test_db_secret_scan

HOST = "note.yokoma.de"
EXPIRES = min(test_auth_scan.EXPIRES, test_db_scan.EXPIRES, test_db_secret_scan.EXPIRES)
SCANS = {"auth": test_auth_scan, "db": test_db_scan, "db-secret": test_db_secret_scan}


def evaluate(kind, report, *, image, host, release, approved_release, today, file_hash=None):
    scan = SCANS[kind]
    if (
        host != HOST
        or image != scan.IMAGE
        or re.fullmatch(r"[0-9a-f]{40}", release) is None
        or release != approved_release
        or today >= EXPIRES
    ):
        raise ValueError("Public release exception scope, revision or expiry mismatch")
    if kind == "db-secret" and file_hash != scan.PUBLIC_FILE_SHA256:
        raise ValueError("Public build artifact hash mismatch")
    field = "Secrets" if kind == "db-secret" else "Vulnerabilities"
    for result in report.get("Results") or []:
        findings = result.get(field)
        if findings is not None and not isinstance(findings, list):
            raise ValueError("Malformed scan findings")
    return scan.evaluate_findings(report)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("kind", choices=SCANS)
    parser.add_argument("--image", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--approved-release", required=True)
    parser.add_argument("--file-hash")
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    try:
        count = evaluate(
            args.kind,
            json.load(sys.stdin),
            image=args.image,
            host=args.host,
            release=args.release,
            approved_release=args.approved_release,
            today=dt.datetime.now(dt.UTC).date(),
            file_hash=args.file_hash,
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        raise SystemExit("Public release scan blocked; raw findings withheld") from None
    args.evidence.write_text(
        json.dumps(
            {
                "scope": "public-notebook",
                "hostname": HOST,
                "release": args.release,
                "scan": args.kind,
                "image": args.image,
                "count": count,
                "expires": EXPIRES.isoformat(),
                "file_hash": args.file_hash,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
