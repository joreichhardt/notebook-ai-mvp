"""Nonroot B4 runner: frozen dependencies, migration0006, exactly7PG+50synthetic.

Raw subprocess stdout/stderr goes to DEVNULL at source; reports contain fixed
case ordinals in pytest collection order, enumerated outcomes/error types only.
"""

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
HERE = Path("/snapshot/tests/security/document_list")
PG_TEST = "tests/security/document_list/test_postgres_router.py"
FOCUSED = ["tests/security/document_list/test_router.py"]
EXPECTED = {"postgres": 7, "focused": 50}
ERRORS = {
    "Error",
    "RuntimeError",
    "TimeoutExpired",
    "PermissionError",
    "FileExistsError",
    "FileNotFoundError",
    "ValueError",
    "AssertionError",
    "Failed",
    "Skipped",
    "OperationalError",
    "ProgrammingError",
    "IntegrityError",
    "HTTPException",
}


def private_file(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(value)


def error_kind(error):
    name = type.__getattribute__(type(error), "__name__")
    return name if type(name) is str and name in ERRORS else "Error"


def execute(stage, args, env, timeout=300):
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

    expected = EXPECTED[phase]
    paths = [PG_TEST] if phase == "postgres" else FOCUSED
    reports = []
    ordinals = {}

    class Reporter:
        def pytest_collection_modifyitems(self, items):
            # No raw IDs/param values/exception strings exported, even on failure.
            for index, item in enumerate(items, 1):
                ordinals[item.nodeid] = f"{phase}-{index:02}"

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            result = yield
            report = result.get_result()
            if report.when == "call" or report.failed or report.skipped:
                row = {
                    "test": ordinals[item.nodeid],
                    "phase": report.when,
                    "outcome": report.outcome,
                }
                if call.excinfo is not None:
                    row["error_type"] = error_kind(call.excinfo.value)
                reports.append(row)

    with (
        open(os.devnull, "w") as sink,
        contextlib.redirect_stdout(sink),
        contextlib.redirect_stderr(sink),
    ):
        code = pytest.main(
            ["-p", "no:terminal", "-p", "no:cacheprovider", *paths], plugins=[Reporter()]
        )
    passed = sum(r["outcome"] == "passed" and r["phase"] == "call" for r in reports)
    failed = sum(r["outcome"] == "failed" for r in reports)
    skipped = sum(r["outcome"] == "skipped" for r in reports)
    return {
        "stage": phase,
        "ok": code == 0
        and len(ordinals) == expected
        and passed == expected
        and failed == skipped == 0,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "expected": expected,
        "cases": reports,
    }


def phase_main(phase):
    if phase not in {"migrate", "role", "postgres", "focused"}:
        return 2
    try:
        if phase == "migrate":
            from alembic import command
            from alembic.config import Config

            with (
                open(os.devnull, "w") as sink,
                contextlib.redirect_stdout(sink),
                contextlib.redirect_stderr(sink),
            ):
                command.upgrade(Config("backend/migrations/alembic.ini"), "0006")
            report = {"stage": phase, "ok": True}
        elif phase == "role":
            import psycopg
            from psycopg import sql

            # Synthetic credential only; no grants/role attributes/schema edits.
            with psycopg.connect(
                os.environ["B4_ADMIN_URL"],
                passfile=os.environ["B4_ADMIN_PASSFILE"],
                cursor_factory=psycopg.ClientCursor,
                connect_timeout=3,
                options="-c statement_timeout=3000 -c lock_timeout=1000",
            ) as db:
                if db.execute(
                    "SELECT current_user,session_user,current_setting('password_encryption'),"
                    "(SELECT version_num FROM alembic_version)"
                ).fetchone() != ("postgres", "postgres", "scram-sha-256", "0006"):
                    raise RuntimeError("migration-principal")
                db.execute(
                    sql.SQL("ALTER ROLE notebook_app PASSWORD {}").format(
                        sql.Literal((PRIVATE / "app-password").read_text())
                    )
                )
            with psycopg.connect(
                os.environ["B4_APP_URL"],
                passfile=os.environ["B4_APP_PASSFILE"],
                connect_timeout=3,
                options="-c statement_timeout=3000 -c lock_timeout=1000",
            ) as db:
                if db.execute(
                    "SELECT current_user,session_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                ).fetchone() != ("notebook_app", "notebook_app", False, False):
                    raise RuntimeError("app-principal")
            report = {"stage": phase, "ok": True}
        elif phase in EXPECTED:
            report = pytest_report(phase)
        else:
            raise RuntimeError("unknown-phase")
    except BaseException as error:  # noqa: BLE001 — no exception strings or SQL
        report = {"stage": phase, "ok": False, "error_type": error_kind(error)}
    private_file(PRIVATE / (phase + ".json"), json.dumps(report))
    return 0 if report["ok"] else 1


def main():
    if os.getuid() == 0 or not Path("/snapshot/uv.lock").is_file() or not Path("/work").is_dir():
        return 2
    if len(sys.argv) == 3 and sys.argv[1] == "--phase":
        return phase_main(sys.argv[2])
    stage = "prepare"
    success = False
    try:
        sha = os.environ["B4_SNAPSHOT_SHA"]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise RuntimeError("invalid-snapshot")
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
            "PATH": os.environ["PATH"],
            "HOME": "/work/tmp",
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
        database = "notebook_b4_" + sha[:12]
        admin_password = Path("/run/secrets/bootstrap").read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{48}", admin_password):
            raise RuntimeError("invalid-bootstrap")
        app_password = secrets.token_hex(24)
        private_file(PRIVATE / "app-password", app_password)
        for name, user, password in (
            ("app", "notebook_app", app_password),
            ("admin", "postgres", admin_password),
        ):
            private_file(PRIVATE / (name + ".pgpass"), f"db:5432:{database}:{user}:{password}\n")
        env.update(
            {
                "B4_ASSIGNED_DATABASE": database,
                "B4_APP_URL": f"postgresql://notebook_app@db:5432/{database}",
                "B4_ADMIN_URL": f"postgresql://postgres@db:5432/{database}",
                "B4_APP_PASSFILE": str(PRIVATE / "app.pgpass"),
                "B4_ADMIN_PASSFILE": str(PRIVATE / "admin.pgpass"),
                "NOTEBOOK_MIGRATION_DATABASE_URL": f"postgresql+psycopg://postgres@db:5432/{database}",
                "PGPASSFILE": str(PRIVATE / "admin.pgpass"),
            }
        )
        python = str(SOURCE / ".venv/bin/python")
        for stage in ("migrate", "role", "postgres", "focused"):
            if stage != "migrate":
                env.pop("PGPASSFILE", None)
                env.pop("NOTEBOOK_MIGRATION_DATABASE_URL", None)
            if stage == "focused":
                for key in tuple(env):
                    if key.startswith("B4_"):
                        env.pop(key)
                for name in ("app-password", "app.pgpass", "admin.pgpass"):
                    (PRIVATE / name).unlink(missing_ok=True)
            if stage == "postgres":
                env["B4_RUN_POSTGRES"] = "isolated-kube1-approved"
            else:
                env.pop("B4_RUN_POSTGRES", None)
            process = subprocess.run(
                [python, str(HERE / "run_b4.py"), "--phase", stage],
                cwd=SOURCE,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=420,
                check=False,
            )
            result_file = PRIVATE / (stage + ".json")
            if not result_file.is_file() or result_file.stat().st_size > 65536:
                raise RuntimeError("missing-report")
            report = json.loads(result_file.read_text())
            # Operator repeats closed-schema validation before durable evidence.
            from operator_b4 import safe_report

            print("B4_RESULT " + json.dumps(safe_report(report)), flush=True)
            if process.returncode or not report["ok"]:
                return 1  # single shot; no automatic retry
        success = True
    except BaseException as error:  # noqa: BLE001 — enum only
        print(
            "B4_RESULT "
            + json.dumps({"stage": stage, "ok": False, "error_type": error_kind(error)}),
            flush=True,
        )
    finally:
        try:
            paths = [PRIVATE / name for name in ("app-password", "app.pgpass", "admin.pgpass")]
            for path in paths:
                path.unlink(missing_ok=True)
            removed = not any(path.exists() for path in paths)
            print(
                "B4_RESULT " + json.dumps({"stage": "runner-credentials", "ok": removed}),
                flush=True,
            )
            success = success and removed
        except BaseException:  # noqa: BLE001 — tmpfs also removed with container
            print('B4_RESULT {"stage":"runner-credentials","ok":false}', flush=True)
            success = False
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
