"""No DB: exercise the actual ClientCursor compiler on Phase A's source query."""

import ast
from pathlib import Path

from psycopg import ClientCursor, ProgrammingError
from psycopg.adapt import Transformer


def test_protected_role_query_compiles_with_one_migrator_and_literal_percent():
    tree = ast.parse(Path(__file__).with_name("phase_a.py").read_text())
    queries = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and "rolname LIKE" in node.args[0].value
    ]
    assert len(queries) == 1, "expected_single_protected_role_query"
    migrator = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "MIGRATOR" for target in node.targets)
    )
    compiled = ClientCursor._query_cls(Transformer())
    failed = False
    try:
        compiled.convert(queries[0].encode(), (migrator,))
    except ProgrammingError:
        # Fixed assertion only: no raw source, driver error or query repr in RED output.
        failed = True
    assert not failed, "protected_role_query_client_compilation_failed"
    assert compiled.params is not None and len(compiled.params) == 1
    rendered = compiled.query
    assert type(rendered) is bytes, "expected_compiled_query_bytes"
    assert rendered.count(b"notebook_%") == 1, "expected_single_literal_percent"
    assert b"notebook_%%" not in rendered, "unexpected_doubled_server_percent"
    assert rendered.count(migrator.encode()) == 1, "expected_one_migrator_binding"
    assert b"%s" not in rendered, "unexpected_unbound_placeholder"
