"""Synthetic closed NoSuchKey protocol/use-site proofs, not native body evidence."""

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.documents.storage import ObjectScope

from backend.tests.storage_real import stage_b_contract as contract
from backend.tests.storage_real.test_stage_b_contract import config

CANONICAL = {
    "statusCode": "404",
    "error": "not_found",
    "code": "NoSuchKey",
    "message": "Object not found",
}
BODY = json.dumps(CANONICAL).encode()
BAD_BODIES = [
    b"",
    b"HTML_CANARY",
    b"{",
    b"[]",
    b"null",
    b"\xff",
    json.dumps(dict(CANONICAL, code="AccessDenied")).encode(),
    json.dumps(dict(CANONICAL, code="NoSuchBucket")).encode(),
    json.dumps(dict(CANONICAL, code="InvalidKey")).encode(),
    json.dumps(dict(CANONICAL, statusCode=404)).encode(),
    json.dumps(dict(CANONICAL, statusCode="403")).encode(),
    json.dumps(dict(CANONICAL, error="InvalidJWT")).encode(),
    json.dumps(dict(CANONICAL, message="BODY_CANARY")).encode(),
    json.dumps(dict(CANONICAL, extra="BODY_CANARY")).encode(),
    json.dumps(dict(CANONICAL, code={"nested": "BODY_CANARY"})).encode(),
    json.dumps({key: value for key, value in CANONICAL.items() if key != "code"}).encode(),
    BODY[:-1] + b',"code":"NoSuchKey"}',
    b" " * 16385,
    b"[" * 1100 + b"0" + b"]" * 1100,
    json.dumps({key: value for key, value in CANONICAL.items() if key != "message"}).encode(),
]


@pytest.mark.parametrize("status", [400, 404])
def test_actual_predicate_requires_literal_upstream_canonical_absence(status, capsys):
    assert contract.canonical_absence(contract.Reply(status, BODY)) is True
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("body", BAD_BODIES)
@pytest.mark.parametrize("status", [400, 404])
def test_actual_predicate_rejects_raw_route_auth_malformed_and_canary_bodies(status, body, capsys):
    assert contract.canonical_absence(contract.Reply(status, body)) is False
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("status", [200, 204, 401, 403, 500, True, 400.0])
def test_actual_predicate_rejects_wrong_wire_status_even_with_canonical_body(status):
    assert contract.canonical_absence(contract.Reply(status, BODY)) is False


@pytest.mark.parametrize(
    "delete_status,delete_body,get_status,get_body,success",
    [
        (200, b"", 400, BODY, True),
        (204, b"", 404, BODY, True),
        (400, BODY, 400, BODY, True),
        (404, BODY, 404, BODY, True),
        (400, b"BODY_CANARY", 400, BODY, False),
        (404, b"BODY_CANARY", 400, BODY, False),
        (403, BODY, 400, BODY, False),
        (500, BODY, 400, BODY, False),
        (200, b"", 404, b"BODY_CANARY", False),
        (200, b"", 400, b"BODY_CANARY", False),
        (200, b"", 200, b"BODY_CANARY", False),
        (200, b"", 403, BODY, False),
    ]
    + [(200, b"", 400, bad, False) for bad in BAD_BODIES]
    + [(400, bad, 400, BODY, False) for bad in BAD_BODIES]
    + [(200, b"", bad, BODY, False) for bad in (200, 204, 401, 403, 500)]
    + [(bad, BODY, 400, BODY, False) for bad in (401, 403, 500)],
)
def test_actual_delete_claimed_never_reaches_finish_after_noncanonical_absence(
    delete_status, delete_body, get_status, get_body, success, capsys
):
    scope = ObjectScope(*(uuid4() for _ in range(4)))
    finish = []
    methods = []

    def respond(request):
        methods.append(request.method)
        if request.method == "DELETE":
            return httpx.Response(delete_status, content=delete_body)
        return httpx.Response(get_status, content=get_body)

    async def operation():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            await contract.delete_claimed(
                client, config(), SimpleNamespace(storage_key=scope.key), scope
            )
            finish.append(True)

    if success:
        asyncio.run(operation())
    else:
        with pytest.raises(ValueError):
            asyncio.run(operation())
    assert finish == ([True] if success else [])
    if success:
        assert methods == ["DELETE", "GET"]
    output = capsys.readouterr()
    assert not output.err
    assert all(
        value not in output.out
        for value in ("BODY_CANARY", "SYNTHETIC_CANARY", "Object not found", "NoSuchKey", scope.key)
    )


def test_actual_runner_and_cleanup_use_same_predicate_at_trusted_boundaries():
    base = Path(__file__).parent
    runner = ast.parse((base / "stage_b.py").read_text())
    verify = next(
        node
        for node in runner.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "verify"
    )
    calls = [
        node
        for node in ast.walk(verify)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "canonical_absence"
    ]
    assert len(calls) == 1 and len(calls[0].args) == 1
    awaited = calls[0].args[0]
    assert isinstance(awaited, ast.Await) and isinstance(awaited.value, ast.Call)
    request = awaited.value
    assert isinstance(request.func, ast.Name) and request.func.id == "request"
    assert isinstance(request.args[1], ast.Constant) and request.args[1].value == "GET"
    assert any(
        key.arg == "headers" and isinstance(key.value, ast.Name) and key.value.id == "headers"
        for key in request.keywords
    )
    cleanup = ast.parse((base / "stage_b_contract.py").read_text())
    delete = next(
        node
        for node in cleanup.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "delete_claimed"
    )
    assert (
        len(
            [
                node
                for node in ast.walk(delete)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "canonical_absence"
            ]
        )
        == 2
    )
