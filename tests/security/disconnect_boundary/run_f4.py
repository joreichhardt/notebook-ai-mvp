"""Nonroot kube1 runner: frozen dependencies, fresh migration, safe test reports.

Never run on the laptop. Raw subprocess output is discarded, never persisted.
Only fixed stage labels, enumerated outcomes and allowlisted assertion labels leave.
"""

import ast
import contextlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = Path("/work/source")
PRIVATE = Path("/work/private")
HERE = Path("/snapshot/tests/security/disconnect_boundary")
PG_TEST = "tests/security/disconnect_boundary/test_postgres_acceptance.py"
FOCUSED = [
    "tests/security/disconnect_boundary/test_discovery_revalidation.py",
    "tests/security/disconnect_boundary/test_guarded_discovery.py",
    "tests/security/disconnect_boundary/test_fixed_discovery_errors.py",
    "tests/security/disconnect_boundary/test_postgres_packet_guards.py",
    "tests/security/disconnect_boundary/test_operator_packet.py",
    "backend/tests/providers/test_owned_execution.py",
    "backend/tests/providers/test_review_edges.py",
    "backend/tests/providers/test_connections.py",
]


def private_file(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(value)


def error_kind(error):
    name = type(error).__name__
    return name if re.fullmatch(r"[A-Za-z_]{1,64}", name) else "Error"


def execute(stage, args, env, timeout=300):
    # Discard ALL stdout/stderr at source, not raw-log-then-redact.
    result = subprocess.run(
        args,
        cwd=SOURCE,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(stage)


def pytest_report(phase):
    import pytest

    paths = [PG_TEST] if phase == "postgres" else FOCUSED
    expected = 30 if phase == "postgres" else 135
    # Labels only from literal require(..., label)/redacted(label) calls in reviewed packet.
    tree = ast.parse((SOURCE / PG_TEST).read_text())
    labels = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            index = {"require": 1, "redacted": 0}.get(node.func.id)
            if index is not None and len(node.args) > index:
                label = node.args[index]
                if isinstance(label, ast.Constant) and isinstance(label.value, str):
                    labels.add(label.value)
    reports = []

    class Reporter:
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            result = yield
            report = result.get_result()
            if report.when == "call" or report.failed or report.skipped:
                # Node IDs originate only from immutable selected tests; no request values.
                name = item.nodeid
                if not re.fullmatch(r"[A-Za-z0-9_./:\[\]-]{1,400}", name):
                    name = "unrecognized-test-id"
                row = {"test": name, "phase": report.when, "outcome": report.outcome}
                if call.excinfo is not None:
                    row["error_type"] = error_kind(call.excinfo.value)
                    if isinstance(call.excinfo.value, pytest.fail.Exception):
                        message = str(call.excinfo.value)
                        if message in labels:
                            row["label"] = message
                reports.append(row)

    with (
        open(os.devnull, "w") as sink,
        contextlib.redirect_stdout(sink),
        contextlib.redirect_stderr(sink),
    ):
        code = pytest.main(
            ["-p", "no:terminal", "-p", "no:cacheprovider", *paths],
            plugins=[Reporter()],
        )
    passed = sum(r["outcome"] == "passed" and r["phase"] == "call" for r in reports)
    failed = sum(r["outcome"] == "failed" for r in reports)
    skipped = sum(r["outcome"] == "skipped" for r in reports)
    return {
        "stage": phase,
        "ok": code == 0 and passed == expected and failed == skipped == 0,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "expected": expected,
        "cases": reports,
    }


def phase_main(phase):
    try:
        if phase == "migrate":
            from alembic import command
            from alembic.config import Config

            with (
                open(os.devnull, "w") as sink,
                contextlib.redirect_stdout(sink),
                contextlib.redirect_stderr(sink),
            ):
                command.upgrade(Config("backend/migrations/alembic.ini"), "head")
            report = {"stage": phase, "ok": True}
        elif phase == "role":
            import psycopg
            from psycopg import sql

            # ClientCursor required for parameterized DDL, direct admin fixture only.
            with psycopg.connect(
                os.environ["F4_ADMIN_URL"],
                passfile=os.environ["F4_ADMIN_PASSFILE"],
                cursor_factory=psycopg.ClientCursor,
                connect_timeout=3,
            ) as db:
                db.execute(
                    sql.SQL("ALTER ROLE notebook_app PASSWORD {}").format(
                        sql.Literal((PRIVATE / "app-password").read_text())
                    )
                )
            report = {"stage": phase, "ok": True}
        elif phase in ("postgres", "focused"):
            report = pytest_report(phase)
        else:
            raise RuntimeError("unknown phase")
    except BaseException as error:  # noqa: BLE001 — no raw diagnostic may escape
        report = {"stage": phase, "ok": False, "error_type": error_kind(error)}
        state = getattr(error, "sqlstate", None)
        if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state):
            report["sqlstate"] = state
    private_file(PRIVATE / (phase + ".json"), json.dumps(report))
    return 0 if report["ok"] else 1


def main():
    if os.getuid() == 0 or not Path("/snapshot/uv.lock").is_file() or not Path("/work").is_dir():
        return 2
    if len(sys.argv) == 3 and sys.argv[1] == "--phase":
        return phase_main(sys.argv[2])
    stage = "prepare"
    try:
        sha = os.environ["F4_SNAPSHOT_SHA"]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise RuntimeError("invalid snapshot")
        SOURCE.mkdir(mode=0o700)
        for name in ("backend", "tests", "pyproject.toml", "uv.lock"):
            source = Path("/snapshot") / name
            if source.is_dir():
                shutil.copytree(source, SOURCE / name)
            else:
                shutil.copyfile(source, SOURCE / name)
        PRIVATE.mkdir(mode=0o700)
        Path("/work/tmp").mkdir(mode=0o700)
        env = {
            **os.environ,
            "TMPDIR": "/work/tmp",
            "UV_NO_CACHE": "1",
            "UV_LINK_MODE": "copy",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_CONCURRENT_DOWNLOADS": "2",
            "UV_CONCURRENT_INSTALLS": "2",
            "RAYON_NUM_THREADS": "2",
            "UV_PROJECT_ENVIRONMENT": str(SOURCE / ".venv"),
            "PYTHONPATH": str(SOURCE / "backend"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
        stage = "dependencies"
        execute(
            stage,
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--no-cache-dir",
                "--disable-pip-version-check",
                "--target",
                "/work/tools",
                "uv==0.9.9",
            ],
            env,
        )
        execute(stage, ["/work/tools/bin/uv", "sync", "--frozen", "--quiet"], env, timeout=600)
        database = "notebook_f4_" + sha[:12]
        admin_password = Path("/run/secrets/bootstrap").read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{48}", admin_password):
            raise RuntimeError("invalid bootstrap")
        app_password = secrets.token_hex(24)
        private_file(PRIVATE / "app-password", app_password)
        for name, user, password in (
            ("app", "notebook_app", app_password),
            ("admin", "postgres", admin_password),
        ):
            private_file(PRIVATE / (name + ".pgpass"), f"db:5432:{database}:{user}:{password}\n")
        env.update(
            {
                "F4_ASSIGNED_HOST": "db",
                "F4_ASSIGNED_DATABASE": database,
                "F4_APP_URL": f"postgresql://notebook_app@db:5432/{database}",
                "F4_ADMIN_URL": f"postgresql://postgres@db:5432/{database}",
                "F4_APP_PASSFILE": str(PRIVATE / "app.pgpass"),
                "F4_ADMIN_PASSFILE": str(PRIVATE / "admin.pgpass"),
                "NOTEBOOK_MIGRATION_DATABASE_URL": f"postgresql+psycopg://postgres@db:5432/{database}",
                "PGPASSFILE": str(PRIVATE / "admin.pgpass"),
            }
        )
        env.pop("NOTEBOOK_TEST_POSTGRES_URL", None)
        python = str(SOURCE / ".venv/bin/python")
        for stage in ("migrate", "role", "postgres", "focused"):
            if stage == "postgres":
                env["F4_RUN_POSTGRES"] = "isolated-kube1-approved"
            else:
                env.pop("F4_RUN_POSTGRES", None)
            process = subprocess.run(
                [python, str(HERE / "run_f4.py"), "--phase", stage],
                cwd=SOURCE,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=420,
                check=False,
            )
            result_file = PRIVATE / (stage + ".json")
            if result_file.is_file():
                report = json.loads(result_file.read_text())
                print("F4_RESULT " + json.dumps(report), flush=True)
            else:
                report = {"ok": False}
            if process.returncode or not report["ok"]:
                return 1  # NO retry; Main must diagnose/rule before another run
        return 0
    except BaseException as error:  # noqa: BLE001 — fixed stage/type only
        print(
            "F4_RESULT "
            + json.dumps({"stage": stage, "ok": False, "error_type": error_kind(error)}),
            flush=True,
        )
        return 1
    finally:
        # Container tmpfs also disappears; explicit credential removal is defense in depth.
        for name in ("app-password", "app.pgpass", "admin.pgpass"):
            (PRIVATE / name).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
