"""Local ASGI adapter checks; synthetic services are NOT real storage evidence."""

import asyncio
import importlib
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def exchange(module, *, headers=(), content=b"payload", notebook=None):
    app = FastAPI()
    app.include_router(module.router)
    reads = []

    async def stream():
        reads.append(True)
        yield content

    async def run():
        async with AsyncClient(transport=ASGITransport(app), base_url="https://test") as client:
            return await client.post(
                f"/api/notebooks/{notebook or uuid4()}/documents",
                headers=headers,
                content=stream(),
            )

    return asyncio.run(run()), reads


HEADERS = [
    ("Origin", "https://test"),
    ("Cookie", "__Host-notebook_csrf=test"),
    ("X-CSRF-Token", "test"),
    ("Idempotency-Key", "synthetic-key"),
    ("Content-Type", "multipart/form-data; boundary=fixture"),
]
BODY = (
    b'--fixture\r\nContent-Disposition: form-data; name="file"; filename="note.txt"\r\n'
    b"Content-Type: text/plain\r\n\r\nSynthetic text.\r\n--fixture--\r\n"
)


@pytest.fixture
def opened(monkeypatch):
    from app.auth.supabase import Identity
    from app.documents import upload_router as module
    from app.documents.service import DocumentDTO, JobDTO, UploadReceipt

    user = Identity(id=uuid4(), email="synthetic@example.test")
    calls = []
    notebook = uuid4()
    receipt = UploadReceipt(
        document=DocumentDTO(
            id=uuid4(),
            notebook_id=notebook,
            filename="note.txt",
            version_id=uuid4(),
            status="queued",
            error_code="not_processed",
        ),
        job=JobDTO(id=uuid4(), status="queued"),
    )

    class Service:
        def authorize_upload(self, **kwargs):
            assert kwargs == {"actor_id": user.id, "notebook_id": notebook}
            calls.append("authorize")

        async def upload(self, **kwargs):
            assert kwargs["actor_id"] == user.id
            assert kwargs["notebook_id"] == notebook
            assert kwargs["idempotency_key"] == "synthetic-key"
            assert kwargs["upload"].data == b"Synthetic text."
            calls.append("upload")
            await asyncio.sleep(0)
            calls.append("synthetic committed")
            return receipt

    service = Service()

    async def verified(request):
        calls.append("auth")
        return user

    def factory(original):
        assert original is user
        calls.append("service")
        return service

    monkeypatch.setattr(module, "capabilities", lambda: {"documents": True})
    monkeypatch.setattr(module, "identity", verified)
    monkeypatch.setattr(
        module, "get_settings", lambda: SimpleNamespace(public_origin="https://test")
    )
    monkeypatch.setattr(module, "upload_service", factory)
    return SimpleNamespace(
        module=module, user=user, notebook=notebook, receipt=receipt, calls=calls, service=service
    )


def test_success_is_typed_receipt_after_awaited_service(opened):
    response, reads = exchange(
        opened.module, headers=HEADERS, content=BODY, notebook=opened.notebook
    )
    assert response.status_code == 202
    assert response.json() == opened.receipt.model_dump(mode="json")
    assert reads == [True]
    assert opened.calls == ["auth", "service", "authorize", "upload", "synthetic committed"]


