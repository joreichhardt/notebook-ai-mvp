"""Digest- and release-bound operator exception; never a production approval."""

import argparse
import datetime as dt
import json
from pathlib import Path

IMAGE = "supabase/postgres@sha256:f371b5f3f2ac0a05703f33d6e6134515fb2498cab708fb948a0aeb7481467c00"
RELEASE_PARENT = "64473c148726110480f7e14b7c669f970f5c9b14"
EXPIRES = dt.date(2026, 12, 1)
CURL_CVES = (
    "11352", "11586", "12064", "5773", "6276", "8286", "8458", "8925", "8927",
    "92392", "9547",
)
GO_CVES = (
    "111943", "27145", "32280", "32281", "32283", "33810", "33811", "33814",
    "33818", "39820", "39821", "39822", "39836", "42499", "42504", "46600",
    "56853", "56858", "56859", "56860", "56862", "78667", "78669", "97031",
)
# CVE, package, installed version and severity, reconstructed from the retained
# Trivy table of run 44119789187 / job 134170786705. No wildcard approvals.
APPROVED = {
    ("CVE-2026-111497", "brotli-libs", "1.2.0-r0", "CRITICAL"),
    ("CVE-2026-33630", "c-ares", "1.34.6-r0", "HIGH"),
    ("CVE-2026-85091", "zlib", "1.3.2-r0", "HIGH"),
    *((f"CVE-2026-{cve}", pkg, "8.19.0-r0", "HIGH")
      for pkg in ("curl", "libcurl") for cve in CURL_CVES),
    *((f"CVE-2026-{cve}", pkg, "3.5.6-r0", "HIGH")
      for pkg in ("libcrypto3", "libssl3") for cve in ("14456", "45447")),
    *(("CVE-2026-107891", pkg, "1.2.5-r23", "HIGH")
      for pkg in ("musl", "musl-utils")),
    *((f"CVE-2026-{cve}", "stdlib", "v1.26.1", "HIGH") for cve in GO_CVES),
}


def evaluate(report, image, environment, release_parent, today):
    if (environment != "test" or image != IMAGE or release_parent != RELEASE_PARENT
            or today >= EXPIRES):
        raise ValueError("Test exception scope, release ancestry or expiry mismatch")
    return evaluate_findings(report)


def evaluate_findings(report):
    if (report.get("SchemaVersion") != 2 or report.get("ArtifactName") != IMAGE
            or report.get("ArtifactType") != "container_image"
            or not isinstance(report.get("Results"), list) or not report["Results"]):
        raise ValueError("Missing or invalid image scan report")
    count = 0
    for result in report["Results"]:
        if not isinstance(result, dict) or result.get("Class") not in ("os-pkgs", "lang-pkgs"):
            raise ValueError("Invalid vulnerability result")
        findings = result.get("Vulnerabilities")
        if findings is None:
            continue
        if not isinstance(findings, list):
            raise TypeError("Invalid vulnerabilities")
        for finding in findings:
            key = tuple(finding.get(k) for k in
                        ("VulnerabilityID", "PkgName", "InstalledVersion", "Severity"))
            if key not in APPROVED:
                raise ValueError(f"Unapproved finding: {key}")
            # The binary finding approval is limited to the reported gosu target.
            if key[1] == "stdlib" and result.get("Target") != "usr/local/bin/gosu":
                raise ValueError("Unapproved Go binary target")
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--release-parent", required=True)
    args = parser.parse_args()
    count = evaluate(json.loads(args.report.read_text()), args.image, args.environment,
                     args.release_parent, dt.datetime.now(dt.UTC).date())
    print(f"TEST ONLY: {count} known DB findings accepted until {EXPIRES}; no production approval")


if __name__ == "__main__":
    main()
