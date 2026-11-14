"""Private serial SCRAM runner. Invoked only by the reviewed kube1 operator."""

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from protocol import (
    PHASES,
    RELATIVE,
    case_key,
    contract,
    need,
    safe_concurrency,
    safe_report,
    unique,
)

SOURCE = Path("/work/source")
PRIVATE = Path("/work/private")
PYTHON = SOURCE / ".venv/bin/python"


def private_file(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(value)


def environment():
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/work/tmp",
        "TMPDIR": "/work/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": ":".join(
            str(SOURCE / p)
            for p in (
                ".",
                "backend",
                "backend/tests/auth",
                "backend/tests/documents",
                "backend/tests/providers",
                "tests/security/disconnect_boundary",
                "tests/security/upload_http",
                str(RELATIVE),
            )
        ),
        "UV_NO_CACHE": "1",
        "UV_LINK_MODE": "copy",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_CONCURRENT_DOWNLOADS": "2",
        "UV_CONCURRENT_INSTALLS": "2",
        "RAYON_NUM_THREADS": "2",
        "UV_PROJECT_ENVIRONMENT": str(SOURCE / ".venv"),
        "PGPASSFILE": str(PRIVATE / "database.pgpass"),
        "PGCONNECT_TIMEOUT": "3",
        "PGOPTIONS": "-c statement_timeout=15000 -c lock_timeout=3000",
        "NOTEBOOK_TEST_POSTGRES_URL": "postgresql://postgres@db:5432/delegation",
        "NOTEBOOK_MIGRATION_DATABASE_URL": "postgresql+psycopg://postgres@db:5432/delegation",
    }


