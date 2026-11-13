"""Closed ACL checkpoints, actual reporter, and unchanged original assertion AST."""

import ast
import json
from hashlib import sha256
from pathlib import Path

import pytest

BASE = Path(__file__).parent
# Exact post_storage_acl AST at immutable 0ccc02d, before instrumentation.
ORIGINAL_AST = "43951163876e38dd329fa5dc96de0bd5840087eb408e460b58f928a8fc850c70"


def domain(filename):
    tree = ast.parse((BASE / filename).read_text())
    values = [
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "ACL_CHECKPOINTS"
            for target in node.targets
        )
    ]
    assert len(values) == 1, "missing_literal_checkpoint_domain"
    result = values[0]
    assert type(result) is tuple and len(result) == 21 and len(set(result)) == 21
    assert all(type(value) is str for value in result)
    return result


def test_runner_and_operator_domains_are_same_closed_literals():
    assert domain("stage_b.py") == domain("stage_b_launch.py")


def test_actual_operator_checkpoint_parser_and_old_failure_are_closed():
    from backend.tests.storage_real.stage_b_launch import safe_line

    assert safe_line("STAGE_B_ACL_CHECKPOINT=catalog_relation_ids") == {
        "acl_checkpoint": "catalog_relation_ids"
    }
    for suffix in ("CANARY", "", "catalog_relation_ids CANARY", "catalog_relation_ids\nCANARY"):
        assert safe_line("STAGE_B_ACL_CHECKPOINT=" + suffix) is None
    failure = {"stage": "post_storage_acl", "type": "assertion", "sqlstate": "unknown"}
    assert safe_line("STAGE_B_FAILURE=" + json.dumps(failure)) == {"failure": failure}
    assert safe_line("STAGE_B_FAILURE=" + json.dumps(dict(failure, checkpoint="CANARY"))) is None


def test_actual_runner_emitter_rejects_before_output(capsys):
    from backend.tests.storage_real import stage_b

    stage_b.acl_checkpoint("catalog_relation_ids")
    assert capsys.readouterr().out == "STAGE_B_ACL_CHECKPOINT=catalog_relation_ids\n"
    for unknown in ("CANARY", "catalog_relation_ids\nCANARY", None, 42):
        with pytest.raises(AssertionError):
            stage_b.acl_checkpoint(unknown)
        captured = capsys.readouterr()
        assert captured.out == captured.err == ""


def test_actual_assertion_function_is_identical_after_stripping_only_literal_checkpoints():
    tree = ast.parse((BASE / "stage_b.py").read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "post_storage_acl"
    )
    checkpoints = []
    inventories = []

    class Strip(ast.NodeTransformer):
        def visit_Expr(self, node):
            call = node.value
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "migration_inventory"
            ):
                assert not call.keywords and len(call.args) == 2
                assert [arg.id if isinstance(arg, ast.Name) else None for arg in call.args] == [
                    "migrations",
                    "source",
                ]
                inventories.append(call)
                return None
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "acl_checkpoint"
            ):
                assert (
                    len(call.args) == 1
                    and not call.keywords
                    and isinstance(call.args[0], ast.Constant)
                )
                checkpoints.append(call.args[0].value)
                return None
            return self.generic_visit(node)

    stripped = Strip().visit(function)
    assert len(inventories) == 1
    assert len(checkpoints) == 21 and set(checkpoints) == set(domain("stage_b.py"))
    assert sha256(ast.dump(stripped, include_attributes=False).encode()).hexdigest() == ORIGINAL_AST
