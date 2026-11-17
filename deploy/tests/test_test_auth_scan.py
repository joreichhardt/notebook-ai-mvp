"""Fail-closed tests for the explicitly approved test-image exception."""
import copy
import datetime as dt
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "test_auth_scan_gate", Path(__file__).resolve().parents[1] / "test_auth_scan.py"
)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
TODAY = dt.date(2026, 11, 17)


def report():
    return {
        "SchemaVersion": 2,
        "ArtifactType": "container_image",
        "ArtifactName": gate.IMAGE,
        "Results": [{"Vulnerabilities": [
            {"PkgName": pkg, "InstalledVersion": version, "VulnerabilityID": cve,
             "Severity": "HIGH"}
            for pkg, version, cve in sorted(gate.APPROVED)
        ]}],
    }


def test_exact_fifteen_known_findings_are_accepted():
    assert gate.evaluate(report(), gate.IMAGE, "test", TODAY) == 15


@pytest.mark.parametrize("field,value", [
    ("PkgName", "different-package"), ("InstalledVersion", "different-version"),
    ("VulnerabilityID", "CVE-2099-9999"), ("Severity", "CRITICAL"),
    ("Severity", None),
])
def test_new_or_changed_findings_block(field, value):
    data = report()
    data["Results"][0]["Vulnerabilities"][0][field] = value
    with pytest.raises(ValueError):
        gate.evaluate(data, gate.IMAGE, "test", TODAY)


@pytest.mark.parametrize("image,environment,today", [
    (gate.IMAGE, "production", TODAY), (gate.IMAGE + "other", "test", TODAY),
    (gate.IMAGE, "test", gate.EXPIRES),
])
def test_scope_and_expiration_block(image, environment, today):
    with pytest.raises(ValueError):
        gate.evaluate(report(), image, environment, today)


@pytest.mark.parametrize("field,value", [
    ("SchemaVersion", 1), ("ArtifactName", "different-image"),
    ("ArtifactType", "filesystem"), ("Results", []), ("Results", None),
])
def test_invalid_reports_block(field, value):
    data = copy.deepcopy(report())
    data[field] = value
    with pytest.raises(ValueError):
        gate.evaluate(data, gate.IMAGE, "test", TODAY)


def test_fixed_findings_do_not_need_to_remain_present():
    data = report()
    data["Results"][0]["Vulnerabilities"] = []
    assert gate.evaluate(data, gate.IMAGE, "test", TODAY) == 0
