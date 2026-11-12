"""Isolated migration fixture; never modifies the supplied fixture database."""

import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url


def migrate(url: str, revision: str):
    root = Path(__file__).resolve().parents[3]
    return subprocess.run(
        [
            str(root / ".venv/bin/alembic"),
            "-c",
            "backend/migrations/alembic.ini",
            "upgrade",
            revision,
        ],
        cwd=root,
        env={
            **os.environ,
            "PYTHONPATH": str(root / "backend"),
            "NOTEBOOK_MIGRATION_DATABASE_URL": make_url(url)
            .set(drivername="postgresql+psycopg")
            .render_as_string(hide_password=False),
        },
        capture_output=True,
        text=True,
        check=False,
    )


@contextmanager
def isolated_database(base_url: str, revision: str):
    name = "quota_version_" + uuid4().hex
    url = make_url(base_url).set(database=name)
    with psycopg.connect(base_url, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(
                sql.Identifier(name)
            )
        )
    try:
        result = migrate(url.render_as_string(hide_password=False), revision)
        assert result.returncode == 0, "Version-pinned fixture migration failed (output suppressed)"
        yield url.render_as_string(hide_password=False)
    finally:
        with psycopg.connect(base_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
