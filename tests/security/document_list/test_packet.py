"""Local no-DB/container packet proofs. Nested reporter probes are synthetic only."""

import copy
import importlib.util
import json
import os
import secrets
import socket
import sqlite3
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
SHA = "b" * 40


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    import psycopg
    import sqlalchemy

    def forbidden(*args, **kwargs):
        pytest.fail("packet attempted DB/network/container subprocess", pytrace=False)

    for target, name in (
        (psycopg, "connect"),
        (psycopg.Connection, "connect"),
        (sqlite3, "connect"),
        (sqlalchemy, "create_engine"),
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket, "create_connection"),
        (subprocess, "run"),
        (subprocess, "Popen"),
    ):
        monkeypatch.setattr(target, name, forbidden)


def success_report(phase):
    count = {"postgres": 7, "focused": 50}[phase]
    return {
        "stage": phase,
        "ok": True,
        "passed": count,
        "failed": 0,
        "skipped": 0,
        "expected": count,
        "cases": [
            {"test": f"{phase}-{n:02}", "phase": "call", "outcome": "passed"}
            for n in range(1, count + 1)
        ],
    }


def test_dedicated_namespace_and_exact_selection():
    operator, runner = load("operator_b4"), load("run_b4")
    assert operator.BASE == Path("/home/jre/notebook-tests/b4")
    assert operator.PROJECT == "notebook-test-b4"
    assert runner.HERE == Path("/snapshot/tests/security/document_list")
    assert runner.PG_TEST == "tests/security/document_list/test_postgres_router.py"
    assert runner.FOCUSED == ["tests/security/document_list/test_router.py"]
    assert runner.EXPECTED == operator.EXPECTED == {"postgres": 7, "focused": 50}


def test_exception_name_is_not_an_output_channel():
    for module in (load("run_b4"), load("operator_b4")):
        assert module.error_kind(type("SyntheticPrivateCanary", (Exception,), {})()) == "Error"
        assert module.error_kind(RuntimeError("SYNTHETIC_PRIVATE")) == "RuntimeError"


@pytest.mark.parametrize("phase", ["postgres", "focused"])
def test_exact_success_report_accepted(phase):
    report = success_report(phase)
    assert load("operator_b4").safe_report(report) is report


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "extra",
        "string-count",
        "bool-count",
        "count",
        "expected",
        "duplicate",
        "name",
        "header",
        "label",
        "error",
        "outcome",
        "skipped",
        "case-missing",
    ],
)
def test_hostile_or_incomplete_report_rejected(fault):
    report = success_report("postgres")
    if fault == "missing":
        report = {"stage": "postgres", "ok": True}
    elif fault == "extra":
        report["sqlstate"] = "SYNTHETIC_PRIVATE"
    elif fault == "string-count":
        report["passed"] = "7"
    elif fault == "bool-count":
        report["failed"] = False
    elif fault == "count":
        report["passed"] = 6
    elif fault == "expected":
        report["expected"] = 8
    elif fault == "duplicate":
        report["cases"][1] = report["cases"][0]
    elif fault == "name":
        report["cases"][0]["test"] = "SYNTHETIC_PRIVATE"
    elif fault == "header":
        report["cases"][0]["headers"] = {"Synthetic": "PRIVATE"}
    elif fault == "label":
        report["cases"][0]["label"] = "B4 private label"
    elif fault == "error":
        report["cases"][0]["error_type"] = "SyntheticPrivateCanary"
    elif fault == "outcome":
        report["cases"][0]["outcome"] = "unknown"
    elif fault == "skipped":
        report["cases"][0]["outcome"] = "skipped"
        report["skipped"], report["passed"] = 1, 6
    elif fault == "case-missing":
        report["cases"].pop()
    with pytest.raises(RuntimeError):
        load("operator_b4").safe_report(report)


