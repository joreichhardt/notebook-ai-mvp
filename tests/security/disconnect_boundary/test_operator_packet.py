"""Pure local operator/report tests: never invoke subprocesses, DB or network."""

import stat

import operator_f4
import pytest
import run_f4


def test_safe_report_keeps_only_structured_evidence():
    report = {
        "stage": "postgres",
        "ok": False,
        "passed": 0,
        "failed": 1,
        "skipped": 0,
        "expected": 30,
        "cases": [
            {
                "test": "test_packet.py::test_case[test]",
                "phase": "call",
                "outcome": "failed",
                "error_type": "Failed",
                "label": "F4 unexpected HTTP status",
            }
        ],
    }
    assert operator_f4.safe_report(report) is report


@pytest.mark.parametrize("fault", ["unknown-field", "stage", "type", "label"])
def test_safe_report_rejects_unreviewed_fields(fault):
    report = {"stage": "postgres", "ok": False}
    if fault == "unknown-field":
        report["raw_error"] = "SYNTHETIC_PRIVATE_DIAGNOSTIC"
    elif fault == "stage":
        report["stage"] = "unrecognized"
    elif fault == "type":
        report["error_type"] = "SYNTHETIC: diagnostic"
    else:
        report["cases"] = [
            {
                "test": "test_packet.py::test_case",
                "phase": "call",
                "outcome": "failed",
                "label": "SYNTHETIC_PRIVATE_DIAGNOSTIC",
            }
        ]
    with pytest.raises(RuntimeError):
        operator_f4.safe_report(report)


def test_private_file_is_exclusive_and_private(tmp_path):
    target = tmp_path / "synthetic"
    run_f4.private_file(target, "SYNTHETIC_UNUSED")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        run_f4.private_file(target, "REPLACEMENT")
    assert target.read_text() == "SYNTHETIC_UNUSED"


def test_error_kind_never_contains_exception_message():
    assert run_f4.error_kind(RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")) == "RuntimeError"


def test_empty_pytest_run_cannot_claim_acceptance(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(run_f4, "SOURCE", Path(__file__).resolve().parents[3])
    monkeypatch.setattr(pytest, "main", lambda *args, **kwargs: 0)
    report = run_f4.pytest_report("postgres")
    assert report["ok"] is False
    assert report["expected"] == 30
    assert report["passed"] == 0


def test_reporter_collects_without_terminal_plugin(monkeypatch, tmp_path):
    from pathlib import Path

    synthetic = tmp_path / "test_safe_probe.py"
    synthetic.write_text("def test_safe_probe():\n    assert True\n")
    monkeypatch.setattr(run_f4, "SOURCE", Path(__file__).resolve().parents[3])
    monkeypatch.setattr(run_f4, "FOCUSED", [str(synthetic)])
    report = run_f4.pytest_report("focused")
    assert report["passed"] == 1
    assert report["ok"] is False  # One synthetic case is not a full acceptance run.
