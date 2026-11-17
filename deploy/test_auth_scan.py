"""Evaluate the operator-approved, digest-bound test-only Auth scan exception."""
import argparse
import datetime as dt
import json
from pathlib import Path

IMAGE = "supabase/gotrue@sha256:1736a63078f5922b198c4cbe50f80ab9a2d3b54fe8b7b6cfb2e9dc5dbbc12c6b"
EXPIRES = dt.date(2026, 12, 1)
APPROVED = {
    ("libcrypto3", "3.5.7-r0", "CVE-2026-14456"),
    ("libssl3", "3.5.7-r0", "CVE-2026-14456"),
    ("musl", "1.2.6-r2", "CVE-2026-107891"),
    ("musl-utils", "1.2.6-r2", "CVE-2026-107891"),
    ("zlib", "1.3.2-r0", "CVE-2026-85091"),
    ("github.com/go-jose/go-jose/v4", "v4.1.4", "CVE-2026-116587"),
    ("github.com/jackc/pgproto3/v2", "v2.3.3", "CVE-2026-32286"),
    ("golang.org/x/net", "v0.57.0", "CVE-2026-78661"),
    ("golang.org/x/net", "v0.57.0", "CVE-2026-78669"),
    ("google.golang.org/grpc", "v1.82.1", "CVE-2026-84304"),
    ("google.golang.org/grpc", "v1.82.1", "CVE-2026-84445"),
    ("stdlib", "v1.27.0", "CVE-2026-111943"),
    ("stdlib", "v1.27.0", "CVE-2026-78667"),
    ("stdlib", "v1.27.0", "CVE-2026-78669"),
    ("stdlib", "v1.27.0", "CVE-2026-97031"),
}


def evaluate(report, image, environment, today):
    if environment != "test" or image != IMAGE or today >= EXPIRES:
        raise ValueError("Exception requires test scope, exact Auth digest and unexpired approval")
    if (report.get("SchemaVersion") != 2 or report.get("ArtifactName") != IMAGE
            or report.get("ArtifactType") != "container_image"
            or not isinstance(report.get("Results"), list) or not report["Results"]):
        raise ValueError("Missing or invalid image scan report")
    accepted = 0
    for result in report["Results"]:
        for finding in result.get("Vulnerabilities") or []:
            key = tuple(finding.get(k) for k in ("PkgName", "InstalledVersion", "VulnerabilityID"))
            if finding.get("Severity") != "HIGH" or key not in APPROVED:
                raise ValueError(f"Unapproved finding: {key}")
            accepted += 1
    return accepted


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--environment", required=True)
    args = parser.parse_args()
    count = evaluate(json.loads(args.report.read_text()), args.image, args.environment,
                     dt.datetime.now(dt.UTC).date())
    print(f"TEST ONLY: {count} known HIGH findings accepted until {EXPIRES}; no production approval")


if __name__ == "__main__":
    main()
