"""One-shot private DB authority; never mounted in long-running services.

Run from the reviewed API image with only its private admin consumer directory.
No password is accepted in argv and no SQL/driver diagnostics are forwarded.
"""

import argparse
import logging
import os
import re
import sys
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from app.auth.bootstrap import bootstrap
from psycopg import ClientCursor
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from deploy.poc_setup import ADMIN_EMAIL, SetupError, private_value


class DatabaseSetupError(Exception):
    """Sanitized operator failure."""


def migration_url(password: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{48}", password):
        raise DatabaseSetupError("Invalid dedicated database credential")
    return URL.create(
        "postgresql+psycopg",
        username="postgres",
        password=password,
        host="db",
        port=5432,
        database="postgres",
    ).render_as_string(hide_password=False)


def app_password(value: str) -> str:
    try:
        url = make_url(value)
        if (
            url.drivername != "postgresql+psycopg"
            or url.username != "notebook_app"
            or url.host != "db"
            or url.port != 5432
            or url.database != "postgres"
            or url.query
            or not re.fullmatch(r"[A-Za-z0-9_-]{48}", url.password or "")
        ):
            raise ValueError()
        return str(url.password)
    except (ValueError, SQLAlchemyError):
        raise DatabaseSetupError("Invalid dedicated API credential") from None


def activate_api_role(url: str, password: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{48}", password):
        raise DatabaseSetupError("Invalid dedicated API credential")
    parsed = make_url(url)
    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.username != "postgres"
        or parsed.host != "db"
        or parsed.port != 5432
        or parsed.database != "postgres"
        or parsed.query
    ):
        raise DatabaseSetupError("Invalid migration authority")
    conninfo = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    with (
        psycopg.connect(conninfo, cursor_factory=ClientCursor, connect_timeout=5) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL log_statement = 'none'")
        cursor.execute("SET LOCAL log_min_error_statement = 'panic'")
        cursor.execute("SET LOCAL log_parameter_max_length_on_error = 0")
        cursor.execute(
            "SELECT rolname,rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication FROM pg_roles WHERE rolname='notebook_app'"
        )
        if cursor.fetchone() != ("notebook_app", False, False, False, False, False):
            raise DatabaseSetupError("Unexpected API role authority")
        cursor.execute("ALTER ROLE notebook_app LOGIN PASSWORD %s", (password,))
        cursor.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname IN ('notebook_storage_io','notebook_worker_scheduler') AND rolcanlogin"
        )
        if cursor.fetchone() != (0,):
            raise DatabaseSetupError("Unexpected capability login")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["migrate", "activate-api-role", "admit-confirmed-owner"])
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        url = migration_url(
            private_value(Path("/run/poc-admin/notebook_postgres_password")).strip()
        )
        if args.action == "migrate":
            os.environ["NOTEBOOK_MIGRATION_DATABASE_URL"] = url
            config = Config()
            config.set_main_option("script_location", "/app/backend/migrations")
            command.upgrade(config, "head")
        elif args.action == "activate-api-role":
            password = app_password(
                private_value(Path("/run/poc-admin/notebook_database_url")).strip()
            )
            activate_api_role(url, password)
        else:
            bootstrap(ADMIN_EMAIL, url)
    except (SetupError, DatabaseSetupError, SQLAlchemyError, psycopg.Error, ValueError, OSError):
        print("Private database operation failed; no diagnostics disclosed.", file=sys.stderr)
        return 1
    finally:
        os.environ.pop("NOTEBOOK_MIGRATION_DATABASE_URL", None)
    print("Private database operation completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