@pytest.fixture
def operator_scene(monkeypatch, tmp_path):
    operator = load("operator_b4")
    base = tmp_path / "b4"
    base.mkdir(mode=0o700)
    root = base / SHA
    root.mkdir(mode=0o700)
    (root / "uv.lock").touch()
    monkeypatch.setattr(operator, "BASE", base)
    monkeypatch.setattr(operator, "__file__", str(root / operator.RELATIVE / "operator_b4.py"))
    monkeypatch.setattr(
        operator, "preflight", lambda: {"stage": "preflight", "ok": True, "namespace": [0, 0, 0]}
    )
    monkeypatch.setattr(operator, "namespace", lambda: [0, 0, 0])
    scene = SimpleNamespace(operator=operator, base=base, root=root, calls=[], fault=None)
    reports = [
        {"stage": "migrate", "ok": True},
        {"stage": "role", "ok": True},
        success_report("postgres"),
        success_report("focused"),
        {"stage": "runner-credentials", "ok": True},
    ]

    def command(args, env=None, timeout=30):
        scene.calls.append(args)
        assert env is not None
        assert args[:8] == [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-p",
            "notebook-test-b4",
            "-f",
            str(root / operator.RELATIVE / "compose.b4.yaml"),
        ]
        assert not any(
            key in env
            for key in (
                "BWS_ACCESS_TOKEN",
                "PGPASSWORD",
                "DATABASE_URL",
                "NOTEBOOK_MIGRATION_DATABASE_URL",
                "DOCKER_HOST",
                "COMPOSE_FILE",
            )
        )
        bootstrap = Path(env["B4_BOOTSTRAP_FILE"])
        assert bootstrap.stat().st_mode & 0o777 == 0o444
        assert env["B4_DATABASE"] == "notebook_b4_" + SHA[:12]
        if "run" in args:
            if scene.fault == "timeout":
                raise subprocess.TimeoutExpired(args, 1, output=b"SYNTHETIC_PRIVATE")
            rows = copy.deepcopy(reports)
            if scene.fault == "bad-report":
                rows[2]["cases"][0]["test"] = "SYNTHETIC_PRIVATE"
            elif scene.fault == "missing-report":
                rows.pop()
            output = b"SYNTHETIC_PRIVATE_IGNORED\n" + b"\n".join(
                b"B4_RESULT " + json.dumps(r).encode() for r in rows
            )
            return SimpleNamespace(returncode=int(scene.fault == "runner-exit"), stdout=output)
        return SimpleNamespace(
            returncode=int(
                ("up" in args and scene.fault == "up") or ("down" in args and scene.fault == "down")
            ),
            stdout=b"",
        )

    monkeypatch.setattr(operator, "command", command)
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "SYNTHETIC_PRIVATE_ENV")
    return scene


@pytest.mark.parametrize(
    "fault", [None, "timeout", "up", "down", "runner-exit", "bad-report", "missing-report"]
)
def test_one_shot_run_cleanup_and_no_raw_evidence(operator_scene, capsys, fault):
    scene = operator_scene
    scene.fault = fault
    assert scene.operator.run(SHA) == (0 if fault is None else 1)
    runtime = scene.base / (".runtime-b4-" + SHA)
    evidence = runtime / "evidence.jsonl"
    assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
    assert stat.S_IMODE(evidence.stat().st_mode) == 0o600
    assert not (runtime / "bootstrap").exists()
    assert "SYNTHETIC_PRIVATE" not in evidence.read_text() + capsys.readouterr().out
    rows = [json.loads(line) for line in evidence.read_text().splitlines()]
    assert rows[-1]["stage"] == "credential-removal" and rows[-1]["ok"] is True
    assert any("down" in call for call in scene.calls)
    assert sum("up" in call for call in scene.calls) == 1
    assert sum("run" in call for call in scene.calls) <= 1
    with pytest.raises(FileExistsError):
        scene.operator.run(SHA)


def test_namespace_cleanup_failure_cannot_pass(operator_scene):
    operator_scene.operator.namespace = lambda: [1, 0, 0]
    assert operator_scene.operator.run(SHA) == 1