@pytest.mark.parametrize(
    "keys",
    [
        [],
        [""],
        ["x" * 129],
        ["space key"],
        ["comma,key"],
        ["synthetic-key", "other"],
        [b"nonascii-\xe9"],
    ],
)
def test_invalid_idempotency_is_rejected_before_body(opened, keys):
    headers = [(k, v) for k, v in HEADERS if k != "Idempotency-Key"]
    headers += [("Idempotency-Key", key) for key in keys]
    response, reads = exchange(
        opened.module, headers=headers, content=BODY, notebook=opened.notebook
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_idempotency_key"
    assert reads == []
    assert opened.calls == ["auth", "service", "authorize"]


@pytest.mark.parametrize(
    "payload,expected",
    [
        (BODY.replace(b"text/plain", b"application/pdf"), (415, "unsupported_media")),
        (BODY.replace(b'name="file"', b'name="connection_id"'), (400, "invalid_upload")),
        (BODY.replace(b"--fixture--\r\n", b""), (400, "invalid_upload")),
        (BODY.replace(b"Synthetic text.", b"binary\x00"), (422, "invalid_text")),
        (BODY.replace(b"Synthetic text.", b"\xff"), (422, "invalid_text")),
        (BODY.replace(b"Synthetic text.", b""), (422, "empty_text")),
    ],
)
def test_invalid_multipart_never_calls_upload(opened, payload, expected):
    response, reads = exchange(
        opened.module, headers=HEADERS, content=payload, notebook=opened.notebook
    )
    assert (response.status_code, response.json()["detail"]["code"]) == expected
    assert reads == [True]
    assert opened.calls == ["auth", "service", "authorize"]


def test_actual_file_bytes_oversize_not_content_length(opened):
    payload = BODY.replace(b"Synthetic text.", b"x" * (25 * 1024 * 1024 + 1))
    response, reads = exchange(
        opened.module,
        headers=HEADERS + [("Content-Length", "1")],
        content=payload,
        notebook=opened.notebook,
    )
    assert response.status_code == 413
    assert reads == [True]
    assert response.json()["detail"]["code"] == "upload_too_large"
    assert opened.calls == ["auth", "service", "authorize"]


@pytest.mark.parametrize(
    "code,expected",
    [
        ("quota_capacity", 429),
        ("upload_idempotency_conflict", 409),
        ("upload_timeout", 503),
        ("unsafe_database_role", 503),
        ("driver secret /private/path", 503),
    ],
)
def test_service_error_status_is_fixed_not_exception_status(opened, code, expected):
    from app.documents.service import DocumentError

    async def fail(**kwargs):
        raise DocumentError(code, 200)

    opened.service.upload = fail
    response, _ = exchange(opened.module, headers=HEADERS, content=BODY, notebook=opened.notebook)
    assert response.status_code == expected
    assert "driver secret" not in response.text
    assert "/private/path" not in response.text


@pytest.mark.parametrize("mode", ["subclass", "getter", "string", "dict", "unknown"])
def test_hostile_error_metadata_never_executes_or_escapes(opened, mode):
    from app.documents.service import DocumentError
    from fastapi import HTTPException

    touches = []

    class HostileError(DocumentError):
        def __getattribute__(self, name):
            touches.append(name)
            raise AssertionError("metadata access")

    class GetterError(DocumentError):
        @property
        def code(self):
            touches.append("getter")
            raise AssertionError("getter access")

    class HostileString(str):
        def __hash__(self):
            touches.append("hash")
            raise AssertionError("hash access")

        def __eq__(self, other):
            touches.append("equals")
            raise AssertionError("equals access")

    class HostileDict(dict):
        def get(self, *args):
            touches.append("dict")
            raise AssertionError("dict access")

    failures = {
        "subclass": HostileError("private canary"),
        "getter": BaseException.__new__(GetterError),
        "string": DocumentError(HostileString("private canary")),
        "dict": HTTPException(401, detail=HostileDict(code="private canary")),
        "unknown": RuntimeError("private canary"),
    }

    async def fail(**kwargs):
        raise failures[mode]

    opened.service.upload = fail
    response, _ = exchange(opened.module, headers=HEADERS, content=BODY, notebook=opened.notebook)
    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": "upload_unavailable", "message": "Upload vorübergehend nicht möglich."}
    }
    assert touches == []


@pytest.mark.parametrize("phase", ["parse", "upload"])
def test_cancellation_propagates_without_retry(opened, monkeypatch, phase):
    calls = []

    async def cancelled(*args, **kwargs):
        calls.append("cancel")
        raise asyncio.CancelledError

    if phase == "parse":
        monkeypatch.setattr(opened.module, "parse_text_form", cancelled)
    else:
        opened.service.upload = cancelled
    with pytest.raises(asyncio.CancelledError):
        exchange(opened.module, headers=HEADERS, content=BODY, notebook=opened.notebook)
    assert calls == ["cancel"]
    assert opened.calls == ["auth", "service", "authorize"]


@pytest.mark.parametrize("result", [None, {}, {"private_job": "canary"}])
def test_success_requires_existing_typed_receipt(opened, result):
    async def invalid(**kwargs):
        return result

    opened.service.upload = invalid
    response, _ = exchange(opened.module, headers=HEADERS, content=BODY, notebook=opened.notebook)
    assert response.status_code == 503
    assert "canary" not in response.text


