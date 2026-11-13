"""No DB checks of the actual packet's query compiler and existing integrity gate."""

import ast
import asyncio
import re
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from psycopg import ClientCursor, ProgrammingError
from psycopg.adapt import Transformer


def test_actual_prepare_insert_columns_exist_in_actual_foundation_migration():
    base = Path(__file__)
    migration = ast.parse((base.parents[2] / "migrations/versions/0001_foundation.py").read_text())
    schema = {}
    for node in ast.walk(migration):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
            and node.func.attr == "create_table"
        ):
            continue
        table = ast.literal_eval(node.args[0])
        schema[table] = {
            ast.literal_eval(column.args[0])
            for column in node.args[1:]
            if isinstance(column, ast.Call)
            and isinstance(column.func, ast.Attribute)
            and column.func.attr == "Column"
        }
    fixture = ast.parse(base.with_name("stage_b.py").read_text())
    prepare = next(
        node
        for node in fixture.body
        if isinstance(node, ast.FunctionDef) and node.name == "prepare"
    )
    seen = set()
    for node in ast.walk(prepare):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and type(node.args[0].value) is str
        ):
            continue
        sql = node.args[0].value
        if not sql.startswith("INSERT "):
            continue
        matched = re.match(r"INSERT INTO public\.([a-z_]+)\(([^)]+)\) VALUES ", sql)
        assert matched is not None, "unrecognized_fixture_insert"
        table, raw_columns = matched.groups()
        columns = raw_columns.split(",")
        assert len(columns) == len(set(columns)), "duplicate_fixture_insert_column"
        assert table in schema and set(columns) <= schema[table], (
            "fixture_column_missing_from_actual_migration"
        )
        seen.add(table)
    assert seen == {"app_admissions", "workspaces", "notebooks", "workspace_members"}


def test_actual_packet_client_queries_compile_and_sqlalchemy_text_is_literal():
    tree = ast.parse(Path(__file__).with_name("stage_b.py").read_text())
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "text":
            assert (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and type(node.args[0].value) is str
            ), "nonliteral_sqlalchemy_query"
        if not (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and type(node.args[0].value) is str
        ):
            continue
        count += 1
        parameters = None
        if len(node.args) > 1:
            assert isinstance(node.args[1], ast.Tuple), "unexpected_parameter_shape"
            parameters = tuple("synthetic" for _ in node.args[1].elts)
        compiled = ClientCursor._query_cls(Transformer())
        invalid = False
        try:
            compiled.convert(node.args[0].value.encode(), parameters)
        except ProgrammingError:
            invalid = True
        assert not invalid, "packet_client_placeholder_failure"
        if parameters is not None:
            assert compiled.params is not None and len(compiled.params) == len(parameters)
    assert count >= 20, "missing_actual_packet_queries"


@pytest.mark.parametrize("corrupted", [b"bad", b"jello\n"])
def test_synthetic_readback_corruption_never_confirms_or_finalizes(monkeypatch, corrupted):
    # Entire DB/admission/store prelude is SYNTHETIC, not native Storage/RLS evidence.
    # No synthetic receipt is manufactured: both receipt and finalize calls are traps.
    from app.documents import service as module
    from app.documents.storage import ObjectScope
    from app.ingestion.text import TextUpload

    actor, notebook, workspace, document, version, intent_id, token = [uuid4() for _ in range(7)]
    scope = ObjectScope(workspace, notebook, document, version)
    body = b"hello\n"
    upload = TextUpload("sample.txt", "text/plain", body, body.decode(), sha256(body).hexdigest())
    intent = SimpleNamespace(
        id=intent_id,
        workspace_id=workspace,
        notebook_id=notebook,
        document_id=document,
        version_id=version,
        storage_key=scope.key,
        state="reserved",
    )
    claim = SimpleNamespace(
        storage_key=scope.key,
        sha256=upload.sha256,
        byte_count=len(body),
        media_type=upload.media_type,
        token=token,
    )
    monkeypatch.setattr(module, "reserve_upload", lambda *a, **kw: intent)
    monkeypatch.setattr(module, "claim_upload_io", lambda *a, **kw: claim)
    monkeypatch.setattr(module, "check_upload_admission", lambda *a, **kw: True)
    confirm = Mock(side_effect=AssertionError("unexpected_receipt"))
    finalize = Mock(side_effect=AssertionError("unexpected_finalize"))
    monkeypatch.setattr(module, "confirm_upload_io", confirm)
    monkeypatch.setattr(module, "finalize_upload", finalize)
    actions = []

    class CorruptStore:
        async def put(self, actor_id, scope, data, media_type):
            actions.append("put")

        async def read(self, actor_id, scope):
            actions.append("read")
            return corrupted

    service = module.UploadService(store_factory=lambda auth: CorruptStore())
    service.configured = True
    monkeypatch.setattr(service, "_db", lambda actor, operation, **kw: operation(None))
    monkeypatch.setattr(service, "_notebook", lambda *a, **kw: None)

    async def operation():
        with pytest.raises(module.DocumentError) as caught:
            await service.upload(
                actor_id=actor,
                notebook_id=notebook,
                idempotency_key="synthetic-integrity",
                upload=upload,
            )
        assert caught.value.code == "original_integrity_failed" and caught.value.status_code == 502

    asyncio.run(operation())
    assert actions == ["put", "read"]
    confirm.assert_not_called()
    finalize.assert_not_called()