@pytest.mark.parametrize("fault", ["location", "root-mode", "root-symlink"])
def test_snapshot_fail_closed_before_commands(operator_scene, monkeypatch, fault):
    scene = operator_scene
    if fault == "location":
        monkeypatch.setattr(scene.operator, "__file__", "/tmp/other/operator_b4.py")
    elif fault == "root-mode":
        scene.root.chmod(0o755)
    else:
        target = scene.root.with_name("target")
        scene.root.rename(target)
        scene.root.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError):
        scene.operator.run(SHA)
    assert scene.calls == []


def test_wrong_host_never_calls_cli(monkeypatch):
    operator = load("operator_b4")
    monkeypatch.setattr(operator.socket, "gethostname", lambda: "laptop")
    with pytest.raises(RuntimeError):
        operator.preflight()


def test_unknown_phase_cannot_escape_private_report_directory(monkeypatch, tmp_path):
    runner = load("run_b4")
    monkeypatch.setattr(runner, "PRIVATE", tmp_path)
    assert runner.phase_main("../../unreviewed") == 2
    assert list(tmp_path.iterdir()) == []


def test_private_file_is_0600_exclusive(tmp_path):
    runner = load("run_b4")
    path = tmp_path / "fixture"
    runner.private_file(path, "synthetic")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        runner.private_file(path, "replacement")
    assert path.read_text() == "synthetic"


def test_execute_discards_at_source(monkeypatch):
    runner = load("run_b4")

    def fake(args, **kwargs):
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 300 and kwargs["check"] is False
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(runner.subprocess, "run", fake)
    with pytest.raises(RuntimeError, match="dependencies"):
        runner.execute("dependencies", ["synthetic-command"], {})


@pytest.mark.parametrize(
    "fault", ["host", "database", "role", "empty-password", "driver", "mode", "symlink", "relative"]
)
def test_original_pg_configuration_rejects_before_connection(monkeypatch, tmp_path, fault):
    from sqlalchemy.engine import make_url

    module = load("test_postgres_router")
    database = "notebook_b4_" + SHA[:12]
    monkeypatch.setenv("B4_ASSIGNED_DATABASE", database)
    for role, user in (("APP", "notebook_app"), ("ADMIN", "postgres")):
        path = tmp_path / role
        path.touch(mode=0o600)
        monkeypatch.setenv(f"B4_{role}_PASSFILE", str(path))
        monkeypatch.setenv(f"B4_{role}_URL", f"postgresql://{user}@db:5432/{database}")
    if fault in ("host", "database", "role", "empty-password", "driver"):
        url = make_url(os.environ["B4_APP_URL"])
        if fault == "host":
            url = url.set(host="elsewhere")
        elif fault == "database":
            url = url.set(database="foreign")
        elif fault == "role":
            url = url.set(username="postgres")
        elif fault == "empty-password":
            # Empty URL passwords are forbidden too: None, not truthiness, is required.
            url = url.set(password="")
        else:
            url = url.set(drivername="sqlite")
        monkeypatch.setenv("B4_APP_URL", url.render_as_string(hide_password=False))
    elif fault == "mode":
        (tmp_path / "APP").chmod(0o644)
    elif fault == "symlink":
        (tmp_path / "APP").rename(tmp_path / "target")
        (tmp_path / "APP").symlink_to(tmp_path / "target")
    else:
        monkeypatch.setenv("B4_APP_PASSFILE", "relative")
    if fault == "empty-password":
        assert make_url(os.environ["B4_APP_URL"]).password == ""
    with pytest.raises(pytest.fail.Exception) as rejected:
        module.configuration()
    if fault == "empty-password":
        assert str(rejected.value) == "B4 assigned passwordless direct role endpoint required"


