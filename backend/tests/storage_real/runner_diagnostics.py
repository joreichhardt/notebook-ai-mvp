"""Fixed runner failure metadata only. Never inspect SQL, args, repr or traceback."""

import contextlib
import json
from collections.abc import Callable

import psycopg
from alembic.util.exc import CommandError
from sqlalchemy import exc as sa_exc

STAGES = (
    "preflight",
    "bootstrap_credentials",
    "native_catalog",
    "upstream_probe",
    "migration_owner",
    "alembic_upgrade",
    "migrator_disable",
    "migration_verify",
    "runtime_logins",
    "app_catalog",
    "actor_denials",
    "helper_acl",
    "runtime_memberships",
    "storage_graph",
    "credential_cleanup",
    "complete",
)
SQLSTATES = (
    "08006",
    "28P01",
    "23503",
    "23505",
    "3F000",
    "42501",
    "42601",
    "42703",
    "42704",
    "42710",
    "42883",
    "42P01",
    "42P07",
    "55P03",
    "57014",
)
# Identity matching avoids hostile class-name, __class__, hash and string getters.
TYPES = (
    (AssertionError, "assertion"),
    (ValueError, "value"),
    (OSError, "os"),
    (CommandError, "alembic_command"),
    (sa_exc.ProgrammingError, "sqlalchemy_programming"),
    (sa_exc.OperationalError, "sqlalchemy_operational"),
    (sa_exc.IntegrityError, "sqlalchemy_integrity"),
    (sa_exc.SQLAlchemyError, "sqlalchemy_error"),
    (psycopg.ProgrammingError, "driver_programming"),
    (psycopg.OperationalError, "driver_operational"),
    (psycopg.IntegrityError, "driver_integrity"),
    (psycopg.errors.InsufficientPrivilege, "driver_programming"),
    (psycopg.errors.UndefinedTable, "driver_programming"),
    (psycopg.errors.UndefinedColumn, "driver_programming"),
    (psycopg.errors.UndefinedObject, "driver_programming"),
)
ABSENT = object()
INVALID = object()


def _get(source, name):
    try:
        return getattr(source, name, ABSENT)
    except BaseException:  # noqa: BLE001 -- hostile metadata getters must not leak exit text
        # Narrow metadata boundary only. A hostile getter must not print SystemExit
        # text or replace the original runner exit. This does NOT catch operation exits.
        return INVALID


def _state(error) -> str:
    # At most two candidates, two structured fields each; no recursive causes/contexts.
    candidate = error
    for index in range(2):
        value = _get(candidate, "sqlstate")
        if value is INVALID:
            return "unknown"
        if value is ABSENT or value is None:
            diag = _get(candidate, "diag")
            if diag is INVALID:
                return "unknown"
            value = _get(diag, "sqlstate") if diag is not ABSENT and diag is not None else ABSENT
        if value is INVALID:
            return "unknown"
        if value is not ABSENT and value is not None:
            if type(value) is not str or len(value) != 5:
                return "unknown"
            return next((fixed for fixed in SQLSTATES if value == fixed), "unknown")
        if index == 0:
            candidate = _get(error, "orig")
            if candidate is INVALID or candidate is ABSENT or candidate is None:
                break
    return "unknown"


class RunnerTrace:
    def __init__(self):
        self.current = "preflight"

    def enter(self, stage: str):
        self.current = next(
            (fixed for fixed in STAGES if type(stage) is str and stage == fixed),
            "unknown",
        )

    @contextlib.contextmanager
    def cleanup(self, stage: str):
        previous = self.current
        self.enter(stage)
        yield
        # Restore only if cleanup succeeded; a cleanup failure owns its own stage.
        self.enter(previous)

    def failure(self, error: BaseException) -> str:
        stage = next(
            (fixed for fixed in STAGES if type(self.current) is str and self.current == fixed),
            "unknown",
        )
        category = next((fixed for cls, fixed in TYPES if type(error) is cls), "unknown")
        return json.dumps(
            {"stage": stage, "type": category, "sqlstate": _state(error)}, separators=(",", ":")
        )


def run_reported(operation: Callable[[RunnerTrace], object]) -> int:
    trace = RunnerTrace()
    try:
        operation(trace)
    except Exception as error:  # noqa: BLE001 -- never allow raw runner exceptions as output
        print("PHASE_A_FAILURE=" + trace.failure(error), flush=True)
        return 1
    print("PHASE_A_EXIT=0", flush=True)
    return 0
