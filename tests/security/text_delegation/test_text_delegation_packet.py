"""Lightweight fake-process packet guards only: no Docker, database, network or SSH."""

import ast
import contextlib
import copy
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import operator_packet as operator
import prepare
import protocol
import pytest
import receive
import run_packet

SHA = "b" * 40
ID = "a" * 64


@pytest.fixture(autouse=True)
def no_external_commands(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("real subprocess forbidden in packet unit tests")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def successful(stage="delegation"):
    report = {
        "stage": stage,
        "ok": True,
        "collection": True,
        "exit": 0,
        "cases": [
            {"case": i, "when": when, "outcome": "passed", "xfail": False}
            for i in range(len(protocol.contract()[stage]))
            for when in ("setup", "call", "teardown")
        ],
    }
    if stage == "concurrency":
        from concurrency import empty_diagnostic

        diagnostic = empty_diagnostic()
        diagnostic.update(
            failure="none",
            pid_count=2,
            distinct_pid_count=2,
            grant_count=1,
            fence_matches=True,
            final_executable=True,
        )
        for i, row in enumerate(diagnostic["contenders"]):
            for key, value in row.items():
                if type(value) is bool:
                    row[key] = True
            row.update(
                outcome="grant" if i == 0 else "idle", failure_stage="none", transaction="committed"
            )
        report["diagnostic"] = diagnostic
    return report


@pytest.mark.parametrize("stage", protocol.PHASES)
def test_reviewed_cases_accept_only_complete_three_phase_success(stage):
    assert protocol.safe_report(successful(stage))["ok"]


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "skip", "xfail", "exit", "collection", "canary"]
)
def test_closed_report_refuses_incomplete_forged_success(change):
    report = successful()
    if change == "missing":
        report["cases"].pop()
    if change == "duplicate":
        report["cases"][-1] = report["cases"][0]
    if change == "skip":
        report["cases"][0]["outcome"] = "skipped"
    if change == "xfail":
        report["cases"][0]["xfail"] = True
    if change == "exit":
        report["exit"] = 1
    if change == "collection":
        report["collection"] = False
    if change == "canary":
        report["exception"] = "BODY_CANARY"
    with pytest.raises(protocol.Refused):
        protocol.safe_report(report)


def test_negative_report_is_not_a_pass():
    report = successful()
    report["ok"] = False
    report["cases"][0]["outcome"] = "failed"
    assert not protocol.safe_report(report)["ok"]


def test_regression_requires_explicit_external_skip_and_no_hidden_db_skips():
    names = [
        "backend/tests/auth/test_documents.py::test_document_tables_are_rls_protected",
        "backend/tests/storage_real/test_phase_a.py::test_real_upstream_acl_boundary",
        protocol.RESEARCH_SKIP,
    ]
    report = {
        "collection": True,
        "exit": 0,
        "collected": names,
        "cases": [
            {"case": i, "when": when, "outcome": outcome, "xfail": False}
            for i, pairs in enumerate(
                [
                    [("setup", "passed"), ("call", "passed"), ("teardown", "passed")],
                    [("setup", "skipped"), ("teardown", "passed")],
                    [("setup", "passed"), ("call", "skipped"), ("teardown", "passed")],
                ]
            )
            for when, outcome in pairs
        ],
    }
    assert protocol.regression_ok(report)
    report["cases"][1]["outcome"] = "skipped"
    assert not protocol.regression_ok(report)


def test_node_exclusions_are_exact_reviewed_functions_and_parameter_values():
    root = Path(__file__).resolve().parents[3]
    tree = ast.parse((root / "backend/tests/storage_real/test_stage_b_wrapper.py").read_text())
    values = next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "NEGATIVES"
    )
    expected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            name = "backend/tests/storage_real/test_stage_b_wrapper.py::" + node.name
            expected.extend(
                [name + "[" + v + "]" for v in values]
                if node.name.endswith("before_any_child_start")
                else [name]
            )
    assert json.loads(Path(__file__).with_name("node_exclusions.json").read_text()) == expected