def test_manifest_hardening_and_pins():
    # Manifest is JSON-flow YAML with only two YAML quoted-line continuations.
    # Normalize that exact reviewed form, then use stdlib: no new PyYAML dependency.
    source = (HERE / "compose.b4.yaml").read_text()
    assert source.count("\\\n        ") == 2
    manifest = json.loads(source.replace("\\\n        ", ""))
    services = manifest["services"]
    assert set(services) == {"db", "runner"}
    assert (
        services["db"]["image"]
        == "postgres:16-alpine@sha256:aa6cb33cf4c19c765cefcf48d96176258800870a9a1e930ad126200f9d825139"
    )
    assert (
        services["runner"]["image"]
        == "python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df"
    )
    for service in services.values():
        assert service["read_only"] and service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["cpus"] == 1 and service["pids_limit"] == 128
        assert service["logging"] == {"driver": "none"}
        assert not any(key in service for key in ("ports", "privileged", "network_mode", "build"))
    assert services["db"]["mem_limit"] == "512m" and services["runner"]["mem_limit"] == "1g"
    assert services["db"]["networks"] == ["database"]
    assert manifest["networks"]["database"] == {"internal": True}
    assert len(services["runner"]["volumes"]) == 1 and services["runner"]["volumes"][0].endswith(
        ":/snapshot:ro"
    )
    assert "--auth-host=scram-sha-256" == services["db"]["environment"]["POSTGRES_INITDB_ARGS"]
    assert all(
        "BWS" not in key and "PASSWORD" not in key for key in services["runner"]["environment"]
    )


@pytest.mark.parametrize(
    "fault", [None, "memory", "disk", "load", "namespace", "cli", "root", "directory", "endpoint"]
)
def test_preflight_capacity_identity_and_cli_fail_closed(monkeypatch, fault):
    operator = load("operator_b4")
    monkeypatch.setattr(operator.socket, "gethostname", lambda: "kube1")
    monkeypatch.setattr(operator.os, "getuid", lambda: 0 if fault == "root" else 1000)
    monkeypatch.setattr(operator, "private_directory", lambda path: fault != "directory")
    monkeypatch.setattr(
        operator, "namespace", lambda: [1, 0, 0] if fault == "namespace" else [0, 0, 0]
    )
    monkeypatch.setattr(
        operator.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=0 if fault == "disk" else 4 * 1024**3),
    )
    monkeypatch.setattr(operator.os, "getloadavg", lambda: (7 if fault == "load" else 0, 0, 0))
    original = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *args, **kwargs: (
            ("MemAvailable: 0 kB\n" if fault == "memory" else "MemAvailable: 4194304 kB\n")
            if str(self) == "/proc/meminfo"
            else original(self, *args, **kwargs)
        ),
    )
    calls = []

    def command(args, *extra):
        calls.append(args)
        output = (
            b"ssh://SYNTHETIC_PRIVATE_FOREIGN"
            if fault == "endpoint"
            else b"unix:///var/run/docker.sock"
        )
        return SimpleNamespace(returncode=int(fault == "cli"), stdout=output)

    monkeypatch.setattr(operator, "command", command)
    if fault is None:
        assert operator.preflight()["namespace"] == [0, 0, 0]
        assert len(calls) == 3
    else:
        with pytest.raises(RuntimeError):
            operator.preflight()
    assert not any("up" in args or "pull" in args or "run" in args for args in calls)


def test_command_default_environment_has_no_inherited_docker_or_secret_config(monkeypatch):
    operator = load("operator_b4")
    monkeypatch.setenv("DOCKER_HOST", "SYNTHETIC_PRIVATE_FOREIGN")
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "SYNTHETIC_PRIVATE")

    def process(args, **kwargs):
        assert set(kwargs["env"]) <= {"PATH", "HOME", "XDG_RUNTIME_DIR"}
        assert kwargs["stderr"] == subprocess.DEVNULL
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr(operator.subprocess, "run", process)
    assert operator.command(["synthetic-no-execution"]).returncode == 0


def test_namespace_queries_include_stopped_and_are_exact(monkeypatch):
    operator = load("operator_b4")
    calls = []

    def command(args):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=b"one\ntwo\n")

    monkeypatch.setattr(operator, "command", command)
    assert operator.namespace() == [2, 2, 2]
    assert calls[0][:3] == ["docker", "ps", "-aq"]
    assert all(
        args[-2:] == ["--filter", "label=com.docker.compose.project=notebook-test-b4"]
        for args in calls
    )


