"""Closed evidence schema; collection contracts are reviewed alongside source."""

import json
from pathlib import Path
from typing import Any

RELATIVE = Path("tests/security/text_delegation")
PHASES = ("migration", "delegation", "concurrency", "worker", "dispatch", "focused")


class Refused(RuntimeError):
    """Fixed failure class; never disclose exception messages or subprocess output."""


def need(condition):
    if not condition:
        raise Refused()


def unique(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result)
        result[key] = value
    return result


def contract():
    data = json.loads(Path(__file__).with_name("cases.json").read_text(), object_pairs_hook=unique)
    need(tuple(data) == PHASES)
    for cases in data.values():
        need(type(cases) is list and 0 < len(cases) < 500)
        need(all(type(case) is str for case in cases) and len(set(cases)) == len(cases))
    return data


def case_key(item):
    """Parameter indices avoid exporting arbitrary parametrized values or raw node IDs."""
    path = item.nodeid.split("::", 1)[0]
    key = path + "::" + item.originalname
    if hasattr(item, "callspec"):
        key += "[" + ",".join(f"{k}={v}" for k, v in sorted(item.callspec.indices.items())) + "]"
    return key


def safe_report(report: Any):
    need(type(report) is dict and type(report.get("ok")) is bool)
    stage = report.get("stage")
    if stage == "regression":
        return safe_regression(report)
    need(stage in (*PHASES, "dependencies", "migrate", "principals", "credentials"))
    if stage not in PHASES:
        need(set(report) == {"stage", "ok"})
        return report
    need(set(report) == {"stage", "ok", "collection", "exit", "cases"})
    expected = len(contract()[stage])
    need(type(report["collection"]) is bool)
    exit_code: Any = report["exit"]
    need(type(exit_code) is int and 0 <= exit_code <= 5)
    raw: Any = report["cases"]
    need(type(raw) is list and len(raw) <= expected * 3)
    rows: list[dict[str, Any]] = raw
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"case", "when", "outcome", "xfail"}:
            raise Refused()
        ordinal: Any = row["case"]
        need(type(ordinal) is int and 0 <= ordinal < expected)
        need(row["when"] in ("setup", "call", "teardown"))
        need(row["outcome"] in ("passed", "failed", "skipped"))
        need(type(row["xfail"]) is bool)
        key = (row["case"], row["when"])
        need(key not in seen)
        seen.add(key)
    complete = (
        report["collection"]
        and report["exit"] == 0
        and seen == {(i, when) for i in range(expected) for when in ("setup", "call", "teardown")}
        and all(r["outcome"] == "passed" and not r["xfail"] for r in rows)
    )
    need(report["ok"] == complete)
    return report


EXTERNAL_SKIPS = {
    "backend/tests/storage_real/test_phase_a.py",
    "tests/security/document_list/test_postgres_router.py",
    "tests/security/disconnect_boundary/test_postgres_acceptance.py",
    "tests/security/upload_http/test_live_flow.py",
}
RESEARCH_SKIP = (
    "backend/tests/storage_real/test_stage_b_inventory.py::"
    "test_actual_lock_verified_source_loader_includes_bundled_zero_without_database"
)


def regression_ok(report: Any):
    collected: list[str] = report["collected"]
    rows: list[dict[str, Any]] = report["cases"]
    if not report["collection"] or report["exit"] != 0 or not collected:
        return False
    for i, name in enumerate(collected):
        actual = [r for r in rows if r["case"] == i]
        if any(r["xfail"] for r in actual):
            return False
        skip = name.split("::")[0] in EXTERNAL_SKIPS or name == RESEARCH_SKIP
        wanted = (
            (
                [("setup", "passed"), ("call", "skipped"), ("teardown", "passed")]
                if name == RESEARCH_SKIP
                else [("setup", "skipped"), ("teardown", "passed")]
            )
            if skip
            else [("setup", "passed"), ("call", "passed"), ("teardown", "passed")]
        )
        if [(r["when"], r["outcome"]) for r in actual] != wanted:
            return False
    return True


def safe_regression(report: Any):
    need(set(report) == {"stage", "ok", "collection", "exit", "cases", "collected", "excluded"})
    need(type(report["collection"]) is bool and type(report["exit"]) is int)
    need(0 <= report["exit"] <= 5)
    collected: Any = report["collected"]
    need(type(collected) is list and 0 < len(collected) < 10000)
    need(all(type(c) is str and len(c) < 700 for c in collected))
    need(len(set(collected)) == len(collected))
    files = json.loads(Path(__file__).with_name("regression_files.json").read_text())
    need(all(c.split("::")[0] in files for c in collected))
    need(collected == json.loads(Path(__file__).with_name("regression_cases.json").read_text()))
    need(
        report["excluded"]
        == json.loads(Path(__file__).with_name("node_exclusions.json").read_text())
    )
    raw: Any = report["cases"]
    need(type(raw) is list and len(raw) <= 3 * len(collected))
    rows: list[dict[str, Any]] = raw
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"case", "when", "outcome", "xfail"}:
            raise Refused()
        ordinal: Any = row["case"]
        need(type(ordinal) is int and 0 <= ordinal < len(collected))
        need(row["when"] in ("setup", "call", "teardown"))
        need(row["outcome"] in ("passed", "failed", "skipped") and type(row["xfail"]) is bool)
        key = (row["case"], row["when"])
        need(key not in seen)
        seen.add(key)
    need(report["ok"] == regression_ok(report))
    return report