def test_error_detail_with_hostile_key_cannot_execute(opened):
    from fastapi import HTTPException

    touched = []

    class Key:
        def __hash__(self):
            return hash("code")

        def __eq__(self, other):
            touched.append("equality")
            raise RuntimeError("private canary")

    error = HTTPException(401, detail={Key(): "private canary"})

    async def fail(**kwargs):
        raise error

    opened.service.upload = fail
    response, _ = exchange(opened.module, headers=HEADERS, content=BODY, notebook=opened.notebook)
    assert response.status_code == 503
    assert "canary" not in response.text
    assert touched == []


def test_original_identity_factory_is_fresh_and_rejects_actor_spoof(monkeypatch):
    from app.auth.supabase import Identity
    from app.documents import upload_router as module
    from app.documents.service import DocumentError

    user = Identity(id=uuid4(), email="synthetic@example.test")
    seen = []
    sessions = []

    @contextmanager
    def factory(original):
        assert original is user
        seen.append(original)
        db = object()
        sessions.append(db)
        yield db
        seen.append("committed")

    monkeypatch.setattr(module, "authenticated_session_factory", lambda: factory)
    service = module.upload_service(user)
    assert not service.configured and service.io_tx is None
    assert service.actor_tx is not None
    with service.actor_tx(user.id):
        pass
    with service.actor_tx(user.id):
        pass
    assert sessions[0] is not sessions[1]
    assert seen == [user, "committed", user, "committed"]
    with pytest.raises(DocumentError, match="authentication_required"), service.actor_tx(uuid4()):
        pytest.fail("spoof must not open")
    assert len(sessions) == 2


def test_real_service_preflight_and_upload_awaits_outside_synthetic_transactions(monkeypatch):
    """Real service flow, scripted DB/Storage doubles; NOT RLS/commit/real Storage proof."""
    from app.auth.supabase import Identity
    from app.documents import service as lifecycle
    from app.documents import upload_router as module
    from app.documents.storage import ObjectScope

    user = Identity(id=uuid4(), email="synthetic@example.test")
    workspace, notebook, document, version, job, intent_id, token = [uuid4() for _ in range(7)]
    scope = ObjectScope(workspace, notebook, document, version)
    active, events, sessions = [], [], []

    class DB:
        def execute(self, statement):
            query = str(statement)
            if "SELECT current_user" in query:
                role = "notebook_app" if active == ["actor"] else "notebook_storage_io"
                login = "notebook_app" if active == ["actor"] else "synthetic_io"
                return SimpleNamespace(one=lambda: (role, login, False, False, False, False))
            if "current_setting" in query:
                return SimpleNamespace(scalar_one=lambda: str(user.id))
            if "documents.id" in query:
                row = {
                    "id": document,
                    "notebook_id": notebook,
                    "filename": "note.txt",
                    "version_id": version,
                    "status": "queued",
                    "error_code": "not_processed",
                }
                return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: row))
            if "ingestion_jobs.id" in query:
                return SimpleNamespace(
                    mappings=lambda: SimpleNamespace(one=lambda: {"id": job, "status": "queued"})
                )
            assert query.startswith("SET LOCAL")

        def scalar(self, statement):
            query = str(statement)
            if "app_admissions" in query:
                return user.id
            if "notebooks.workspace_id" in query:
                events.append("write preflight")
                return workspace
            if "workspace_members.role" in query:
                return "editor"
            raise AssertionError("unexpected synthetic query")

    @contextmanager
    def actor_factory(original):
        assert original is user and not active
        active.append("actor")
        db = DB()
        sessions.append(db)
        try:
            yield db
            events.append("actor commit")
        finally:
            active.pop()

    @contextmanager
    def io_tx():
        assert not active
        active.append("io")
        try:
            yield DB()
            events.append("io commit")
        finally:
            active.pop()

    async def verified(request):
        assert not active
        return user

    monkeypatch.setattr(module, "identity", verified)
    monkeypatch.setattr(module, "capabilities", lambda: {"documents": True})
    monkeypatch.setattr(
        module, "get_settings", lambda: SimpleNamespace(public_origin="https://test")
    )
    monkeypatch.setattr(module, "authenticated_session_factory", lambda: actor_factory)
    real_factory = module.upload_service
    stored = []

    class SyntheticStore:
        async def put(self, actor, target, data, media):
            assert not active and actor == user.id and target == scope
            await asyncio.sleep(0)
            stored.append(data)
            events.append("put")

        async def read(self, actor, target):
            assert not active
            await asyncio.sleep(0)
            events.append("read")
            return stored[0]

    def factory(original):
        service = real_factory(original)
        monkeypatch.setattr(service, "io_tx", io_tx)
        service.configured = True
        monkeypatch.setattr(service, "store_factory", lambda auth: SyntheticStore())
        return service

    monkeypatch.setattr(module, "upload_service", factory)
    intent = SimpleNamespace(
        id=intent_id,
        workspace_id=workspace,
        notebook_id=notebook,
        document_id=document,
        version_id=version,
        storage_key=scope.key,
        state="reserved",
    )
    receipt_state = {}

    def reserve(db, target, **kwargs):
        assert active == ["actor"]
        receipt_state.update(kwargs)
        return intent

    def claim(*args, **kwargs):
        assert active == ["io"]
        return SimpleNamespace(
            storage_key=scope.key,
            sha256=receipt_state["sha256"],
            byte_count=receipt_state["byte_count"],
            media_type="text/plain",
            token=token,
        )

    def finalize(*args):
        assert active == ["actor"]
        events.append("finalize")
        return SimpleNamespace(document_id=document, job_id=job)

    monkeypatch.setattr(lifecycle, "reserve_upload", reserve)
    monkeypatch.setattr(lifecycle, "claim_upload_io", claim)
    monkeypatch.setattr(lifecycle, "check_upload_admission", lambda *args: True)
    monkeypatch.setattr(lifecycle, "confirm_upload_io", lambda *args, **kwargs: True)
    monkeypatch.setattr(lifecycle, "finalize_upload", finalize)
    real_parse = module.parse_text_form

    async def parse(request):
        assert not active
        assert events == ["write preflight", "actor commit"]
        return await real_parse(request)

    monkeypatch.setattr(module, "parse_text_form", parse)
    response, reads = exchange(module, headers=HEADERS, content=BODY, notebook=notebook)
    assert response.status_code == 202 and reads == [True]
    assert response.json()["document"]["id"] == str(document)
    assert response.json()["job"] == {"id": str(job), "status": "queued"}
    assert events.count("write preflight") == 3  # before parse, again in upload, finalize
    assert events[-2:] == ["finalize", "actor commit"]
    assert "put" in events and "read" in events and not active
    assert len({id(db) for db in sessions}) == len(sessions)


