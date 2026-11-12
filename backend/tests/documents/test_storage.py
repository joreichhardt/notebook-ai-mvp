"""Synthetic HTTP fixtures, NOT real private Storage policy acceptance."""
import asyncio
from uuid import uuid4

import httpx
import pytest
from app.documents.storage import ObjectScope, StorageConfig, StorageError, SupabaseStorage
from pydantic import SecretStr


def test_private_bucket_checked_and_scope_authorized_before_each_io():
    scope = ObjectScope(*(uuid4() for _ in range(4)))
    actor = uuid4()
    events = []

    async def authorize(who, target, action):
        assert who == actor and target == scope and action == "read"
        events.append("authorize")

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-token"
        if "/bucket/" in request.url.path:
            events.append("bucket")
            return httpx.Response(200, json={"id": "originals", "public": False})
        events.append("object")
        assert request.url.path == f"/storage/v1/object/authenticated/originals/{scope.key}"
        return httpx.Response(200, content=b"hello")

    storage = SupabaseStorage(
        StorageConfig("https://configured.example/storage/v1", "originals", SecretStr("synthetic-token")),
        authorize=authorize, transport=httpx.MockTransport(handler),
    )
    assert asyncio.run(storage.read(actor, scope)) == b"hello"
    assert events == ["authorize", "bucket", "authorize", "object", "authorize"]
    assert "synthetic-token" not in repr(storage)


@pytest.mark.parametrize("public,status", [(True, 200), (False, 302), (False, 401)])
def test_public_bucket_and_redirects_fail_closed(public, status):
    calls = []

    async def authorize(*args):
        pass

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"id": "originals", "public": public},
                              headers={"location": "https://evil.example"})

    storage = SupabaseStorage(
        StorageConfig("https://configured.example/storage/v1", "originals", SecretStr("secret")),
        authorize=authorize, transport=httpx.MockTransport(handler),
    )
    with pytest.raises(StorageError):
        asyncio.run(storage.read(uuid4(), ObjectScope(*(uuid4() for _ in range(4)))))
    assert len(calls) == 1


def test_unconfigured_storage_and_revocation_never_reach_object():
    calls = []

    async def revoked(*args):
        raise StorageError("storage_forbidden")

    storage = SupabaseStorage(None, authorize=revoked)
    with pytest.raises(StorageError, match="storage_unconfigured"):
        asyncio.run(storage.read(uuid4(), ObjectScope(*(uuid4() for _ in range(4)))))
    storage = SupabaseStorage(
        StorageConfig("https://configured.example/storage/v1", "originals", SecretStr("secret")),
        authorize=revoked, transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )
    with pytest.raises(StorageError, match="storage_forbidden"):
        asyncio.run(storage.read(uuid4(), ObjectScope(*(uuid4() for _ in range(4)))))
    assert calls == []


def test_non_upsert_write_uses_generated_key_and_private_bucket():
    scope = ObjectScope(*(uuid4() for _ in range(4)))
    checks, requests = [], []

    async def authorize(actor, target, action):
        assert target == scope and action == "write"
        checks.append(action)

    def handler(request):
        requests.append(request)
        if "/bucket/" in request.url.path:
            return httpx.Response(200, json={"id": "originals", "public": False})
        assert request.method == "POST"
        assert request.url.path == f"/storage/v1/object/originals/{scope.key}"
        assert request.headers["x-upsert"] == "false"
        assert request.headers["content-type"] == "text/plain"
        assert request.content == b"hello"
        return httpx.Response(200, json={"Key": f"originals/{scope.key}"})

    storage = SupabaseStorage(
        StorageConfig("https://configured.example/storage/v1", "originals", SecretStr("fixture")),
        authorize=authorize, transport=httpx.MockTransport(handler),
    )
    asyncio.run(storage.put(uuid4(), scope, b"hello", "text/plain"))
    assert len(requests) == 2 and len(checks) == 3


@pytest.mark.parametrize("mode", ["oversize", "compressed", "wrong_key", "malformed", "secret_error"])
def test_http_boundary_rejects_untrusted_or_oversize_responses_without_secret(mode):
    import traceback

    async def authorize(*args):
        pass

    def handler(request):
        if mode == "secret_error":
            raise httpx.ConnectError("transport contains fixture-credential", request=request)
        if "/bucket/" in request.url.path:
            return httpx.Response(200, json={"id": "originals", "public": False})
        if mode == "oversize":
            return httpx.Response(200, content=b"x" * (25 * 1024 * 1024 + 1))
        if mode == "compressed":
            # An actual compressed body is unnecessary: reject before decoding it.
            return httpx.Response(200, headers={"content-encoding": "br"}, content=b"")
        if mode == "wrong_key":
            return httpx.Response(200, json={"Key": "foreign/original"})
        return httpx.Response(200, content=b"not json")

    storage = SupabaseStorage(
        StorageConfig("https://configured.example/storage/v1", "originals", SecretStr("fixture-credential")),
        authorize=authorize, transport=httpx.MockTransport(handler),
    )
    with pytest.raises(StorageError) as error:
        if mode in ("wrong_key", "malformed"):
            asyncio.run(storage.put(uuid4(), ObjectScope(*(uuid4() for _ in range(4))), b"hello", "text/plain"))
        else:
            asyncio.run(storage.read(uuid4(), ObjectScope(*(uuid4() for _ in range(4)))))
    assert "fixture-credential" not in "".join(traceback.format_exception(error.value))


def test_revocation_after_bucket_check_prevents_object_request():
    calls, checks = [], []

    async def authorize(*args):
        checks.append(1)
        if len(checks) == 2:
            raise StorageError("storage_forbidden")

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"id": "originals", "public": False})

    storage = SupabaseStorage(
        StorageConfig("https://configured.example/storage/v1", "originals", SecretStr("fixture")),
        authorize=authorize, transport=httpx.MockTransport(handler),
    )
    with pytest.raises(StorageError, match="storage_forbidden"):
        asyncio.run(storage.read(uuid4(), ObjectScope(*(uuid4() for _ in range(4)))))
    assert calls == ["/storage/v1/bucket/originals"]


@pytest.mark.parametrize("endpoint,bucket", [
    ("http://configured.example/storage/v1", "originals"),
    ("https://configured.example/storage/v1?token=x", "originals"),
    ("https://configured.example/storage/v1", "../foreign"),
])
def test_invalid_administrator_configuration_is_rejected(endpoint, bucket):
    with pytest.raises(ValueError):
        StorageConfig(endpoint, bucket, SecretStr("fixture"))
