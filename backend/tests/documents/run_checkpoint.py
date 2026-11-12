"""Kube1-only bounded checkpoint runner; never use against an existing database.

The Compose definition provides a private, unexposed ephemeral PostgreSQL service
and a synthetic per-run credential file. Only a copied scratch source is writable;
/snapshot remains the immutable committed archive. No production settings loaded.
"""

import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

PASSWORD_FILE = Path("/run/secrets/test_password")
SOURCE = Path("/work/source")
TARGET = "backend/tests/ingestion/test_worker.py::test_postcommit_watchdog_loss_does_not_misreport_success"


def run(*args: str, env: dict[str, str]) -> None:
    subprocess.run(args, cwd=SOURCE, env=env, check=True)


def configure_test_role() -> None:
    import psycopg
    from psycopg import sql

    with psycopg.connect(os.environ["NOTEBOOK_TEST_POSTGRES_URL"]) as db:
        db.execute(
            sql.SQL("ALTER ROLE notebook_app PASSWORD {}").format(
                sql.Literal(Path("/work/private/runtime").read_text())
            )
        )


def main() -> None:
    if os.getuid() == 0 or not Path("/snapshot/uv.lock").is_file() or not Path("/work").is_dir():
        raise RuntimeError("run only in assigned nonroot checkpoint container")
    if sys.argv[1:] == ["--configure-test-role"]:
        configure_test_role()
        return
    SOURCE.mkdir()
    for name in ("backend", "tests", "pyproject.toml", "uv.lock"):
        original = Path("/snapshot") / name
        if original.is_dir():
            shutil.copytree(original, SOURCE / name)
        else:
            shutil.copyfile(original, SOURCE / name)
    (SOURCE.parent / "tmp").mkdir()
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
    }
    run(
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
        env=env,
    )
    run("/work/tools/bin/uv", "sync", "--frozen", "--quiet", env=env)
    material = PASSWORD_FILE.read_text().strip()
    if len(material) != 48 or any(c not in "0123456789abcdef" for c in material):
        raise RuntimeError("invalid synthetic test credential")
    private = Path("/work/private")
    private.mkdir(mode=0o700)
    runtime = secrets.token_hex(24)
    for name, value in {
        "runtime": runtime,
        "pgpass": f"database:5432:*:postgres:{material}\ndatabase:5432:*:notebook_app:{runtime}\n",
    }.items():
        with os.fdopen(
            os.open(private / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as target:
            target.write(value)
    # libpq reads mode0600 per-role synthetic credentials; no secret in URL/env/CLI.
    env["PGPASSFILE"] = str(private / "pgpass")
    env["NOTEBOOK_TEST_POSTGRES_URL"] = "postgresql://postgres@database/notebook_b3_test"
    env["NOTEBOOK_MIGRATION_DATABASE_URL"] = (
        "postgresql+psycopg://postgres@database/notebook_b3_test"
    )
    python = str(SOURCE / ".venv/bin/python")
    run(
        str(SOURCE / ".venv/bin/alembic"),
        "-c",
        "backend/migrations/alembic.ini",
        "upgrade",
        "head",
        env=env,
    )
    run(python, __file__, "--configure-test-role", env=env)
    worker = SOURCE / "backend/app/workers/text.py"
    fixed = worker.read_text()
    new = """            if work in done:
                await work
                return "succeeded"
            watcher.result()
            raise LeaseLost()"""
    old = '''            if watcher in done:
                watcher.result()
            await work
            return "succeeded"'''
    if fixed.count(new) != 1:
        raise RuntimeError("checkpoint reporting fix changed; review regression runner")
    command = [python, "-m", "pytest", TARGET, "-q", "--tb=short"]
    try:
        # RED uses only an ephemeral scratch copy to restore the old reporting bug;
        # publication/leases/RLS are unchanged, snapshot is never edited.
        worker.write_text(fixed.replace(new, old))
        red = subprocess.run(
            command, cwd=SOURCE, env=env, capture_output=True, text=True, check=False
        )
        if red.returncode != 1 or "lease_lost" not in red.stdout or "1 failed" not in red.stdout:
            print(red.stdout, red.stderr)
            raise RuntimeError("expected watchdog-reporting RED was not reproduced")
        print("Reporting regression RED: 1 expected assertion failure (old reporting only).")
    finally:
        worker.write_text(fixed)
    run(*command, env=env)
    print("Reporting regression GREEN: committed worker, real restricted-role PostgreSQL.")
    run(
        python,
        "-m",
        "pytest",
        "backend/tests/documents/test_service.py",
        "-q",
        "--tb=short",
        env=env,
    )
    run(python, "-m", "pytest", "-q", "--tb=short", env=env)
    run(str(SOURCE / ".venv/bin/ruff"), "check", "--no-cache", "backend", "tests/security", env=env)


if __name__ == "__main__":
    main()