def test_static_cases_are_bound_to_source_function_and_parameter_indices():
    root = Path(__file__).resolve().parents[3]
    for expected in protocol.contract().values():
        actual = []
        for filename in dict.fromkeys(c.split("::")[0] for c in expected):
            import itertools

            body = ast.parse((root / filename).read_text()).body
            fixtures = {}
            for fixture in body:
                if isinstance(fixture, ast.FunctionDef):
                    for dec in fixture.decorator_list:
                        if (
                            isinstance(dec, ast.Call)
                            and isinstance(dec.func, ast.Attribute)
                            and dec.func.attr == "fixture"
                        ):
                            for keyword in dec.keywords:
                                if keyword.arg == "params":
                                    assert isinstance(keyword.value, (ast.List, ast.Tuple))
                                    fixtures[fixture.name] = len(keyword.value.elts)
            for node in body:
                if not isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) or not node.name.startswith("test_"):
                    continue
                axes = []
                for dec in reversed(node.decorator_list):
                    if (
                        isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "parametrize"
                    ):
                        assert not dec.keywords
                        names = ast.literal_eval(dec.args[0]).split(",")
                        values = dec.args[1]
                        assert isinstance(values, (ast.List, ast.Tuple))
                        axes.append([{n: i for n in names} for i in range(len(values.elts))])
                for arg in node.args.args:
                    if arg.arg in fixtures:
                        axes.append([{arg.arg: i} for i in range(fixtures[arg.arg])])
                for combined_index, combo in enumerate(itertools.product(*axes)):
                    # Pinned pytest9.2 normalizes all axes to the combined case index.
                    indices = {k: combined_index for axis in combo for k in axis}
                    suffix = (
                        "[" + ",".join(f"{k}={v}" for k, v in sorted(indices.items())) + "]"
                        if indices
                        else ""
                    )
                    actual.append(filename + "::" + node.name + suffix)
        assert actual == expected


