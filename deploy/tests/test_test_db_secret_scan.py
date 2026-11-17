"""No general secret bypass and no raw-match output."""

import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "test_db_secret_scan.py"
SPEC = importlib.util.spec_from_file_location("db_secret_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def report():
    return {"SchemaVersion": 2, "ArtifactName": gate.IMAGE, "ArtifactType": "container_image",
            "Results": [{"Target": gate.TARGET, "Class": "secret", "Secrets": [
                {"RuleID": "private-key", "Severity": "HIGH", "Match": "SENTINEL_NOT_A_KEY"}
            ]}]}


def check(data, **kwargs):
    args = {"image": gate.IMAGE, "environment": "test", "parent": gate.RELEASE_PARENT,
            "today": dt.date(2026, 11, 17)}
    args.update(kwargs)
    return gate.evaluate(data, **args)


def test_exact_public_key_finding():
    assert check(report()) == 1


@pytest.mark.parametrize("field,value", [("Target", "/other/key.pem"), ("Class", "other")])
def test_other_paths_and_classes_block(field, value):
    data = report()
    data["Results"][0][field] = value
    with pytest.raises(ValueError):
        check(data)


@pytest.mark.parametrize("field,value", [("RuleID", "aws-access-key-id"), ("Severity", "CRITICAL")])
def test_other_findings_block(field, value):
    data = report()
    data["Results"][0]["Secrets"][0][field] = value
    with pytest.raises(ValueError):
        check(data)


@pytest.mark.parametrize("changes", [
    {"image": "different"}, {"environment": "production"}, {"parent": "different"},
    {"today": gate.EXPIRES},
])
def test_scope_blocks(changes):
    with pytest.raises(ValueError):
        check(report(), **changes)


def test_invalid_report_and_additional_finding_block():
    with pytest.raises(ValueError):
        check({})
    data = report()
    data["Results"].append({"Class": "secret", "Target": "/other", "Secrets": [
        {"RuleID": "private-key", "Severity": "HIGH"}]})
    with pytest.raises(ValueError):
        check(data)


def test_cli_failure_never_emits_raw_matches(tmp_path):
    evidence = tmp_path / "evidence.json"
    result = subprocess.run([
        sys.executable, str(SCRIPT), "--image", gate.IMAGE, "--environment", "production",
        "--release-parent", gate.RELEASE_PARENT, "--evidence", str(evidence),
    ], input=json.dumps(report()), capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "SENTINEL_NOT_A_KEY" not in result.stdout + result.stderr + evidence.read_text()
    assert json.loads(evidence.read_text())["approved"] is False
