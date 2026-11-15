"""Actual ACL test bodies against closed catalog doubles, NOT PostgreSQL proof.

Only two source-named, hash-authenticated functions are compiled; their modules,
fixtures, URL configuration and skip marks are never imported or changed. The
quota anchor is the original function, allowing exactly the reviewed owner line.
Changes beyond that relationship require deliberate source/anchor review.
"""

import ast
import hashlib
import os
import re
import socket
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
import sqlalchemy
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.sql.elements import TextClause

ROOT = Path(__file__).resolve().parents[3]
QUOTA = ROOT / "backend/tests/auth/test_quota.py"
NATIVE = ROOT / "backend/tests/documents/test_delegation_database.py"
QUOTA_NAME = "test_gate_and_mirror_privileges_are_exact"
NATIVE_NAME = "test_acl_isolation_fixed_definer_and_no_raw_job_visibility_widening"
QUOTA_ANCHOR = "537e954b3367b1e6ba5316ecbe04e7ba05e8374b026eed8759e321e92ff79e61"
NATIVE_ANCHOR = "a653e52c78d55f241a5341fad7d3a14111553a445bb27f23c028afa7c0a2eb91"
OWNER = "notebook_text_dispatch_owner"
SCHEDULER = "notebook_text_scheduler"
OWNER_LINE = '                    "notebook_text_dispatch_owner",\n'
ROLES = sorted(
    (
        "notebook_app",
        "notebook_membership",
        "notebook_document_lifecycle",
        "notebook_upload_finalize",
        "notebook_upload_io_owner",
        OWNER,
    )
)
COLUMNS = ("job_state", "job_attempts", "job_lease_token", "job_lease_expires_at", "work_cancelled")
TABLES = (
    "text_job_tickets",
    "upload_intents",
    "app_admissions",
    "workspace_members",
    "documents",
    "document_versions",
    "ingestion_jobs",
)
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
SIGNATURES = (
    "claim_text_job(integer)",
    "validate_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid)",
    "renew_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid,integer)",
    "ack_text_job(uuid,uuid,uuid,uuid,uuid,text,uuid)",
)
ACTOR, EDITOR, JOB, URL_SENTINEL = object(), object(), object(), object()


