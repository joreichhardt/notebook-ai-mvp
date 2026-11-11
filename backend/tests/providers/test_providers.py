from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from app.connections.vault import BitwardenStore
from app.providers.adapter import Adapter, validate_destination
from app.providers.config import Provider, ProviderSettings
from fastapi import HTTPException


def test_unconfigured_store_is_closed():
    assert not ProviderSettings().configured
    with pytest.raises(HTTPException) as error:
        BitwardenStore(ProviderSettings()).create(uuid4(), uuid4(), "DO_NOT_LEAK")
    assert error.value.status_code == 503
    assert "DO_NOT_LEAK" not in str(error.value.detail)


def test_external_provider_cannot_resolve_private_ip():
    provider = Provider(
        id="cloud", label="Cloud", kind="openai", base_url="https://models.example/v1"
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]),
        pytest.raises(HTTPException),
    ):
        validate_destination(provider)


def test_explicit_internal_provider_can_use_http():
    provider = Provider(
        id="local",
        label="Local",
        kind="ollama",
        base_url="http://127.0.0.1:11434",
        allow_internal=True,
    )
    validate_destination(provider)


def test_secret_metadata_bound_to_owner_and_project():
    org, project, owner, conn, secret = [uuid4() for _ in range(5)]
    config = ProviderSettings(bitwarden_organization_id=str(org), bitwarden_project_id=str(project))
    data = SimpleNamespace(
        organization_id=org, project_id=project, key=f"notebook:{owner}:{conn}", value="PRIVATE"
    )
    client = SimpleNamespace(
        secrets=lambda: SimpleNamespace(get=lambda _: SimpleNamespace(success=True, data=data))
    )
    store = BitwardenStore(config)
    assert store._get(client, secret, owner, conn) == "PRIVATE"
    with pytest.raises(ValueError):
        store._get(client, secret, uuid4(), conn)
    data.project_id = uuid4()
    with pytest.raises(ValueError):
        store._get(client, secret, owner, conn)


def test_adapter_redacts_provider_error_and_never_follows_redirect():
    import asyncio

    p = Provider(
        id="local",
        label="Local",
        kind="openai",
        base_url="http://127.0.0.1:8009/v1",
        allow_internal=True,
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254"}, json={"error": "PRIVATE"}
        )

    with pytest.raises(HTTPException) as error:
        asyncio.run(Adapter(p, "PRIVATE", httpx.MockTransport(handler)).models())
    assert len(requests) == 1
    assert "PRIVATE" not in str(error.value.detail)


def test_native_anthropic_models_protocol():
    import asyncio

    p = Provider(
        id="claude",
        label="Claude",
        kind="anthropic",
        base_url="http://127.0.0.1:8010/v1",
        allow_internal=True,
    )

    def handler(request):
        assert request.headers["x-api-key"] == "PRIVATE"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"id": "fixture-model"}]})

    assert asyncio.run(Adapter(p, "PRIVATE", httpx.MockTransport(handler)).models()) == [
        {"id": "fixture-model"}
    ]


@pytest.mark.parametrize(
    "outcome",
    ["envelope_error", "missing", "empty", "different", "partial", "duplicate", "success"],
)
def test_delete_requires_exact_confirmed_sdk_item(outcome):
    from bitwarden_sdk.schemas import (
        ResponseForSecretsDeleteResponse,
        SecretDeleteResponse,
        SecretsDeleteResponse,
    )

    secret, owner, connection = uuid4(), uuid4(), uuid4()
    item = SecretDeleteResponse(secret)
    rows = [item]
    if outcome == "empty":
        rows = []
    elif outcome == "different":
        rows = [SecretDeleteResponse(uuid4())]
    elif outcome == "partial":
        rows = [SecretDeleteResponse(secret, error="PRIVATE_UPSTREAM_DETAIL")]
    elif outcome == "duplicate":
        rows = [item, item]
    result = ResponseForSecretsDeleteResponse(
        success=outcome != "envelope_error",
        data=None if outcome == "missing" else SecretsDeleteResponse(rows),
    )
    client = SimpleNamespace(secrets=lambda: SimpleNamespace(delete=lambda ids: result))
    store = BitwardenStore(ProviderSettings())
    with (
        patch.object(store, "client", return_value=client),
        patch.object(store, "_get", return_value="KEY"),
    ):
        if outcome == "success":
            assert store.delete(secret, owner, connection) is None
        else:
            with pytest.raises(HTTPException) as error:
                store.delete(secret, owner, connection)
            assert error.value.status_code == 503
            assert "PRIVATE_UPSTREAM_DETAIL" not in str(error.value.detail)