def test_migrate_is_exact_0006_and_quiet(monkeypatch, tmp_path, capsys):
    from alembic import command

    runner = load("run_b4")
    monkeypatch.setattr(runner, "PRIVATE", tmp_path)
    calls = []

    def upgrade(config, revision):
        calls.append((config.config_file_name, revision))
        print("SYNTHETIC_PRIVATE_MIGRATION")

    monkeypatch.setattr(command, "upgrade", upgrade)
    assert runner.phase_main("migrate") == 0
    assert calls == [("backend/migrations/alembic.ini", "0006")]
    assert json.loads((tmp_path / "migrate.json").read_text()) == {"stage": "migrate", "ok": True}
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("bad_app", [False, True])
def test_role_phase_sets_only_synthetic_password_and_checks_direct_login(
    monkeypatch, tmp_path, bad_app
):
    import psycopg
    from psycopg import sql

    runner = load("run_b4")
    monkeypatch.setattr(runner, "PRIVATE", tmp_path)
    (tmp_path / "app-password").write_text(secrets.token_hex(24))
    calls = []
    for role, user in (("ADMIN", "postgres"), ("APP", "notebook_app")):
        monkeypatch.setenv(f"B4_{role}_URL", f"postgresql://{user}@db:5432/notebook_b4_{SHA[:12]}")
        monkeypatch.setenv(f"B4_{role}_PASSFILE", str(tmp_path / (role + ".pgpass")))

    class Database:
        def __init__(self, admin):
            self.admin = admin

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query):
            calls.append(query)
            if isinstance(query, sql.Composed):
                assert self.admin
                return None
            row = (
                ("postgres", "postgres", "scram-sha-256", "0006")
                if self.admin
                else ("notebook_app", "postgres" if bad_app else "notebook_app", False, False)
            )
            return SimpleNamespace(fetchone=lambda: row)

    def connect(url, **kwargs):
        assert (
            kwargs["connect_timeout"] == 3
            and kwargs["options"] == "-c statement_timeout=3000 -c lock_timeout=1000"
        )
        assert "passfile" in kwargs and "password" not in kwargs
        if "postgres@" in url:
            assert kwargs["cursor_factory"] is psycopg.ClientCursor
        return Database("postgres@" in url)

    monkeypatch.setattr(psycopg, "connect", connect)
    assert runner.phase_main("role") == int(bad_app)
    assert len(calls) == 3 and sum(isinstance(query, sql.Composed) for query in calls) == 1
    report = json.loads((tmp_path / "role.json").read_text())
    assert report["ok"] is (not bad_app)
    assert "synthetic" not in json.dumps(report)