class ForbiddenIO(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    attempts = []

    def deny(*_args, **_kwargs):
        attempts.append(1)
        raise ForbiddenIO("local_io_denied")

    targets = (
        (socket, "socket"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
        (socket, "gethostbyname"),
        (subprocess, "Popen"),
        (subprocess, "run"),
        (os, "system"),
        (os, "fork"),
        (os, "posix_spawn"),
        (psycopg, "connect"),
        (psycopg.Connection, "connect"),
        (psycopg.AsyncConnection, "connect"),
        (sqlalchemy, "create_engine"),
        (Engine, "connect"),
        (Engine, "raw_connection"),
    )
    for target, attribute in targets:
        monkeypatch.setattr(target, attribute, deny)
    yield targets, attempts
    assert not attempts, "unexpected_external_io"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def source_function(path, name):
    source = path.read_text()
    assert len(source) < 100_000
    nodes = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(nodes) == 1
    node = nodes[0]
    segment = ast.get_source_segment(source, node)
    assert isinstance(segment, str) and len(segment) < 16_000
    assert not node.decorator_list
    if path == QUOTA:
        assert segment.count(OWNER_LINE) in (0, 1)
        assert digest(segment.replace(OWNER_LINE, "")) == QUOTA_ANCHOR
    else:
        assert path == NATIVE and digest(segment) == NATIVE_ANCHOR
    return node


def actual_function(path, name, model):
    node = source_function(path, name)
    namespace = {
        "__builtins__": {"sorted": sorted},
        "psycopg": SimpleNamespace(connect=model.connect),
        "URL": URL_SENTINEL,
        "app": model.app,
        "text": text,
        "perform": model.perform,
    }
    # Trusted bounded source only; the autouse deny fixture is active before this seam.
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class Result:
    def __init__(self, method, value):
        self.method, self.value = method, value

    def fetchall(self):
        assert self.method == "fetchall"
        return self.value

    def fetchone(self):
        assert self.method == "fetchone"
        return self.value

    def scalar_one(self):
        assert self.method == "scalar_one"
        return self.value

    def first(self):
        assert self.method == "first"
        return self.value


class Script:
    """Ordered statements/accessors/bindings, with balanced synthetic contexts."""

    def __init__(self, path, name):
        node = source_function(path, name)
        calls = sorted(
            (
                n
                for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "execute"
            ),
            key=lambda n: n.lineno,
        )
        self.queries, self.lines = [], []
        assertions = [n for n in ast.walk(node) if isinstance(n, ast.Assert)]
        for call in calls:
            argument = call.args[0]
            if isinstance(argument, ast.Call):
                argument = argument.args[0]
            self.queries.append(ast.literal_eval(argument))
            containing = [n for n in assertions if call in list(ast.walk(n))]
            self.lines.append(containing[0].test.lineno if containing else None)
        self.steps = []
        self.position = 0
        self.events = []
        self.active = None
        self.failure_at: int | None = None
        self.error: Exception | None = None
        self.performed = False

    def add(self, session, query, params, method, value):
        self.steps.append((session, query, params, method, value))

    @contextmanager
    def session(self, kind):
        assert self.active is None
        self.active = kind
        self.events.append((kind, "enter"))
        try:
            yield self
        finally:
            self.events.append((kind, "exit"))
            self.active = None

    def connect(self, url):
        assert url is URL_SENTINEL
        return self.session("admin")

    def app(self, actor):
        assert actor is ACTOR
        return self.session("app")

    def actor_tx(self, actor):
        assert actor is ACTOR or actor is EDITOR
        return self.session("actor" if actor is ACTOR else "editor")

    def perform(self, scene):
        assert scene["actor"] is ACTOR and not self.performed
        self.performed = True
        return SimpleNamespace(job=SimpleNamespace(id=JOB))

    def execute(self, statement, *args):
        session, query, params, method, value = self.steps[self.position]
        assert self.active == session
        if session == "admin":
            assert type(statement) is str
            sql = statement
        else:
            assert isinstance(statement, TextClause)
            sql = statement.text
            assert set(statement._bindparams) == set(params or {})
        assert sql == self.queries[query]
        assert args == (() if params is None else (params,))
        index = self.position
        self.position += 1
        if index == self.failure_at:
            assert self.error is not None
            raise self.error
        return Result(method, value)

    def balanced(self):
        assert self.active is None
        assert len(self.events) % 2 == 0
        for enter, exit_ in zip(self.events[::2], self.events[1::2], strict=True):
            assert enter[0] == exit_[0] and enter[1] == "enter" and exit_[1] == "exit"

    def run(self, path, name, argument):
        try:
            actual_function(path, name, self)(argument)
        finally:
            self.balanced()


def quota_script():
    model = Script(QUOTA, QUOTA_NAME)
    model.add("admin", 0, None, "fetchall", [(role,) for role in ROLES])
    model.add("admin", 1, None, "fetchone", (False,) * 4)
    for column in COLUMNS:
        model.add("app", 2, {"c": column}, "scalar_one", False)
    return model


def rejected(model, path, name, argument, index):
    with pytest.raises(AssertionError) as caught:
        model.run(path, name, argument)
    trace = caught.value.__traceback__
    actual_lines = []
    last_function = None
    while trace is not None:
        last_function = trace.tb_frame.f_code.co_name
        if last_function == name:
            actual_lines.append(trace.tb_lineno)
        trace = trace.tb_next
    assert last_function == name
    # A failed double/query/binding contract is NOT an accepted ACL rejection.
    expected_line = model.lines[model.steps[index][1]]
    if path == QUOTA and index == 0:
        expected_line = next(
            n.lineno
            for n in ast.walk(source_function(path, name))
            if isinstance(n, ast.Assert)
            and isinstance(n.test, ast.Compare)
            and isinstance(n.test.left, ast.Name)
            and n.test.left.id == "roles"
        )
    assert actual_lines == [expected_line]
    assert model.position == index + 1


def replace_value(model, index, value):
    session, query, params, method, _ = model.steps[index]
    model.steps[index] = (session, query, params, method, value)


def test_gate_and_mirror_privileges_are_exact_accepts_six():
    model = quota_script()
    model.run(QUOTA, QUOTA_NAME, (ACTOR,))
    assert model.position == 7 and len(model.events) == 12


@pytest.mark.parametrize(
    "scenario",
    [
        "missing_owner",
        "missing_old_owner",
        "foreign",
        "effective_inherited",
        "scheduler",
        "duplicate",
        "order",
    ],
)
def test_gate_and_mirror_privileges_are_exact_rejects_roles(scenario):
    model = quota_script()
    roles = ROLES.copy()
    if scenario.startswith("missing"):
        roles.remove(OWNER if scenario == "missing_owner" else "notebook_upload_io_owner")
    elif scenario == "order":
        roles.reverse()
    else:
        roles = sorted(
            (
                *roles,
                {
                    "foreign": "foreign_role",
                    "effective_inherited": "inheriting_role",
                    "scheduler": SCHEDULER,
                    "duplicate": OWNER,
                }[scenario],
            )
        )
    replace_value(model, 0, [(role,) for role in roles])
    rejected(model, QUOTA, QUOTA_NAME, (ACTOR,), 0)


@pytest.mark.parametrize("index", range(4))
def test_gate_and_mirror_privileges_are_exact_rejects_privileges(index):
    model = quota_script()
    replace_value(model, 1, tuple(i == index for i in range(4)))
    rejected(model, QUOTA, QUOTA_NAME, (ACTOR,), 1)


@pytest.mark.parametrize("column", COLUMNS)
def test_gate_and_mirror_privileges_are_exact_rejects_columns(column):
    model = quota_script()
    index = COLUMNS.index(column) + 2
    replace_value(model, index, True)
    rejected(model, QUOTA, QUOTA_NAME, (ACTOR,), index)


@pytest.mark.parametrize("index", range(7))
@pytest.mark.parametrize("kind", ["query", "driver"])
def test_gate_and_mirror_privileges_are_exact_propagates_errors(index, kind):
    model = quota_script()
    model.failure_at = index
    error = (
        RuntimeError("synthetic_query_failure") if kind == "query" else psycopg.OperationalError()
    )
    model.error = error
    with pytest.raises(type(error)) as caught:
        model.run(QUOTA, QUOTA_NAME, (ACTOR,))
    assert caught.value is error and model.position == index + 1


def migration_columns():
    """Independent fixed 0007 grant specification, not native-test expectations."""
    sql = (ROOT / "backend/migrations/versions/0007_text_dispatch.sql").read_text()
    grants = re.findall(
        r"GRANT SELECT\(([^)]+)\)\s+ON public\.(\w+) TO notebook_text_dispatch_owner;", sql
    )
    assert len(grants) == 6
    reads = {table: sorted(re.findall(r"\w+", columns)) for columns, table in grants}
    ticket = re.search(r"CREATE TABLE public.text_job_tickets \((.*?)\n\);", sql, re.DOTALL)
    assert ticket is not None
    reads["text_job_tickets"] = sorted(
        re.findall(r"^ (\w+) (?:uuid|text|timestamptz)\b", ticket[1], re.MULTILINE)
    )
    update = re.search(
        r"GRANT UPDATE\(([^)]+)\)\s+ON public.text_job_tickets TO notebook_text_dispatch_owner;",
        sql,
    )
    assert update is not None and set(reads) == set(TABLES)
    assert "GRANT SELECT,INSERT ON public.text_job_tickets TO notebook_text_dispatch_owner;" in sql
    return reads, sorted(re.findall(r"\w+", update[1]))


def native_script(grant_option=None):
    model = Script(NATIVE, NATIVE_NAME)
    assert grant_option in (None, "table", "column")
    model.add("scheduler", 0, None, "scalar_one", True)
    for table in ("text_job_tickets", "ingestion_jobs", "upload_intents", "documents"):
        model.add("scheduler", 1, {"t": "public." + table}, "scalar_one", False)
    model.add("scheduler", 2, None, "scalar_one", False)
    model.add("editor", 3, {"j": JOB}, "first", None)
    for role in (OWNER, SCHEDULER):
        model.add(
            "admin",
            4,
            (role,),
            "fetchone",
            (role == SCHEDULER, False, False, False, False, False, False),
        )
        model.add("admin", 5, (role,), "fetchone", (0,))
        model.add("admin", 6, (role,) * 3, "fetchone", (True, False, role == OWNER))
    for signature in SIGNATURES:
        model.add(
            "admin",
            7,
            ("public." + signature,),
            "fetchone",
            (OWNER, True, ["search_path=pg_catalog"]),
        )
        for role in ("notebook_app", "notebook_storage_io"):
            model.add("admin", 8, (role, "public." + signature), "fetchone", (False,))
    model.add("admin", 9, None, "fetchone", (False,))
    reads, updates = migration_columns()
    for role in (OWNER, SCHEDULER):
        for table in TABLES:
            ticket_owner = role == OWNER and table == "text_job_tickets"
            for privilege in TABLE_PRIVILEGES:
                allowed = ticket_owner and privilege in ("SELECT", "INSERT")
                model.add("admin", 10, (role, "public." + table, privilege), "fetchone", (allowed,))
                model.add(
                    "admin",
                    11,
                    (role, "public." + table, privilege + " WITH GRANT OPTION"),
                    "fetchone",
                    (grant_option == "table" and ticket_owner and privilege == "SELECT",),
                )
            for privilege in COLUMN_PRIVILEGES:
                columns = []
                if role == OWNER and (
                    privilege == "SELECT" or (ticket_owner and privilege == "INSERT")
                ):
                    columns = reads[table]
                elif ticket_owner and privilege == "UPDATE":
                    columns = updates
                model.add(
                    "admin",
                    12,
                    ("public." + table, role, privilege),
                    "fetchall",
                    [(c,) for c in columns],
                )
                model.add(
                    "admin",
                    13,
                    ("public." + table, role, privilege + " WITH GRANT OPTION"),
                    "fetchall",
                    [("user_id",)]
                    if grant_option == "column"
                    and role == OWNER
                    and table == "app_admissions"
                    and privilege == "SELECT"
                    else [(c,) for c in reads[table]]
                    if grant_option == "table" and ticket_owner and privilege == "SELECT"
                    else [],
                )
    model.add("actor", 14, None, "scalar_one", False)
    return model


def native_argument(model):
    return {
        "actor": ACTOR,
        "editor": EDITOR,
        "actor_tx": model.actor_tx,
        "scheduler_tx": lambda: model.session("scheduler"),
    }


def test_acl_isolation_fixed_definer_accepts_exact_catalog():
    model = native_script()
    model.run(NATIVE, NATIVE_NAME, native_argument(model))
    assert model.position == len(model.steps) == 335
    assert model.performed and len(model.events) == 8


@pytest.mark.parametrize("role", [OWNER, SCHEDULER])
@pytest.mark.parametrize("attribute", range(7))
def test_acl_isolation_fixed_definer_rejects_role_attributes(role, attribute):
    model = native_script()
    index = next(i for i, step in enumerate(model.steps) if step[1:3] == (4, (role,)))
    values = list(model.steps[index][4])
    values[attribute] = not values[attribute]
    replace_value(model, index, tuple(values))
    rejected(model, NATIVE, NATIVE_NAME, native_argument(model), index)


@pytest.mark.parametrize("role", [OWNER, SCHEDULER])
@pytest.mark.parametrize("direction", ["member", "roleid", "cross_role"])
def test_acl_isolation_fixed_definer_rejects_membership_edges(role, direction):
    model = native_script()
    index = next(i for i, step in enumerate(model.steps) if step[1:3] == (5, (role,)))
    # Synthetic pg_auth_members join result; SQL must cover both edge endpoints.
    query = model.queries[5]
    assert "r.oid=m.roleid OR r.oid=m.member" in query and direction
    replace_value(model, index, (1,))
    rejected(model, NATIVE, NATIVE_NAME, native_argument(model), index)


@pytest.mark.parametrize("role", [OWNER, SCHEDULER])
@pytest.mark.parametrize("attribute", ["usage", "create", "gate"])
def test_acl_isolation_fixed_definer_rejects_schema_and_gate(role, attribute):
    model = native_script()
    index = next(i for i, step in enumerate(model.steps) if step[1:3] == (6, (role,) * 3))
    values = list(model.steps[index][4])
    position = ("usage", "create", "gate").index(attribute)
    values[position] = not values[position]
    replace_value(model, index, tuple(values))
    rejected(model, NATIVE, NATIVE_NAME, native_argument(model), index)


@pytest.mark.parametrize("signature", SIGNATURES)
@pytest.mark.parametrize("attribute", ["owner", "definer", "path"])
def test_acl_isolation_fixed_definer_rejects_routine_authority(signature, attribute):
    model = native_script()
    index = next(
        i for i, step in enumerate(model.steps) if step[1:3] == (7, ("public." + signature,))
    )
    values = list(model.steps[index][4])
    position = ("owner", "definer", "path").index(attribute)
    values[position] = {"owner": SCHEDULER, "definer": False, "path": ["search_path=public"]}[
        attribute
    ]
    replace_value(model, index, tuple(values))
    rejected(model, NATIVE, NATIVE_NAME, native_argument(model), index)


@pytest.mark.parametrize("role", [OWNER, SCHEDULER])
@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("privilege", TABLE_PRIVILEGES)
def test_acl_isolation_fixed_definer_rejects_table_grant_drift(role, table, privilege):
    model = native_script()
    index = next(
        i
        for i, step in enumerate(model.steps)
        if step[1:3] == (10, (role, "public." + table, privilege))
    )
    replace_value(model, index, (not model.steps[index][4][0],))
    rejected(model, NATIVE, NATIVE_NAME, native_argument(model), index)


@pytest.mark.parametrize("role", [OWNER, SCHEDULER])
@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("privilege", COLUMN_PRIVILEGES)
def test_acl_isolation_fixed_definer_rejects_column_grant_drift(role, table, privilege):
    model = native_script()
    index = next(
        i
        for i, step in enumerate(model.steps)
        if step[1:3] == (12, ("public." + table, role, privilege))
    )
    value = model.steps[index][4]
    replace_value(model, index, sorted([*value, ("unexpected_column",)]))
    rejected(model, NATIVE, NATIVE_NAME, native_argument(model), index)


@pytest.mark.parametrize(
    "table,privilege",
    [(table, "SELECT") for table in TABLES]
    + [("text_job_tickets", "INSERT"), ("text_job_tickets", "UPDATE")],
)
def test_acl_isolation_fixed_definer_rejects_missing_required_columns(table, privilege):
    model = native_script()
    index = next(
        i
        for i, step in enumerate(model.steps)
        if step[1:3] == (12, ("public." + table, OWNER, privilege))
    )
    value = model.steps[index][4]
    assert value
    replace_value(model, index, value[1:])
    rejected(model, NATIVE, NATIVE_NAME, native_argument(model), index)


def test_deny_guards_are_live(no_external_io):
    targets, attempts = no_external_io
    for target, attribute in targets:
        with pytest.raises(ForbiddenIO):
            getattr(target, attribute)()
    assert len(attempts) == len(targets) == 15
    attempts.clear()  # Only these intentional probes, never execution attempts.


@pytest.mark.parametrize("scope", ["table", "column"])
def test_acl_isolation_fixed_definer_rejects_allowed_grant_option(scope):
    model = native_script(grant_option=scope)
    ordinary = (
        (OWNER, "public.text_job_tickets", "SELECT")
        if scope == "table"
        else ("public.app_admissions", OWNER, "SELECT")
    )
    allowed = next(step for step in model.steps if step[2] == ordinary)
    assert allowed[4] == ((True,) if scope == "table" else [("user_id",)])
    option = (*ordinary[:-1], "SELECT WITH GRANT OPTION")
    index = next(i for i, step in enumerate(model.steps) if step[2] == option)
    assert model.steps[index][4] == ((True,) if scope == "table" else [("user_id",)])
    rejected(model, NATIVE, NATIVE_NAME, native_argument(model), index)