def test_environment_scrubs_docker_context_credentials_and_production_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_CONTEXT", "foreign")
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "CANARY")
    env = operator.docker_environment(tmp_path)
    assert env["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert env["DOCKER_CONFIG"] == str(tmp_path)
    assert not {"DOCKER_CONTEXT", "BWS_ACCESS_TOKEN", "PGPASSWORD"} & env.keys()
    assert all("CANARY" not in v for v in run_packet.environment().values())


def test_host_guard_requires_real_and_effective_uid_1001(monkeypatch):
    monkeypatch.setattr(operator.socket, "gethostname", lambda: "kube1")
    monkeypatch.setattr(operator.os, "getuid", lambda: 1001)
    monkeypatch.setattr(operator.os, "geteuid", lambda: 0)
    with pytest.raises(protocol.Refused):
        operator.host_guard()


def test_retained_inode_nonblocking_lock_refuses_second_owner_and_symlink(monkeypatch, tmp_path):
    monkeypatch.setattr(operator, "BASE", tmp_path)
    original = operator.os.fstat

    def owned(fd):
        meta = original(fd)
        values = list(meta)
        values[4] = 1001
        return os.stat_result(values)

    monkeypatch.setattr(operator.os, "fstat", owned)
    with operator.namespace_lock():
        inode = (tmp_path / ".operator.lock").stat().st_ino
        with pytest.raises(BlockingIOError), operator.namespace_lock():
            pass
    assert (tmp_path / ".operator.lock").stat().st_ino == inode
    with operator.namespace_lock():
        pass
    (tmp_path / ".operator.lock").unlink()
    (tmp_path / ".operator.lock").symlink_to(tmp_path / "foreign")
    with pytest.raises(OSError), operator.namespace_lock():
        pass


def test_exact_id_cleanup_checks_ownership_never_namespace_down(monkeypatch):
    state = {"container": [ID], "network": [], "volume": []}
    monkeypatch.setattr(operator, "inventory", lambda env: copy.deepcopy(state))
    ownership = Mock()
    monkeypatch.setattr(operator, "owned", ownership)

    def command(args, *unused):
        assert args == ["docker", "container", "rm", "--force", ID]
        ownership.assert_called_once_with("container", ID, SHA, {})
        state["container"] = []

    monkeypatch.setattr(operator, "command", command)
    rows = []
    assert operator.cleanup({}, SHA, rows.append)
    assert rows[-1]["namespace"] == [0, 0, 0]


def test_unknown_owner_is_not_deleted_or_reported_clean(monkeypatch):
    monkeypatch.setattr(
        operator, "inventory", lambda env: {"container": [ID], "network": [], "volume": []}
    )
    monkeypatch.setattr(operator, "owned", Mock(side_effect=protocol.Refused))
    cmd = Mock()
    monkeypatch.setattr(operator, "command", cmd)
    assert not operator.cleanup({}, SHA, lambda row: None)
    cmd.assert_not_called()


def test_namespace_busy_refuses_before_capacity_mutation(monkeypatch):
    monkeypatch.setattr(operator, "inventory", lambda env: {"container": [ID]})
    with pytest.raises(protocol.Refused):
        operator.capacity({})


def test_private_files_are_exclusive_mode0600(tmp_path):
    path = tmp_path / "secret"
    run_packet.private_file(path, "synthetic")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        run_packet.private_file(path, "replacement")


def test_compose_has_pinned_nonroot_bounded_services_and_no_secret_mounts():
    compose = json.loads(Path(__file__).with_name("compose.json").read_text())
    assert set(compose["services"]) == {"db", "runner"}
    assert compose["networks"]["database"]["internal"]
    assert "volumes" not in compose and "secrets" not in compose
    for service in compose["services"].values():
        assert "@sha256:" in service["image"] and service["user"] != "0:0"
        assert service["read_only"] and service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["cpus"] == 1 and service["mem_limit"] and service["pids_limit"] == 128
        assert not {"ports", "privileged", "build", "secrets"} & service.keys()
        assert service["tmpfs"] and all("size=" in mount for mount in service["tmpfs"])
        assert all("docker.sock" not in mount for mount in service.get("volumes", []))
    assert compose["services"]["runner"]["networks"] == ["dependencies"]
    assert "scram-sha-256" in compose["services"]["db"]["environment"]["POSTGRES_INITDB_ARGS"]


def test_prepare_only_git_archive_not_worktree(monkeypatch, tmp_path, capsys):
    payload = io.BytesIO()
    required = [
        str(protocol.RELATIVE / "operator_packet.py"),
        str(protocol.RELATIVE / "protocol.py"),
        str(protocol.RELATIVE / "receive.py"),
        str(protocol.RELATIVE / "cases.json"),
        "backend/migrations/versions/0007_text_dispatch.sql",
        "backend/app/workers/delegation.py",
        "backend/tests/documents/test_delegation_database.py",
        "backend/tests/auth/test_text_delegation_migration.py",
    ]
    with tarfile.open(fileobj=payload, mode="w") as archive:
        for name in required:
            member = tarfile.TarInfo(name)
            member.mode = 0o644
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))

    def git(args, **kwargs):
        assert args in (
            ["git", "rev-parse", "--verify", SHA + "^{commit}"],
            ["git", "-c", "tar.umask=0022", "archive", "--format=tar", SHA],
        )
        return SimpleNamespace(
            stdout=SHA.encode() if args[1] == "rev-parse" else payload.getvalue()
        )

    monkeypatch.setattr(subprocess, "run", git)
    prepare.prepare(SHA, tmp_path / "packet")
    anchors = json.loads(capsys.readouterr().out)
    for key, name in (
        ("archive_sha256", "source.tar"),
        ("manifest_sha256", "source-manifest.json"),
        ("receiver_sha256", "receive.py"),
    ):
        assert anchors[key] == hashlib.sha256((tmp_path / "packet" / name).read_bytes()).hexdigest()
    manifest = json.loads((tmp_path / "packet/source-manifest.json").read_text())
    assert manifest["sha"] == SHA and set(manifest["files"]) == set(required)
    assert manifest["files"][required[0]]["sha256"] == hashlib.sha256(b"x").hexdigest()
    with pytest.raises(FileExistsError):
        prepare.prepare(SHA, tmp_path / "packet")


def test_old_base_and_short_sha_cannot_prepare(tmp_path):
    for sha in ("ac817f8" + "0" * 33, "abcdef"):
        with pytest.raises(protocol.Refused):
            prepare.prepare(sha, tmp_path / "packet")