@pytest.mark.parametrize("code,status", [("upload_timeout", 408), ("upload_interrupted", 400)])
def test_parser_deadline_and_disconnect_are_fixed_http_errors(opened, monkeypatch, code, status):
    from app.documents.body_limit import UploadBodyError

    async def fail(request):
        raise UploadBodyError(code, 200)

    monkeypatch.setattr(opened.module, "parse_text_form", fail)
    response, reads = exchange(
        opened.module, headers=HEADERS, content=BODY, notebook=opened.notebook
    )
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert not reads and opened.calls == ["auth", "service", "authorize"]


@pytest.mark.parametrize(
    "code,status",
    [("invalid_credentials", 401), ("invalid_identity", 401), ("email_unverified", 403)],
)
def test_auth_failure_is_sanitized_before_service_or_body(opened, monkeypatch, code, status):
    from fastapi import HTTPException

    async def fail(request):
        raise HTTPException(200, detail={"code": code, "message": "private canary"})

    monkeypatch.setattr(opened.module, "identity", fail)
    response, reads = exchange(
        opened.module, headers=HEADERS, content=BODY, notebook=opened.notebook
    )
    assert response.status_code == status
    assert not reads and not opened.calls and "canary" not in response.text


def test_ambiguous_service_failure_after_io_is_never_retried(opened):
    from sqlalchemy.exc import SQLAlchemyError

    calls = []

    async def ambiguous(**kwargs):
        calls.append("external IO once")
        await asyncio.sleep(0)
        raise SQLAlchemyError("private commit canary")

    opened.service.upload = ambiguous
    response, _ = exchange(opened.module, headers=HEADERS, content=BODY, notebook=opened.notebook)
    assert response.status_code == 503 and "canary" not in response.text
    assert calls == ["external IO once"]


@pytest.mark.parametrize(
    "field", ["connection_id", "index_profile_id", "document_id", "expected_version_id"]
)
def test_embedding_and_replacement_form_options_are_not_supported(opened, field):
    extra = (
        f'--fixture\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n'
        f"{uuid4()}\r\n--fixture--\r\n"
    ).encode()
    payload = BODY.replace(b"--fixture--\r\n", extra)
    response, _ = exchange(
        opened.module, headers=HEADERS, content=payload, notebook=opened.notebook
    )
    assert response.status_code == 400
    assert opened.calls == ["auth", "service", "authorize"]


