"""Accept only the verified public libcamera build key in the approved DB image."""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

IMAGE = "supabase/postgres@sha256:f371b5f3f2ac0a05703f33d6e6134515fb2498cab708fb948a0aeb7481467c00"
TARGET = "/nix/store/2ryvqnw8g2lcrvij4hqlm1ihflw2x3hd-ipa-priv-key.pem"
RELEASE_PARENT = "f93fb64a593b15a7b86f297337d0d97d0c0f3762"
EXPIRES = dt.date(2026, 12, 1)


def evaluate(report, image, environment, parent, today):
    if (image != IMAGE or environment != "test" or parent != RELEASE_PARENT
            or today >= EXPIRES):
        raise ValueError("Test exception scope mismatch")
    if (report.get("SchemaVersion") != 2 or report.get("ArtifactName") != IMAGE
            or report.get("ArtifactType") != "container_image"
            or not isinstance(report.get("Results"), list) or not report["Results"]):
        raise ValueError("Invalid scanner report")
    count = 0
    for result in report["Results"]:
        for finding in result.get("Secrets") or []:
            if (result.get("Target") != TARGET or result.get("Class") != "secret"
                    or finding.get("RuleID") != "private-key"
                    or finding.get("Severity") != "HIGH"):
                raise ValueError("Unapproved secret finding; contents withheld")
            count += 1
    if count > 1:
        raise ValueError("Unexpected duplicate secret finding")
    return count


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--release-parent", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    # Raw matches never reach logs, artifacts or disk. Read scanner stdout only.
    report = json.load(sys.stdin)
    try:
        count = evaluate(report, args.image, args.environment, args.release_parent,
                         dt.datetime.now(dt.UTC).date())
    except (ValueError, TypeError, KeyError, AttributeError):
        args.evidence.write_text(json.dumps({"approved": False,
                                            "reason": "unapproved or invalid secret scan"}))
        raise SystemExit("Secret scan blocked; raw matches withheld") from None
    args.evidence.write_text(json.dumps({
        "approved": True, "image": IMAGE, "environment": "test", "count": count,
        "target": TARGET, "rule": "private-key", "expires": EXPIRES.isoformat(),
        "public_file_sha256": "3b3ad9dfa59a1965b74a6f81d8d3b6cad221fd0d8c06411defcf9b0cf0382cfa",
    }, indent=2) + "\n")
    print(f"TEST ONLY: {count} verified public libcamera build-key finding accepted")


if __name__ == "__main__":
    main()