@pytest.mark.parametrize("mode", ["nonzero", "oversize", "timeout", "success"])
def test_command_bounded_output_reaps_fake_process_and_never_discloses_raw_bytes(
    monkeypatch, mode, capsys
):
    reader, writer = os.pipe()
    os.write(writer, b"BODY_CANARY")
    os.close(writer)
    output = os.fdopen(reader, "rb")
    child = SimpleNamespace(
        pid=12345, stdout=output, stdin=None, wait=Mock(return_value=1 if mode == "nonzero" else 0)
    )
    popen = Mock(return_value=child)
    monkeypatch.setattr(subprocess, "Popen", popen)
    kill = Mock()
    monkeypatch.setattr(operator.os, "killpg", kill)
    if mode == "timeout":
        monkeypatch.setattr(operator.time, "monotonic", Mock(side_effect=[0, 2]))
    if mode == "success":
        assert operator.command(["fake"], {"PATH": "/fixed"}, 1) == b"BODY_CANARY"
    else:
        with pytest.raises(protocol.Refused):
            operator.command(
                ["fake"], {"PATH": "/fixed"}, 1, limit=2 if mode == "oversize" else 100
            )
    kill.assert_called_once()
    child.wait.assert_called()
    assert output.closed
    assert "BODY_CANARY" not in capsys.readouterr().out
    assert popen.call_args.kwargs["env"] == {"PATH": "/fixed"}


def test_existing_evidence_refused_before_preflight_or_mutation(monkeypatch, tmp_path):
    root = tmp_path / SHA
    (root / protocol.RELATIVE).mkdir(parents=True)
    (tmp_path / (".runtime-" + SHA)).mkdir()
    monkeypatch.setattr(operator, "BASE", tmp_path)
    monkeypatch.setattr(operator, "__file__", str(root / protocol.RELATIVE / "operator_packet.py"))
    monkeypatch.setattr(operator, "host_guard", lambda: None)
    monkeypatch.setattr(operator, "namespace_lock", contextlib.nullcontext)
    monkeypatch.setattr(operator, "verify_source", lambda *args: 10)
    capacity = Mock()
    monkeypatch.setattr(operator, "capacity", capacity)
    with pytest.raises(protocol.Refused):
        operator.execute(SHA, "c" * 64, "run")
    capacity.assert_not_called()


