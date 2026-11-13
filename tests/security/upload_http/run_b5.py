"""Closed live-case reporter. Imports no application/DB code until explicitly invoked."""

import os
import sys
from pathlib import Path

NODE = "tests/security/upload_http/test_live_flow.py::test_real_root_http_upload_to_ready"
STAGES = ("configuration", "principals", "setup", "closed", "denials", "owner", "editor", "done")
ENV = {
    "PATH": "/workspace/.venv/bin:/usr/local/bin:/usr/bin:/bin",
    "PYTHONPATH": "/workspace/backend",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    "LC_ALL": "C.UTF-8",
    "B5_UPLOAD_HTTP": "isolated-stage-b-approved",
}


class Proof:
    """Accept only literal print fragments, never inspect report/capture/exception text."""

    def __init__(self, output):
        self.output = output
        self.stages = []
        self.hooks = []
        self.collected = 0
        self.session_exit = None
        self.bad = False
        self.pending = None

    def write(self, value):
        # print(stage) writes the literal and then newline separately. Arbitrary text
        # is never accumulated, copied to output or attached to an exception.
        if type(value) is not str:
            self.bad = True
            return 0
        if self.pending is not None and value == "\n":
            stage = self.pending
            self.pending = None
            if len(self.stages) >= len(STAGES) or stage != STAGES[len(self.stages)]:
                self.bad = True
            else:
                self.stages.append(stage)
                self.output.write("B5_PACKET_STAGE=" + stage + "\n")
                self.output.flush()
        elif self.pending is None and value in tuple("B5_HTTP_STAGE=" + s for s in STAGES):
            self.pending = STAGES[tuple("B5_HTTP_STAGE=" + s for s in STAGES).index(value)]
        else:
            self.bad = True
            self.pending = None
        return len(value)

    def flush(self):
        pass

    def isatty(self):
        return False

    def pytest_collection_modifyitems(self, session, config, items):
        self.collected += 1
        if self.collected != 1 or [item.nodeid for item in items] != [NODE]:
            self.bad = True
            items[:] = []

    def pytest_runtest_logreport(self, report):
        expected = ("setup", "call", "teardown")
        if (
            report.nodeid != NODE
            or len(self.hooks) >= len(expected)
            or report.when != expected[len(self.hooks)]
            or report.passed is not True
            or hasattr(report, "wasxfail")
        ):
            self.bad = True
        self.hooks.append(report.when if report.when in expected else "unknown")

    def pytest_sessionfinish(self, session, exitstatus):
        if self.session_exit is not None:
            self.bad = True
        self.session_exit = int(exitstatus)

    def complete(self, exitstatus):
        return (
            not self.bad
            and self.pending is None
            and self.collected == 1
            and self.hooks == ["setup", "call", "teardown"]
            and self.stages == list(STAGES)
            and self.session_exit == 0
            and type(exitstatus) is int
            and exitstatus == 0
        )


def live():
    # Imports/loading of actual fixture occurs only under the fresh closed environment.
    import pytest

    output, error = sys.stdout, sys.stderr
    proof = Proof(output)
    code = 1
    try:
        sys.stdout = sys.stderr = proof
        code = pytest.main(
            [
                "-p",
                "no:terminal",
                "-p",
                "no:cacheprovider",
                "-s",
                "--assert=plain",
                NODE,
            ],
            plugins=[proof],
        )
    except BaseException:  # noqa: BLE001 — no source exceptions or raw report formatting
        proof.bad = True
    finally:
        sys.stdout, sys.stderr = output, error
    if proof.complete(int(code)):
        print("B5_LIVE_CASE_PASSED=1", flush=True)
        return 0
    print("B5_PACKET_FAILURE=live_case", flush=True)
    return 1


def main():
    phase = sys.argv[1:]
    if phase in (["prepare"], ["a5"]):
        mode = "prepare" if phase == ["prepare"] else "verify"
        os.execv(
            sys.executable, [sys.executable, "-u", "backend/tests/storage_real/stage_b.py", mode]
        )
    if phase == ["b5"]:
        # Do not import pytest/application with the original A5 runner environment.
        os.execve(sys.executable, [sys.executable, "-u", str(Path(__file__)), "pytest"], ENV)
    if phase == ["pytest"] and dict(os.environ) == ENV:
        return live()
    return 2


if __name__ == "__main__":
    try:
        result = main()
    except BaseException:  # noqa: BLE001 — fixed source-safe process boundary
        result = 2
        print("B5_PACKET_FAILURE=entrypoint", flush=True)
    raise SystemExit(result)