def test_invalid_notebook_is_sanitized_after_auth_before_service(opened):
    response, reads = exchange(opened.module, headers=HEADERS, content=BODY, notebook="not-a-uuid")
    assert response.status_code == 422 and not reads
    assert response.json()["detail"]["code"] == "invalid_notebook"
    assert opened.calls == ["auth"]


def test_service_unconfigured_before_body(opened):
    from app.documents.service import DocumentError

    def unavailable(**kwargs):
        raise DocumentError("storage_unconfigured")

    opened.service.authorize_upload = unavailable
    response, reads = exchange(
        opened.module, headers=HEADERS, content=BODY, notebook=opened.notebook
    )
    assert response.status_code == 503 and not reads


def test_duplicate_content_type_rejected_without_upload(opened):
    response, _ = exchange(
        opened.module,
        headers=HEADERS + [("Content-Type", "text/plain")],
        content=BODY,
        notebook=opened.notebook,
    )
    assert response.status_code == 400
    assert opened.calls == ["auth", "service", "authorize"]


def test_upload_route_is_closed_and_unregistered():
    module = importlib.import_module("app.documents.upload_router")
    assert module.router.prefix == "/api/notebooks"
    from app.main import app

    assert not any(getattr(r, "endpoint", None) is module.upload_document for r in app.routes)


@pytest.mark.parametrize("flag", [None, False, 0, 1, "true", [], {}])
def test_gate_only_accepts_literal_true(monkeypatch, flag):
    from app.documents import upload_router as module

    monkeypatch.setattr(module, "capabilities", lambda: {"documents": flag})
    response, reads = exchange(module, notebook="not-a-uuid")
    assert response.status_code == 404
    assert reads == []


def test_closed_gate_does_not_read_body_or_resolve_auth(monkeypatch):
    from app.documents import upload_router as module

    def forbidden(*args):
        raise AssertionError("must not resolve")

    monkeypatch.setattr(module, "identity", forbidden, raising=False)
    monkeypatch.setattr(module, "upload_service", forbidden)
    monkeypatch.setattr(module, "authenticated_session_factory", forbidden)
    monkeypatch.setattr(module, "storage_io", forbidden)
    response, reads = exchange(module)
    assert response.status_code == 404
    assert reads == []


def test_open_gate_requires_verified_identity_before_payload(monkeypatch):
    from app.documents import upload_router as module
    from fastapi import HTTPException

    monkeypatch.setattr(module, "capabilities", lambda: {"documents": True})

    async def denied(request):
        raise HTTPException(401, detail={"code": "authentication_required"})

    monkeypatch.setattr(module, "identity", denied, raising=False)
    response, reads = exchange(module)
    assert response.status_code == 401
    assert reads == []


@pytest.mark.parametrize("mode", ["origin", "csrf", "nonmember"])
def test_authorization_preflight_before_payload(monkeypatch, mode):
    from app.auth.supabase import Identity
    from app.documents import upload_router as module
    from app.documents.service import DocumentError

    user = Identity(id=uuid4(), email="synthetic@example.test")
    calls = []
    monkeypatch.setattr(module, "capabilities", lambda: {"documents": True})

    async def verified(request):
        calls.append("auth")
        return user

    def service(original):
        assert original is user
        calls.append("service")

        def authorize_upload(**kwargs):
            assert kwargs["actor_id"] == user.id
            calls.append("authorize")
            raise DocumentError("write_required", 403)

        return SimpleNamespace(authorize_upload=authorize_upload)

    monkeypatch.setattr(module, "identity", verified)
    monkeypatch.setattr(
        module, "get_settings", lambda: SimpleNamespace(public_origin="https://test"), raising=False
    )
    monkeypatch.setattr(module, "upload_service", service, raising=False)
    headers = [("Origin", "https://bad" if mode == "origin" else "https://test")]
    if mode != "csrf":
        headers += [("Cookie", "__Host-notebook_csrf=test"), ("X-CSRF-Token", "test")]
    response, reads = exchange(module, headers=headers)
    assert response.status_code == 403
    assert reads == []
    assert calls == (["auth", "service", "authorize"] if mode == "nonmember" else ["auth"])
