"""Exact scope and fail-closed regression checks for the DB test exception."""

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "db_scan_gate", Path(__file__).resolve().parents[1] / "test_db_scan.py"
)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
TODAY = dt.date(2026, 11, 17)


def report():
    results = []
    for cve, package, version, severity in sorted(gate.APPROVED):
        results.append({
            "Class": "lang-pkgs" if package == "stdlib" else "os-pkgs",
            "Target": "usr/local/bin/gosu" if package == "stdlib" else "alpine",
            "Vulnerabilities": [{"VulnerabilityID": cve, "PkgName": package,
                                 "InstalledVersion": version, "Severity": severity}],
        })
    return {"SchemaVersion": 2, "ArtifactType": "container_image",
            "ArtifactName": gate.IMAGE, "Results": results}


def evaluate(data, **kwargs):
    args = {"image": gate.IMAGE, "environment": "test",
            "release_parent": gate.RELEASE_PARENT, "today": TODAY}
    args.update(kwargs)
    return gate.evaluate(data, **args)


def test_exact_54_high_and_one_critical():
    assert len(gate.APPROVED) == 55
    assert sum(row[3] == "CRITICAL" for row in gate.APPROVED) == 1
    assert evaluate(report()) == 55


@pytest.mark.parametrize("field,value", [
    ("VulnerabilityID", "CVE-2099-1"), ("PkgName", "other"),
    ("InstalledVersion", "other"), ("Severity", "UNKNOWN"),
])
def test_changed_finding_is_not_approved(field, value):
    data = report()
    data["Results"][0]["Vulnerabilities"][0][field] = value
    with pytest.raises(ValueError):
        evaluate(data)


@pytest.mark.parametrize("changes", [
    {"environment": "production"}, {"image": "other"},
    {"release_parent": "other"}, {"today": gate.EXPIRES},
])
def test_scope_mismatch_blocks(changes):
    with pytest.raises(ValueError):
        evaluate(report(), **changes)


@pytest.mark.parametrize("field,value", [
    ("ArtifactName", "other"), ("ArtifactType", "filesystem"),
    ("SchemaVersion", 1), ("Results", []), ("Results", None),
])
def test_invalid_report_blocks(field, value):
    data = report()
    data[field] = value
    with pytest.raises(ValueError):
        evaluate(data)


def test_new_critical_and_other_go_binary_block():
    data = report()
    high = next(r for r in data["Results"] if r["Vulnerabilities"][0]["Severity"] == "HIGH")
    high["Vulnerabilities"][0]["Severity"] = "CRITICAL"
    with pytest.raises(ValueError):
        evaluate(data)
    data = report()
    next(r for r in data["Results"] if r["Class"] == "lang-pkgs")["Target"] = "other"
    with pytest.raises(ValueError):
        evaluate(data)


def test_fixed_findings_need_not_remain_present():
    data = report()
    for result in data["Results"]:
        result["Vulnerabilities"] = []
    assert evaluate(data) == 0


def test_workflow_preserves_gates_and_reads_real_parent():
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml").read_text()
    assert "fetch-depth: 2" in workflow
    assert "parent=$(git show -s --format=%P HEAD)" in workflow
    assert '--environment test --release-parent "$parent"' in workflow
    assert '--exit-code 1 "$image"' in workflow
    assert "--scanners secret" in workflow
    assert "continue-on-error" not in workflow