def quiet(args, timeout=420):
    result = subprocess.run(
        args,
        cwd=SOURCE,
        env=environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    need(result.returncode == 0)


def dependencies():
    SOURCE.mkdir(mode=0o700)
    PRIVATE.mkdir(mode=0o700)
    Path("/work/tmp").mkdir(mode=0o700)
    for source in Path("/snapshot").iterdir():
        name = source.name
        if source.is_dir():
            shutil.copytree(source, SOURCE / name)
        else:
            shutil.copyfile(source, SOURCE / name)
    # Established frozen lockfile convention; no image builds or arbitrary extra dependencies.
    quiet(
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
        180,
    )
    quiet(["/work/tools/bin/uv", "sync", "--frozen", "--quiet"], 600)


def credentials():
    material = sys.stdin.read(4096)
    data = json.loads(material)
    need(set(data) == {"admin", "app"})
    need(all(type(v) is str and re.fullmatch("[0-9a-f]{48}", v) for v in data.values()))
    # Wildcard DATABASE only: historical migration fixtures create fresh databases
    # and connect directly as notebook_app. No wildcard host/port/role or trust.
    private_file(
        PRIVATE / "database.pgpass",
        f"db:5432:*:postgres:{data['admin']}\ndb:5432:*:notebook_app:{data['app']}\n",
    )
    private_file(PRIVATE / "app-password", data["app"])


def principals():
    import psycopg
    from psycopg import sql

    with psycopg.connect(os.environ["NOTEBOOK_TEST_POSTGRES_URL"]) as db:
        need(
            db.execute(
                "SELECT current_user,session_user,current_setting('password_encryption'),"
                "(SELECT version_num FROM alembic_version)"
            ).fetchone()
            == ("postgres", "postgres", "scram-sha-256", "0007")
        )
        db.execute(
            sql.SQL("ALTER ROLE notebook_app PASSWORD {}").format(
                sql.Literal((PRIVATE / "app-password").read_text())
            )
        )
        need(
            db.execute(
                "SELECT count(*) FROM pg_roles WHERE rolname IN "
                "('notebook_text_scheduler','notebook_text_dispatch_owner') AND "
                "NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolinherit"
            ).fetchone()
            == (2,)
        )
    with psycopg.connect("postgresql://notebook_app@db:5432/delegation") as db:
        need(
            db.execute(
                "SELECT current_user,session_user,rolsuper,rolbypassrls FROM pg_roles "
                "WHERE rolname=current_user"
            ).fetchone()
            == ("notebook_app", "notebook_app", False, False)
        )


def concurrency_report(report, diagnostic):
    """Missing/failed diagnostics can only remove success, never override pytest."""
    diagnostic_ok = diagnostic is not None and safe_concurrency(diagnostic)["failure"] == "none"
    return safe_report(report | {"diagnostic": diagnostic, "ok": report["ok"] and diagnostic_ok})


def pytest_report(stage):
    import pytest

    reviewed = contract()
    expected = reviewed.get(stage)
    collected, rows, excluded = [], [], []
    exact = False
    diagnostic = None
    node_exclusions = json.loads((Path(__file__).parent / "node_exclusions.json").read_text())
    files = json.loads((Path(__file__).parent / "regression_files.json").read_text())
    paths = (
        sorted({case.split("::")[0] for case in expected})
        if expected
        else ["backend/tests", "tests/security"]
    )

    class Reporter:
        def pytest_collection_modifyitems(self, session, config, items):
            nonlocal exact
            if stage == "regression":
                need(sorted({i.nodeid.split("::")[0] for i in items}) == files)
                removed = [i for i in items if i.nodeid in node_exclusions]
                need([i.nodeid for i in removed] == node_exclusions)
                excluded.extend(i.nodeid for i in removed)
                items[:] = [i for i in items if i not in removed]
                config.hook.pytest_deselected(items=removed)
            collected.extend(case_key(i) for i in items)
            exact = collected == (
                expected
                if expected
                else json.loads((Path(__file__).parent / "regression_cases.json").read_text())
            )
            need(exact and len(set(collected)) == len(collected))

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            nonlocal diagnostic
            outcome = yield
            report = outcome.get_result()
            if stage == "concurrency" and report.when == "call":
                diagnostic = getattr(item, "concurrency_diagnostic", None)
                if diagnostic is not None:
                    safe_concurrency(diagnostic)
            rows.append(
                {
                    "case": collected.index(case_key(item)),
                    "when": report.when,
                    "outcome": report.outcome,
                    "xfail": hasattr(report, "wasxfail"),
                }
            )

    with (
        open(os.devnull, "w") as sink,
        contextlib.redirect_stdout(sink),
        contextlib.redirect_stderr(sink),
    ):
        code = int(
            pytest.main(
                [
                    "--import-mode=importlib",
                    "-p",
                    "no:terminal",
                    "-p",
                    "no:cacheprovider",
                    "-p",
                    "anyio.pytest_plugin",
                    *paths,
                ],
                plugins=[Reporter()],
            )
        )
    if stage == "regression":
        report = {
            "stage": stage,
            "ok": False,
            "collection": exact,
            "exit": code,
            "cases": rows,
            "collected": collected,
            "excluded": excluded,
        }
        from protocol import regression_ok

        report["ok"] = regression_ok(report)
        return report
    success = (
        exact
        and code == 0
        and len(rows) == 3 * len(expected)
        and all(r["outcome"] == "passed" and not r["xfail"] for r in rows)
    )
    report = {"stage": stage, "ok": success, "collection": exact, "exit": code, "cases": rows}
    if stage == "concurrency":
        return concurrency_report(report, diagnostic)
    return safe_report(report)


def phase(stage):
    os.chdir(SOURCE)
    if stage == "migrate":
        from alembic import command
        from alembic.config import Config

        with (
            open(os.devnull, "w") as sink,
            contextlib.redirect_stdout(sink),
            contextlib.redirect_stderr(sink),
        ):
            command.upgrade(Config("backend/migrations/alembic.ini"), "0007")
    elif stage == "principals":
        principals()
    else:
        return pytest_report(stage)
    return {"stage": stage, "ok": True}


def main():
    os.umask(0o077)
    need(os.getuid() == os.geteuid() == 1001)
    mode = sys.argv[1]
    if mode == "dependencies":
        dependencies()
        print(json.dumps({"stage": "dependencies", "ok": True}))
        return 0
    if mode == "phase":
        report = phase(sys.argv[2])
        private_file(PRIVATE / (sys.argv[2] + ".json"), json.dumps(report))
        return 0 if report["ok"] else 1
    need(mode == "run")
    credentials()
    try:
        for stage in ("migrate", "principals", *PHASES, "regression"):
            result = subprocess.run(
                [str(PYTHON), str(Path(__file__).resolve()), "phase", stage],
                cwd=SOURCE,
                env=environment(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=900 if stage == "regression" else 300,
                check=False,
            )
            path = PRIVATE / (stage + ".json")
            need(path.is_file() and path.stat().st_size < 4 * 1024**2)
            report = safe_report(json.loads(path.read_text(), object_pairs_hook=unique))
            print(json.dumps(report), flush=True)
            need(result.returncode == 0 and report["ok"])
        return 0
    finally:
        for name in ("app-password", "database.pgpass"):
            (PRIVATE / name).unlink(missing_ok=True)
        print(json.dumps({"stage": "credentials", "ok": True}), flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001 — no raw diagnostics, runner tmpfs removed on failure
        raise SystemExit(1) from None
