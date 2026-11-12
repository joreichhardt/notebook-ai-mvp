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