@pytest.mark.parametrize(
    "failure", [None, "dependencies", "migrate", "postgres-timeout", "forged-report"]
)
def test_runner_stage_environment_and_credentials_cleanup(monkeypatch, tmp_path, capsys, failure):
    runner = load("run_b4")
    snapshot, work = tmp_path / "snapshot", tmp_path / "work"
    snapshot.mkdir()
    work.mkdir()
    for name in ("backend", "tests"):
        (snapshot / name).mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        (snapshot / name).touch()
    bootstrap = tmp_path / "bootstrap"
    bootstrap.write_text("c" * 48)

    def mapped_path(value):
        if value == "/run/secrets/bootstrap":
            return bootstrap
        if value == "/snapshot" or value.startswith("/snapshot/"):
            return snapshot / value.removeprefix("/snapshot").lstrip("/")
        if value == "/work" or value.startswith("/work/"):
            return work / value.removeprefix("/work").lstrip("/")
        raise AssertionError("unexpected absolute runner path")

    monkeypatch.setattr(runner, "Path", mapped_path)
    monkeypatch.setattr(runner, "SOURCE", work / "source")
    monkeypatch.setattr(runner, "PRIVATE", work / "private")
    monkeypatch.setattr(runner.os, "getuid", lambda: 1000)
    monkeypatch.setattr(runner.sys, "argv", ["run_b4.py"])
    monkeypatch.syspath_prepend(str(HERE))
    monkeypatch.setenv("B4_SNAPSHOT_SHA", SHA)
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "SYNTHETIC_PRIVATE_ENV")
    stages, installs = [], []

    def execute(stage, args, env, timeout=300):
        assert "BWS_ACCESS_TOKEN" not in env and "PGPASSWORD" not in env
        installs.append(args)
        if failure == "dependencies":
            raise RuntimeError("SYNTHETIC_PRIVATE_DEPENDENCIES")

    monkeypatch.setattr(runner, "execute", execute)

    def process(args, **kwargs):
        phase = args[-1]
        stages.append(phase)
        env = kwargs["env"]
        assert (
            kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL and kwargs["timeout"] == 420
        )
        assert "BWS_ACCESS_TOKEN" not in env and "PGPASSWORD" not in env
        assert ("NOTEBOOK_MIGRATION_DATABASE_URL" in env) == (phase == "migrate")
        assert ("PGPASSFILE" in env) == (phase == "migrate")
        assert (env.get("B4_RUN_POSTGRES") == "isolated-kube1-approved") == (phase == "postgres")
        if phase == "focused":
            assert not any(key.startswith("B4_") for key in env)
            assert not (runner.PRIVATE / "app.pgpass").exists()
        else:
            assert "notebook_app@db:5432/notebook_b4_" in env["B4_APP_URL"]
            for name in ("app.pgpass", "admin.pgpass", "app-password"):
                assert stat.S_IMODE((runner.PRIVATE / name).stat().st_mode) == 0o600
        if phase == "postgres" and failure == "postgres-timeout":
            raise subprocess.TimeoutExpired(args, 1, output=b"SYNTHETIC_PRIVATE_DRIVER")
        report = (
            success_report(phase)
            if phase in ("postgres", "focused")
            else {"stage": phase, "ok": True}
        )
        if phase == "migrate" and failure == "migrate":
            report = {"stage": phase, "ok": False, "error_type": "ProgrammingError"}
        if phase == "postgres" and failure == "forged-report":
            report["private"] = "SYNTHETIC_PRIVATE_DRIVER"
        runner.private_file(runner.PRIVATE / (phase + ".json"), json.dumps(report))
        return SimpleNamespace(returncode=int(phase == "migrate" and failure == "migrate"))

    monkeypatch.setattr(runner.subprocess, "run", process)
    assert runner.main() == int(failure is not None)
    assert not any(
        (runner.PRIVATE / name).exists() for name in ("app.pgpass", "admin.pgpass", "app-password")
    )
    output = capsys.readouterr().out
    assert "SYNTHETIC_PRIVATE" not in output and "c" * 48 not in output
    rows = [json.loads(line.removeprefix("B4_RESULT ")) for line in output.splitlines()]
    assert rows[-1] == {"stage": "runner-credentials", "ok": True}
    if failure is None:
        assert stages == ["migrate", "role", "postgres", "focused"]
        assert "uv==0.9.9" in installs[0] and installs[1][-3:] == ["sync", "--frozen", "--quiet"]
    assert len(stages) == len(set(stages))


def test_actual_reporter_50_synthetic_no_raw_output(monkeypatch, capsys):
    runner = load("run_b4")
    report = runner.pytest_report("focused")
    assert report["ok"] and report["passed"] == 50 and len(report["cases"]) == 50
    assert load("operator_b4").safe_report(report) is report
    assert capsys.readouterr().out == ""


def test_actual_reporter_hostile_failure_discarded(monkeypatch, tmp_path, capsys):
    runner = load("run_b4")
    test = tmp_path / "test_synthetic_reporter_probe.py"
    test.write_text(
        "def test_private_canary():\n print('SYNTHETIC_PRIVATE_STDOUT')\n raise RuntimeError('SYNTHETIC_PRIVATE_EXCEPTION')\n"
    )
    monkeypatch.setattr(runner, "FOCUSED", [str(test)])
    monkeypatch.setattr(runner, "EXPECTED", {"focused": 1})
    report = runner.pytest_report("focused")
    assert not report["ok"] and report["failed"] == 1
    assert report["cases"] == [
        {"test": "focused-01", "phase": "call", "outcome": "failed", "error_type": "RuntimeError"}
    ]
    assert "SYNTHETIC_PRIVATE" not in json.dumps(report) + capsys.readouterr().out
