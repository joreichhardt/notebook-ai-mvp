"""Local-only combined packet guards; no Docker, DB, install or live invocation."""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

BASE = Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location("b5_packet_" + name, BASE / (name + ".py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_reporter_requires_exact_case_hooks_exit_and_ordered_stages():
    packet = load("run_b5")
    output = io.StringIO()
    proof = packet.Proof(output)
    proof.pytest_collection_modifyitems(None, None, [SimpleNamespace(nodeid=packet.NODE)])
    for stage in (
        "configuration",
        "principals",
        "setup",
        "closed",
        "denials",
        "owner",
        "editor",
        "done",
    ):
        proof.write("B5_HTTP_STAGE=" + stage)
        proof.write("\n")
    for when in ("setup", "call", "teardown"):
        proof.pytest_runtest_logreport(SimpleNamespace(nodeid=packet.NODE, when=when, passed=True))
    proof.pytest_sessionfinish(None, 0)
    assert proof.complete(0)
    assert output.getvalue().splitlines()[-1] == "B5_PACKET_STAGE=done"
    assert not proof.complete(5)


def test_full_source_manifest_is_external_anchored_and_never_self_hashes(tmp_path):
    packet = load("operator_b5")
    source = tmp_path / "source.py"
    source.write_text("synthetic source\n")
    source.chmod(0o644)
    manifest = tmp_path / ".source-manifest.json"
    manifest.write_text(json.dumps({"source.py": hashlib.sha256(source.read_bytes()).hexdigest()}))
    manifest.chmod(0o600)
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert packet.verify_source(tmp_path, digest) == 1
    source.write_text("BODY_CANARY")
    with pytest.raises(packet.Refused):
        packet.verify_source(tmp_path, digest)
    manifest.write_text(json.dumps({"source.py": hashlib.sha256(source.read_bytes()).hexdigest()}))
    with pytest.raises(packet.Refused):
        packet.verify_source(tmp_path, digest)


def successful_proof(packet):
    output = io.StringIO()
    proof = packet.Proof(output)
    proof.pytest_collection_modifyitems(None, None, [SimpleNamespace(nodeid=packet.NODE)])
    for stage in packet.STAGES:
        proof.write("B5_HTTP_STAGE=" + stage)
        proof.write("\n")
    return proof, output


@pytest.mark.parametrize(
    "bad", ["skip", "failed", "xfail", "duplicate", "wrongnode", "wrongorder", "no_teardown"]
)
def test_reporter_rejects_missing_duplicate_skipped_xfail_and_foreign_cases(bad):
    packet = load("run_b5")
    proof, _ = successful_proof(packet)
    hooks = ["setup", "call", "teardown"]
    if bad == "wrongorder":
        hooks = ["call", "setup", "teardown"]
    if bad == "no_teardown":
        hooks = ["setup", "call"]
    if bad == "duplicate":
        hooks += ["call"]
    for when in hooks:
        fields = {"nodeid": packet.NODE, "when": when, "passed": True}
        if bad == "wrongnode":
            fields["nodeid"] = "BODY_CANARY"
        if when == "call" and bad in ("skip", "failed"):
            fields["passed"] = False
        if when == "call" and bad == "xfail":
            fields["wasxfail"] = "BODY_CANARY"
        proof.pytest_runtest_logreport(SimpleNamespace(**fields))
    proof.pytest_sessionfinish(None, 0)
    assert not proof.complete(0)


@pytest.mark.parametrize("nodes", [[], ["BODY_CANARY"], ["right", "right"]])
def test_wrong_collection_never_executes_items(nodes):
    packet = load("run_b5")
    proof = packet.Proof(io.StringIO())
    items = [SimpleNamespace(nodeid=packet.NODE if n == "right" else n) for n in nodes]
    proof.pytest_collection_modifyitems(None, None, items)
    assert items == [] and not proof.complete(0)


@pytest.mark.parametrize(
    "text",
    [
        "BODY_CANARY",
        "B5_HTTP_STAGE=done\n",
        "B5_HTTP_STAGE=owner",
        "B5_HTTP_STAGE=configuration\nBODY_CANARY",
    ],
)
def test_raw_unknown_or_out_of_order_stage_text_is_never_emitted(text):
    packet = load("run_b5")
    output = io.StringIO()
    proof = packet.Proof(output)
    proof.write(text)
    proof.write("\n")
    assert not proof.complete(0)
    assert "BODY_CANARY" not in output.getvalue()


@pytest.mark.parametrize("ending", ["pass", "skip", "fail", "xfail", "teardown"])
def test_actual_pytest_protocol_in_synthetic_tree_never_imports_live_fixture(tmp_path, ending):
    # Same exact node spelling in a temporary synthetic tree; never the real DB test.
    target = tmp_path / "tests/security/upload_http/test_live_flow.py"
    target.parent.mkdir(parents=True)
    source = "import pytest\n"
    if ending == "teardown":
        source += '@pytest.fixture\ndef gate():\n yield\n raise RuntimeError("BODY_CANARY")\n'
    if ending == "xfail":
        source += '@pytest.mark.xfail(reason="BODY_CANARY")\n'
    source += (
        "def test_real_root_http_upload_to_ready("
        + ("gate" if ending == "teardown" else "")
        + "):\n"
    )
    for stage in (
        "configuration",
        "principals",
        "setup",
        "closed",
        "denials",
        "owner",
        "editor",
        "done",
    ):
        source += ' print("B5_HTTP_STAGE=' + stage + '",flush=True)\n'
    if ending == "skip":
        source += ' pytest.skip("BODY_CANARY")\n'
    if ending in ("fail", "xfail"):
        source += ' raise RuntimeError("BODY_CANARY")\n'
    target.write_text(source)
    command = (
        'import importlib.util; s=importlib.util.spec_from_file_location("packet",'
        + repr(str(BASE / "run_b5.py"))
        + '); m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print("RESULT="+str(m.live()))'
    )
    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0 and not result.stderr
    assert "BODY_CANARY" not in result.stdout
    assert ("B5_LIVE_CASE_PASSED=1" in result.stdout) == (ending == "pass")
    assert result.stdout.splitlines()[-1] == ("RESULT=0" if ending == "pass" else "RESULT=1")
    if ending == "pass":
        # Exercise the actual environment-clearing/re-exec entrypoint against ONLY
        # this synthetic tree. PYTEST_ADDOPTS/A5/PG/loader overrides do not survive.
        hostile = {
            **os.environ,
            "PYTEST_ADDOPTS": "--invalid-canary",
            "A5_STORAGE_PHASE_A": "1",
            "PGHOST": "BODY_CANARY",
        }
        rerun = subprocess.run(
            [sys.executable, str(BASE / "run_b5.py"), "b5"],
            cwd=tmp_path,
            env=hostile,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert rerun.returncode == 0 and not rerun.stderr
        assert rerun.stdout.splitlines()[-1] == "B5_LIVE_CASE_PASSED=1"
        assert "BODY_CANARY" not in rerun.stdout


def test_stream_hard_timeout_and_raw_canary_have_no_output(capsys):
    packet = load("operator_b5")
    records = []
    start = time.monotonic()
    with pytest.raises(packet.Refused):
        packet.stream(
            [sys.executable, "-c", "import time;time.sleep(2)"],
            dict(os.environ),
            0.05,
            records.append,
        )
    assert time.monotonic() - start < 1
    with pytest.raises(packet.Refused):
        packet.stream(
            [sys.executable, "-c", 'print("BODY_CANARY")'], dict(os.environ), 1, records.append
        )
    assert records == [] and capsys.readouterr().out == ""


def test_runner_create_matches_supported_explicit_service_contract(monkeypatch):
    """Compose 2.40.3 create has no --no-deps; explicit runner has no edges."""
    packet = load("operator_b5")
    compose = json.loads((BASE / "compose.b5.json").read_text())
    assert set(compose["services"]) == {"db", "runner", "storage", "gateway"}
    runner = compose["services"]["runner"]
    # Compose selection includes dependencies: guard explicit AND implicit edges.
    for key in ("depends_on", "links", "volumes_from", "extends", "external_links"):
        assert not runner.get(key)
    for key in ("network_mode", "ipc", "pid"):
        assert not runner.get(key, "").startswith("service:")
    assert not runner.get("provider")
    assert runner["networks"] == ["isolated"]
    assert not runner.get("ports")
    assert compose["networks"] == {"isolated": {"internal": True}}
    assert all(not mount.startswith(("db:", "storage:", "gateway:")) for mount in runner["volumes"])
    prefix = ["docker", "compose", "--project-name", packet.PROJECT]
    calls, removed = [], []

    def supported_create(argv, env, seconds):
        calls.append(argv)
        # Exact production argv, not a stub accepting arbitrary flags or services.
        assert argv[: len(prefix)] == prefix
        assert argv[len(prefix) :] == ["create", "runner"]
        assert env == {"B5_RUN_PHASE": "prepare"}
        assert 0 < seconds <= 30
        return 0

    monkeypatch.setattr(packet, "own_id", lambda *args: "a" * 64)
    monkeypatch.setattr(packet, "remove_runner", lambda cid, *args: removed.append(cid))
    monkeypatch.setattr(
        packet,
        "container_state",
        lambda *args: ["exit_0", "oom_false", "running_false", "engine_error_absent"],
    )
    adapter = SimpleNamespace(quiet=supported_create, fixed_run=lambda *args: 0)
    packet.runner_phase(prefix, {}, "prepare", 120, adapter, lambda row: None)
    assert len(calls) == 1 and removed == ["a" * 64]


@pytest.mark.parametrize(
    "change,accepted",
    [
        ({}, True),
        ({"ExitCode": 1}, False),
        ({"OOMKilled": True}, False),
        ({"Running": True}, False),
        ({"Error": "BODY_CANARY"}, False),
        ({"ExitCode": False}, False),
        ({"Unexpected": "BODY_CANARY"}, False),
    ],
)
def test_runner_uses_actual_selected_state_and_closed_checkpoints(monkeypatch, change, accepted):
    from backend.tests.storage_real import stage_b_launch as original

    packet = load("operator_b5")
    container = "a" * 64
    metadata = {
        "ExitCode": 0,
        "OOMKilled": False,
        "Running": False,
        "Health": "absent",
        "Error": "",
    } | change
    records, removed = [], []
    monkeypatch.setattr(packet, "own_id", lambda *args: container)
    monkeypatch.setattr(packet, "remove_runner", lambda cid, *args: removed.append(cid))

    def inspect(args, **kwargs):
        assert args == [
            "docker",
            "inspect",
            "--type",
            "container",
            "--format",
            original.STATE_FORMAT,
            container,
        ]
        return SimpleNamespace(stdout=json.dumps(metadata).encode())

    monkeypatch.setattr(packet.subprocess, "run", inspect)
    adapter = SimpleNamespace(
        quiet=lambda *args: 0,
        fixed_run=lambda *args: 0,
        STATE_FORMAT=original.STATE_FORMAT,
        selected_state=original.selected_state,
    )
    if accepted:
        packet.runner_phase(["docker", "compose"], {}, "prepare", 120, adapter, records.append)
    else:
        with pytest.raises(packet.Refused):
            packet.runner_phase(["docker", "compose"], {}, "prepare", 120, adapter, records.append)
    assert removed == [container]
    assert [r["runner_checkpoint"] for r in records if "runner_checkpoint" in r] == [
        "create",
        "identify",
        "attach",
        "inspect",
        "validate",
        "remove",
    ]
    assert {"runner_phase": "prepare", "runner_cli_zero": True} in records
    assert any("runner_state_ok" in r and r["runner_state_ok"] is accepted for r in records)
    assert "BODY_CANARY" not in json.dumps(records)
    assert ({"operation": "prepare", "exit": 0} in records) is accepted


@pytest.mark.parametrize("failure", ["create", "identify", "attach", "inspect", "remove"])
def test_closed_checkpoint_locates_failure_without_exception_text(monkeypatch, failure):
    packet = load("operator_b5")
    records, removals = [], []

    class HiddenError(Exception):
        def __str__(self):
            raise AssertionError("exception text must never be inspected")

    def step(name, value):
        if name == failure:
            raise HiddenError()
        return value

    monkeypatch.setattr(packet, "own_id", lambda *args: step("identify", "a" * 64))
    monkeypatch.setattr(
        packet,
        "container_state",
        lambda *args: step(
            "inspect", ["exit_0", "oom_false", "running_false", "engine_error_absent"]
        ),
    )

    def remove(*args):
        removals.append(1)
        step("remove", None)

    monkeypatch.setattr(packet, "remove_runner", remove)
    adapter = SimpleNamespace(
        quiet=lambda *args: step("create", 0), fixed_run=lambda *args: step("attach", 0)
    )
    with pytest.raises(HiddenError):
        packet.runner_phase(["docker", "compose"], {}, "prepare", 120, adapter, records.append)
    checkpoints = [r["runner_checkpoint"] for r in records if "runner_checkpoint" in r]
    if failure in ("create", "identify"):
        assert checkpoints[-1] == failure and not removals
    elif failure != "remove":
        assert checkpoints[-2:] == [failure, "remove"] and removals == [1]
    else:
        assert checkpoints[-1] == "remove" and removals == [1]
    assert set().union(*(set(r) for r in records)) <= {
        "runner_phase",
        "runner_checkpoint",
        "runner_cli_zero",
        "runner_state_ok",
        "operation",
        "exit",
    }


def test_actual_container_exit_is_required_even_when_attach_cli_succeeds(monkeypatch):
    packet = load("operator_b5")
    monkeypatch.setattr(packet, "own_id", lambda *args: "a" * 64)
    removed = []
    monkeypatch.setattr(packet, "remove_runner", lambda cid, *args: removed.append(cid))
    monkeypatch.setattr(
        packet,
        "container_state",
        lambda *args: ["exit_1", "oom_false", "running_false", "engine_error_absent"],
        raising=False,
    )
    adapter = SimpleNamespace(quiet=lambda *args: 0, fixed_run=lambda *args: 0)
    with pytest.raises(packet.Refused):
        packet.runner_phase(["docker", "compose"], {}, "a5", 600, adapter, lambda x: None)
    assert removed == ["a" * 64]


def test_outer_interrupt_reaps_owned_child_instead_of_leaving_execution_running(
    tmp_path, monkeypatch
):
    packet = load("operator_b5")

    class Root:
        def resolve(self):
            return self

        def __str__(self):
            return "/home/jre/notebook-tests/b5/upload-http-" + "a" * 40

        def __truediv__(self, name):
            return tmp_path / name

    class Paths:
        def __new__(cls, *args):
            return Path(*args)

        @staticmethod
        def cwd():
            return Root()

    waits = []
    kills = []

    class Child:
        pid = 12345

        def wait(self, timeout):
            waits.append(timeout)
            if len(waits) == 1:
                raise packet.Refused()
            return 1

    monkeypatch.setattr(packet, "Path", Paths)
    monkeypatch.setattr(packet.subprocess, "Popen", lambda *args, **kwargs: Child())
    monkeypatch.setattr(packet.os, "killpg", lambda pid, sig: kills.append((pid, sig)))
    monkeypatch.setattr(packet.signal, "signal", lambda *args: None)
    monkeypatch.setattr(packet.os, "umask", lambda value: 0o022)
    assert packet.outer("a" * 40, "b" * 64) == 2
    assert waits == [2250, 45] and kills == [(12345, packet.signal.SIGTERM)]


def test_timeout_removes_exact_owned_runner_not_just_cli(monkeypatch):
    packet = load("operator_b5")
    container = "a" * 64
    actions = []
    monkeypatch.setattr(packet, "own_id", lambda *args: container)
    monkeypatch.setattr(
        packet, "check_owned", lambda cid, env, service: actions.append(("ownership", cid, service))
    )

    def fail(*args):
        raise packet.Refused()

    def quiet(args, env, seconds):
        actions.append(tuple(args))
        return 0

    adapter = SimpleNamespace(quiet=quiet, fixed_run=fail)
    with pytest.raises(packet.Refused):
        packet.runner_phase(["docker", "compose"], {}, "a5", 600, adapter, lambda x: None)
    assert actions[-2:] == [
        ("ownership", container, "runner"),
        ("docker", "rm", "--force", container),
    ]


def test_cleanup_failure_still_removes_credentials_and_verifies_namespace(tmp_path, monkeypatch):
    packet = load("operator_b5")
    secret = tmp_path / "credentials"
    secret.mkdir()
    (secret / "synthetic").write_text("BODY_CANARY")
    calls = []
    records = []

    def fail(*args):
        raise OSError("BODY_CANARY")

    monkeypatch.setattr(packet, "inventory", lambda env: calls.append("independent") or [0, 0, 0])
    assert not packet.cleanup(["docker", "compose"], {}, secret, fail, records.append)
    assert not secret.exists() and calls == ["independent"]
    assert records == [{"cleanup_exit": 2, "resources": [0, 0, 0], "credentials_removed": True}]


def test_compose_changes_only_scoped_paths_runner_build_and_phase():
    root = BASE.parents[2]
    packet = json.loads((BASE / "compose.b5.json").read_text())
    original = json.loads((root / "backend/tests/storage_real/stage_b.compose.json").read_text())
    assert packet["name"] == "notebook-test-b5-upload"
    assert set(packet["services"]) == {"db", "runner", "storage", "gateway"}
    for service in packet["services"].values():
        assert not set(service) & {"ports", "env_file", "privileged", "network_mode", "devices"}
        assert service["logging"] == {"driver": "none"}
    assert packet["networks"] == {"isolated": {"internal": True}}
    packet["name"] = original["name"]
    for name in ("db", "storage", "gateway"):
        packet["services"][name]["volumes"] = [
            v.replace("../../../backend/tests/storage_real/", "./")
            for v in packet["services"][name]["volumes"]
        ]
    packet["services"]["runner"]["build"]["dockerfile"] = "backend/tests/storage_real/Dockerfile"
    packet["services"]["runner"]["command"] = [
        "python",
        "backend/tests/storage_real/stage_b.py",
        "verify",
    ]
    assert packet == original
    docker = (BASE / "Dockerfile.b5").read_text().splitlines()
    old = (root / "backend/tests/storage_real/Dockerfile").read_text().splitlines()
    assert docker[:7] == old[:7] and docker[8] == old[7]
    assert docker[7] == "COPY tests/security/upload_http ./tests/security/upload_http"
    assert 'dev = ["pytest' in (root / "pyproject.toml").read_text()
    assert "tests" not in (root / ".dockerignore").read_text().splitlines()


@pytest.mark.parametrize(
    "fault", ["extra", "mode", "special", "symlink", "duplicate", "self", "runtime"]
)
def test_manifest_rejects_unlisted_objects_unsafe_modes_links_and_self_entries(tmp_path, fault):
    packet = load("operator_b5")
    source = tmp_path / "source.py"
    source.write_text("synthetic")
    source.chmod(0o644)
    data = {"source.py": hashlib.sha256(source.read_bytes()).hexdigest()}
    manifest = tmp_path / ".source-manifest.json"
    if fault == "extra":
        (tmp_path / "unlisted").write_text("BODY_CANARY")
    if fault == "mode":
        source.chmod(0o666)
    if fault == "special":
        source.chmod(0o4644)
    if fault == "symlink":
        (tmp_path / "unlisted").symlink_to(source)
    if fault == "self":
        data[".source-manifest.json"] = "a" * 64
    if fault == "runtime":
        data[".b5-runtime/file"] = "a" * 64
    raw = (
        json.dumps(data)
        if fault != "duplicate"
        else '{"source.py":"' + data["source.py"] + '","source.py":"' + data["source.py"] + '"}'
    )
    manifest.write_text(raw)
    manifest.chmod(0o600)
    with pytest.raises(packet.Refused):
        packet.verify_source(tmp_path, hashlib.sha256(manifest.read_bytes()).hexdigest())


def test_deadlines_and_exact_accepted_a5_order_are_not_deduplicated():
    import ast

    packet = load("operator_b5")
    root = BASE.parents[2]
    tree = ast.parse((root / "backend/tests/storage_real/stage_b_launch.py").read_text())
    orders = [
        ast.literal_eval(n.comparators[0])
        for n in ast.walk(tree)
        if isinstance(n, ast.Compare)
        and isinstance(n.left, ast.Name)
        and n.left.id == "seen"
        and isinstance(n.comparators[0], ast.List)
    ]
    assert orders == [list(packet.A5_CASES)] and packet.A5_CASES.count("idempotency") == 2
    assert packet.A5_SECONDS == 600 and packet.B5_SECONDS == 240
    assert packet.ACTIVE_SECONDS == 2250 and packet.OUTER_SECONDS == 2400
    assert (
        2250 + 45 + 5 + 45 + 15 < 2400
    )  # hard child wait + term grace + reap + fallback/down + inventory
    own_tree = ast.parse((BASE / "operator_b5.py").read_text())
    uses = {
        n.attr
        for n in ast.walk(own_tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "a5"
    }
    assert uses <= {
        "quiet",
        "fixed_run",
        "credentials",
        "supervise",
        "selected_state",
        "STATE_FORMAT",
    }
    assert "inventory" not in uses and "main" not in uses


def test_foreign_runner_label_never_authorizes_force_removal(monkeypatch):
    packet = load("operator_b5")
    calls = []
    monkeypatch.setattr(
        packet.subprocess,
        "run",
        lambda *args, **kw: SimpleNamespace(stdout=b"foreign-project|runner"),
    )
    with pytest.raises(packet.Refused):
        packet.remove_runner("a" * 64, {}, lambda *args: calls.append(args))
    assert calls == []


def test_closed_reporter_does_not_inspect_exception_or_capture_properties():
    packet = load("run_b5")
    proof, _ = successful_proof(packet)

    class Report:
        when = "setup"
        nodeid = packet.NODE
        passed = True

        def __getattr__(self, name):
            if name == "wasxfail":
                raise AttributeError
            raise AssertionError("capture/error access forbidden")

    for when in ("setup", "call", "teardown"):
        report = Report()
        report.when = when
        proof.pytest_runtest_logreport(report)
    proof.pytest_sessionfinish(None, 0)
    assert proof.complete(0)


def test_accepted_file_manifest_is_complete_and_unchanged():
    root = BASE.parents[2]
    packet = load("operator_b5")
    packet.verify_pins(root)
    pins = json.loads((BASE / "source_manifest.b5.json").read_text())["accepted_files"]
    files = {
        p.relative_to(root).as_posix()
        for p in (root / "backend/tests/storage_real").iterdir()
        if p.is_file() and not p.name.startswith(".")
    }
    assert files <= set(pins)
    assert "tests/security/upload_http/test_live_flow.py" in pins


def test_imports_have_no_operational_io_or_db_imports():
    script = """import importlib.util,sys
blocked=[]
def audit(event,args):
 if event in ('subprocess.Popen','socket.connect','socket.bind','os.system','os.remove','os.mkdir'):
  blocked.append(event);raise RuntimeError('blocked')
sys.addaudithook(audit)
for path in sys.argv[1:]:
 spec=importlib.util.spec_from_file_location('packet',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 if hasattr(module,'accepted'): module.accepted(__import__('pathlib').Path(sys.argv[1]).parents[3])
assert not blocked
assert not any(name in sys.modules for name in ('sqlalchemy','psycopg','app.main'))
print('IMPORT_NO_OPERATIONAL_IO=1')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(BASE / "operator_b5.py"), str(BASE / "run_b5.py")],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert (
        result.returncode == 0
        and result.stdout == "IMPORT_NO_OPERATIONAL_IO=1\n"
        and not result.stderr
    )