def test_scram_credentials_are_passfile_only_and_wildcard_database_not_host_or_role(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(run_packet, "PRIVATE", tmp_path)
    monkeypatch.setattr(
        run_packet.sys, "stdin", io.StringIO(json.dumps({"admin": "a" * 48, "app": "b" * 48}))
    )
    run_packet.credentials()
    assert (tmp_path / "database.pgpass").read_text().splitlines() == [
        "db:5432:*:postgres:" + "a" * 48,
        "db:5432:*:notebook_app:" + "b" * 48,
    ]
    assert stat.S_IMODE((tmp_path / "database.pgpass").stat().st_mode) == 0o600
    assert "PGPASSWORD" not in run_packet.environment()


def test_regression_checked_collection_matches_file_inventory():
    here = Path(__file__).parent
    files = set(json.loads((here / "regression_files.json").read_text()))
    selected = json.loads((here / "regression_cases.json").read_text())
    assert len(set(selected)) == len(selected)
    assert {n.split("::")[0] for n in selected} == files - {
        "backend/tests/storage_real/test_stage_b_wrapper.py"
    }


@pytest.mark.parametrize(
    "fault", ["none", "hash", "mode", "extra", "symlink", "hardlink", "anchor"]
)
def test_manifest_anchored_full_source_and_modes_fail_closed(monkeypatch, tmp_path, fault):
    def synthetic_uid(original):
        def patched(path, *args, **kwargs):
            meta = original(path, *args, **kwargs)
            values = list(meta)
            values[4] = 1001
            return os.stat_result(values)

        return patched

    monkeypatch.setattr(Path, "lstat", synthetic_uid(Path.lstat))
    root = tmp_path / SHA
    root.mkdir(mode=0o700)
    files = {}
    for name in (
        "backend/app/workers/delegation.py",
        "backend/migrations/versions/0007_text_dispatch.sql",
        "backend/tests/auth/test_text_delegation_migration.py",
        str(protocol.RELATIVE / "cases.json"),
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source")
        path.chmod(0o644)
        files[name] = {"mode": 0o644, "sha256": hashlib.sha256(b"source").hexdigest()}
    manifest = root / ".source-manifest.json"
    manifest.write_text(json.dumps({"sha": SHA, "files": files}))
    manifest.chmod(0o600)
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    path = root / "backend/app/workers/delegation.py"
    if fault == "hash":
        path.write_text("modified")
    if fault == "mode":
        path.chmod(0o600)
    if fault == "extra":
        (root / "unreviewed").touch()
    if fault == "symlink":
        path.unlink()
        path.symlink_to(manifest)
    if fault == "hardlink":
        os.link(path, root / "hardlink")
    if fault == "anchor":
        digest = "0" * 64
    if fault == "none":
        assert operator.verify_source(root, SHA, digest) == len(files)
    else:
        with pytest.raises(protocol.Refused):
            operator.verify_source(root, SHA, digest)


@pytest.mark.parametrize("fault", ["none", "empty", "exit", "skip", "xfail", "setup", "teardown"])
def test_runner_reporter_does_not_accept_exit0_without_exact_collection_and_three_phases(
    monkeypatch, fault
):
    name = protocol.contract()["migration"][0]
    item = SimpleNamespace(nodeid=name, originalname=name.split("::")[1])

    def fake_main(args, plugins):
        reporter = plugins[0]
        items = [] if fault == "empty" else [item]
        reporter.pytest_collection_modifyitems(None, None, items)
        for when in ("setup", "call", "teardown"):
            if fault == when:
                continue
            report = SimpleNamespace(
                when=when, outcome="skipped" if fault == "skip" and when == "call" else "passed"
            )
            if fault == "xfail":
                report.wasxfail = "CANARY"
            hook = reporter.pytest_runtest_makereport(item, None)
            next(hook)
            with pytest.raises(StopIteration):
                hook.send(SimpleNamespace(get_result=lambda current=report: current))
        return 1 if fault == "exit" else 0

    monkeypatch.setattr(pytest, "main", fake_main)
    if fault == "empty":
        with pytest.raises(protocol.Refused):
            run_packet.pytest_report("migration")
    else:
        report = run_packet.pytest_report("migration")
        assert report["ok"] == (fault == "none")


def compose_interpolation_contract(value: Any, env: dict[str, str]) -> Any:
    """Only the packet's $$ / required TD substitutions, NOT a native Compose renderer."""
    if isinstance(value, dict):
        return {k: compose_interpolation_contract(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [compose_interpolation_contract(v, env) for v in value]
    if not isinstance(value, str):
        return value
    pattern = re.compile(r"\$\$|\$\{(TD_SHA|TD_SNAPSHOT):\?[^}]+\}|\$")

    def replace(match):
        if match[0] == "$$":
            return "$"
        assert match[0] != "$", "unescaped/unsupported Compose dollar"
        assert env.get(match[1]), "required Compose variable missing"
        return env[match[1]]

    return pattern.sub(replace, value)


def test_compose_interpolation_contract_preserves_shell_loop_and_required_td_values():
    compose = json.loads(Path(__file__).with_name("compose.json").read_text())
    env = {"TD_SHA": SHA, "TD_SNAPSHOT": "/private/" + SHA}
    rendered = compose_interpolation_contract(compose, env)
    assert rendered["services"]["db"]["entrypoint"] == [
        "/bin/sh",
        "-ec",
        (
            "i=0; while [ ! -f /tmp/bootstrap ]; do i=$((i+1)); [ $i -lt 90 ]; sleep 1; done; "
            "exec /usr/local/bin/docker-entrypoint.sh postgres -c password_encryption=scram-sha-256 "
            "-c log_statement=none -c log_min_error_statement=panic -c log_min_messages=panic"
        ),
    ]
    for item in (*rendered["services"].values(), *rendered["networks"].values()):
        assert item["labels"][operator.LABEL] == SHA
    assert rendered["services"]["runner"]["volumes"] == [env["TD_SNAPSHOT"] + ":/snapshot:ro"]
    with pytest.raises(AssertionError, match="required Compose variable"):
        compose_interpolation_contract(compose, {})


@pytest.mark.parametrize("dollar", ["$((i+1))", "$i", "${i}"])
def test_compose_interpolation_contract_rejects_unescaped_shell_dollars(dollar):
    with pytest.raises(AssertionError, match="unescaped/unsupported"):
        compose_interpolation_contract(dollar, {})


def receiver_artifacts(tmp_path: Path, entries=None) -> tuple[str, str, str, Path, Path, Path]:
    if entries is None:
        entries = [
            (str(protocol.RELATIVE / name), b"raise RuntimeError('MUST_NOT_EXECUTE')", 0o644)
            for name in ("operator_packet.py", "protocol.py", "receive.py")
        ]
    payload = io.BytesIO()
    files = {}
    with tarfile.open(fileobj=payload, mode="w") as archive:
        for name, content, mode in entries:
            member = tarfile.TarInfo(name)
            member.mode, member.size = mode, len(content)
            archive.addfile(member, io.BytesIO(content))
            files[name] = {"mode": mode, "sha256": hashlib.sha256(content).hexdigest()}
    archive_path, manifest_path = tmp_path / "source.tar", tmp_path / "manifest.json"
    archive_path.write_bytes(payload.getvalue())
    manifest_path.write_text(json.dumps({"sha": SHA, "files": files}))
    tmp_path.chmod(0o700)
    return (
        SHA,
        hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        archive_path,
        manifest_path,
        tmp_path / SHA,
    )


@pytest.mark.parametrize("target", ["operator_packet.py", "protocol.py", "archive", "manifest"])
def test_receiver_rejects_transit_tamper_before_tar_parsing_or_extraction(
    monkeypatch, tmp_path, target
):
    args = receiver_artifacts(tmp_path)
    if target in ("operator_packet.py", "protocol.py"):
        # Replace a code member, retaining the parent-authenticated manifest and archive anchor.
        raw = args[3].read_bytes()
        offset = raw.index(target.encode()) + 512
        args[3].write_bytes(raw[:offset] + b"!" + raw[offset + 1 :])
    elif target == "archive":
        args[3].write_bytes(args[3].read_bytes() + b"tamper")
    else:
        args[4].write_bytes(args[4].read_bytes() + b" ")
    parse = Mock(side_effect=AssertionError("must authenticate before parsing"))
    monkeypatch.setattr(receive.tarfile, "open", parse)
    with pytest.raises(ValueError, match="receiver refused"):
        receive.receive(*args)
    parse.assert_not_called()
    assert not args[-1].exists()


@pytest.mark.parametrize("fault", ["path", "mode", "symlink", "hardlink", "duplicate", "hash"])
def test_receiver_validates_all_members_before_creating_snapshot(tmp_path, fault):
    args: Any = list(receiver_artifacts(tmp_path))
    name = str(protocol.RELATIVE / "operator_packet.py")
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for current in (
            name,
            str(protocol.RELATIVE / "protocol.py"),
            str(protocol.RELATIVE / "receive.py"),
        ):
            member = tarfile.TarInfo(
                "../escape" if current == name and fault == "path" else current
            )
            content = b"raise RuntimeError('MUST_NOT_EXECUTE')"
            member.mode = 0o777 if current == name and fault == "mode" else 0o644
            if current == name and fault in ("symlink", "hardlink"):
                member.type = tarfile.SYMTYPE if fault == "symlink" else tarfile.LNKTYPE
                member.linkname = "../escape"
            if current == name and fault == "hash":
                content = b"raise RuntimeError('CHANGED')"
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
            if current == name and fault == "duplicate":
                archive.addfile(member, io.BytesIO(content))
    args[3].write_bytes(raw.getvalue())
    # Even a matching archive anchor cannot bypass the safe-member/manifest checks.
    args[1] = hashlib.sha256(args[3].read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="receiver refused"):
        receive.receive(*args)
    assert not args[-1].exists() and not (tmp_path.parent / "escape").exists()


def test_receiver_verifies_extracted_files_without_executing_received_code(tmp_path):
    args = receiver_artifacts(tmp_path)
    assert receive.receive(*args) == 3
    assert stat.S_IMODE(args[-1].stat().st_mode) == 0o700
    assert stat.S_IMODE((args[-1] / ".source-manifest.json").stat().st_mode) == 0o600
    for name in ("operator_packet.py", "protocol.py", "receive.py"):
        path = args[-1] / protocol.RELATIVE / name
        assert path.read_bytes() == b"raise RuntimeError('MUST_NOT_EXECUTE')"
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
    with pytest.raises(FileExistsError):
        receive.receive(*args)


def test_receiver_rejects_extracted_file_corruption_before_success(monkeypatch, tmp_path):
    args = receiver_artifacts(tmp_path)
    original = Path.chmod

    def corrupt(path, mode, *extra, **kwargs):
        original(path, mode, *extra, **kwargs)
        if path.name == "protocol.py":
            path.write_bytes(b"changed on disk")

    monkeypatch.setattr(Path, "chmod", corrupt)
    with pytest.raises(ValueError, match="receiver refused"):
        receive.receive(*args)


@pytest.mark.parametrize("fail_at", [0, 1, 2, 3])
def test_cleanup_evidence_failure_still_removes_every_owned_id_and_fails(monkeypatch, fail_at):
    state = {"container": [ID, "b" * 64], "network": ["c" * 64], "volume": []}
    monkeypatch.setattr(operator, "inventory", lambda env: copy.deepcopy(state))
    checked, removed, rows = [], [], []
    monkeypatch.setattr(operator, "owned", lambda kind, ident, *args: checked.append((kind, ident)))

    def command(args, *unused):
        kind, ident = args[1], args[-1]
        assert (kind, ident) in checked
        removed.append((kind, ident))
        state[kind].remove(ident)

    def emit(row):
        rows.append(row)
        if len(rows) - 1 == fail_at:
            raise OSError("synthetic disk failure")

    monkeypatch.setattr(operator, "command", command)
    assert not operator.cleanup({}, SHA, emit)
    assert len(removed) == 3 and all(not ids for ids in state.values())
    assert rows[-1]["stage"] == "cleanup"


@pytest.mark.parametrize("fault", ["cleanup", "unlink", "rmtree", "emit"])
def test_cleanup_runtime_independently_attempts_host_credential_and_config_deletion(
    monkeypatch, tmp_path, fault
):
    secret, config = tmp_path / "synthetic.json", tmp_path / "docker"
    secret.write_text("synthetic")
    config.mkdir()
    cleanup = Mock(side_effect=OSError if fault == "cleanup" else None, return_value=True)
    monkeypatch.setattr(operator, "cleanup", cleanup)
    unlink = Mock()
    original_unlink, original_rmtree = Path.unlink, operator.shutil.rmtree

    def remove_secret(path, **kwargs):
        unlink(path, **kwargs)
        if fault == "unlink":
            raise OSError("synthetic unlink failure")
        original_unlink(path, **kwargs)

    monkeypatch.setattr(Path, "unlink", remove_secret)
    rmtree = Mock(side_effect=OSError if fault == "rmtree" else original_rmtree)
    monkeypatch.setattr(operator.shutil, "rmtree", rmtree)
    emit = operator.RememberingEmitter(Mock(side_effect=OSError if fault == "emit" else None))
    assert not operator.cleanup_runtime({}, SHA, emit, secret, config)
    cleanup.assert_called_once()
    unlink.assert_called_once_with(secret, missing_ok=True)
    rmtree.assert_called_once_with(config)
    assert secret.exists() == (fault == "unlink")
    assert config.exists() == (fault == "rmtree")


@pytest.mark.parametrize(
    "failure_stage", ["none", "open", "source", "cleanup", "host-credentials", "complete"]
)
def test_operator_evidence_failure_cannot_accept_or_leak_host_material(
    monkeypatch, tmp_path, failure_stage, report_fault=None
):
    root = tmp_path / SHA
    (root / protocol.RELATIVE).mkdir(parents=True)
    monkeypatch.setattr(operator, "BASE", tmp_path)
    monkeypatch.setattr(operator, "__file__", str(root / protocol.RELATIVE / "operator_packet.py"))
    monkeypatch.setattr(operator, "host_guard", lambda: None)
    lock_held = False

    @contextlib.contextmanager
    def lock():
        nonlocal lock_held
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    monkeypatch.setattr(operator, "namespace_lock", lock)
    monkeypatch.setattr(operator, "verify_source", lambda *args: 10)
    monkeypatch.setattr(operator, "capacity", lambda env: {"stage": "preflight", "ok": True})
    monkeypatch.setattr(operator, "attest_container", lambda *args: None)
    monkeypatch.setattr(operator, "safe_report", lambda report: report)
    db, runner, internal, external = [char * 64 for char in "abcd"]
    state = {"container": [], "network": [], "volume": []}
    monkeypatch.setattr(operator, "inventory", lambda env: copy.deepcopy(state))
    monkeypatch.setattr(
        operator, "own_service", lambda _, service, *args: db if service == "db" else runner
    )
    checked = []
    monkeypatch.setattr(operator, "owned", lambda kind, ident, *args: checked.append((kind, ident)))
    original_dumps = json.dumps
    failures = []
    original_open = Path.open

    def open_evidence(path, *args, **kwargs):
        if path.name == "evidence.jsonl" and failure_stage == "open":
            failures.append("open")
            raise OSError("synthetic evidence open failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_evidence)

    def dumps(value, *args, **kwargs):
        if isinstance(value, dict) and "time" in value and value["stage"] == failure_stage:
            failures.append(value["stage"])
            raise OSError("synthetic evidence failure")
        return original_dumps(value, *args, **kwargs)

    monkeypatch.setattr(operator.json, "dumps", dumps)

    def command(args, env, *unused, **kwargs):
        assert lock_held
        if "create" in args:
            state.update(container=[db, runner], network=[internal, external])
        if "rm" in args:
            kind, ident = args[1], args[-1]
            assert (kind, ident) in checked
            state[kind].remove(ident)
        if "{{.Name}}" in args:
            return (
                operator.PROJECT + ("_database" if args[-1] == internal else "_dependencies")
            ).encode()
        if "{{.Internal}}" in args:
            return b"true"
        if "{{json .NetworkSettings.Networks}}" in args:
            return original_dumps({operator.PROJECT + "_database": {}}).encode()
        if "{{.State.Running}}|{{.State.OOMKilled}}|{{.State.Error}}" in args:
            return b"true|false|"
        if args[-1] == "dependencies":
            if report_fault == "dependencies":
                return b'{"stage":"dependencies","ok":false,"ok":true}'
            return b'{"stage":"dependencies","ok":true}'
        if args[-1] == "run":
            return b"\n".join(
                (
                    b'{"stage":"concurrency","ok":true,"diagnostic":{"version":2,"version":1}}'
                    if report_fault == "diagnostic" and stage == "concurrency"
                    else original_dumps({"stage": stage, "ok": True}).encode()
                )
                for stage in operator.EXPECTED_STAGES
            )
        return b""

    monkeypatch.setattr(operator, "command", command)
    code = operator.execute(SHA, "e" * 64, "run")
    assert code == (0 if failure_stage == "none" and report_fault is None else 1)
    assert bool(failures) == (failure_stage != "none")
    assert all(not ids for ids in state.values())
    runtime = tmp_path / (".runtime-" + SHA)
    assert not (runtime / "synthetic.json").exists() and not (runtime / "docker").exists()
    assert root.exists() and not lock_held
    assert (runtime / "evidence.jsonl").exists() == (failure_stage != "open")


@pytest.mark.parametrize("report_fault", ["dependencies", "diagnostic"])
def test_operator_report_ingress_rejects_duplicate_json_keys(monkeypatch, tmp_path, report_fault):
    test_operator_evidence_failure_cannot_accept_or_leak_host_material(
        monkeypatch, tmp_path, "none", report_fault=report_fault
    )
