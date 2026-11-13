"""Synthetic no-network safeguards; never a substitute for the native packet."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.documents.storage import ObjectScope, StorageConfig
from pydantic import SecretStr


def config():
    return StorageConfig(
        "http://gateway:10000/storage/v1", "a5-private", SecretStr("SYNTHETIC_CANARY"), True
    )


@pytest.mark.parametrize("bad", ["../original", "not-a-scope", "https://foreign/", "/absolute"])
def test_cleanup_key_mismatch_stops_before_http(bad):
    from backend.tests.storage_real.stage_b_contract import delete_claimed

    scope = ObjectScope(*(uuid4() for _ in range(4)))
    claim = SimpleNamespace(storage_key=bad)
    calls = []

    def unexpected(request):
        calls.append(request)
        return httpx.Response(500)

    async def operation():
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
            with pytest.raises(ValueError, match="cleanup_scope_mismatch"):
                await delete_claimed(client, config(), claim, scope)

    asyncio.run(operation())
    assert calls == []


@pytest.mark.parametrize(
    "delete_status,get_status,success",
    [(200, 404, False), (404, 404, False), (200, 200, False), (500, 404, False), (200, 403, False)],
)
def test_cleanup_requires_successful_delete_and_privileged_exact_absence(
    delete_status, get_status, success
):
    from backend.tests.storage_real.stage_b_contract import delete_claimed

    scope = ObjectScope(*(uuid4() for _ in range(4)))
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            delete_status if request.method == "DELETE" else get_status,
            content=b"SYNTHETIC_RESPONSE_CANARY",
        )

    async def operation():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            if success:
                await delete_claimed(
                    client, config(), SimpleNamespace(storage_key=scope.key), scope
                )
            else:
                with pytest.raises(ValueError):
                    await delete_claimed(
                        client, config(), SimpleNamespace(storage_key=scope.key), scope
                    )

    asyncio.run(operation())
    assert calls[0].method == "DELETE"
    if success:
        assert len(calls) == 2 and calls[1].method == "GET"
        assert "/object/authenticated/" in calls[1].url.path


def test_manifest_has_only_reviewed_isolated_services_and_pins():
    base = Path(__file__).parent
    manifest = json.loads((base / "stage_b.compose.json").read_text())
    assert set(manifest["services"]) == {"db", "runner", "storage", "gateway"}
    lock = json.loads((base / "lock.json").read_text())
    assert manifest["services"]["storage"]["image"] == lock["stage_b_forbidden"]["storage"]
    assert manifest["services"]["gateway"]["image"] == lock["stage_b_forbidden"]["envoy"]
    for service in manifest["services"].values():
        assert not any(
            service.get(k) for k in ("ports", "privileged", "network_mode", "container_name")
        )
        assert service["logging"]["driver"] == "none"
        assert service["cpus"] and service["mem_limit"] and service["pids_limit"]
        assert service["networks"] == ["isolated"]
    assert manifest["networks"] == {"isolated": {"internal": True}}
    storage = manifest["services"]["storage"]
    assert storage["environment"]["DB_INSTALL_ROLES"] == "false"
    assert storage["environment"]["IMAGE_TRANSFORMATION_ENABLED"] == "false"
    assert storage["environment"]["PG_QUEUE_ENABLE"] == "false"
    assert storage["read_only"] and storage["cap_drop"] == ["ALL"]


def test_envoy_is_storage_only_with_no_admin_or_access_logs():
    source = Path(__file__).with_name("stage_b.envoy.json").read_text()
    config = json.loads(source)
    assert "admin" not in config and "access_log" not in source
    listener = config["static_resources"]["listeners"][0]
    manager = listener["filter_chains"][0]["filters"][0]["typed_config"]
    routes = manager["route_config"]["virtual_hosts"][0]["routes"]
    assert len(routes) == 2
    assert routes[0]["match"] == {"prefix": "/storage/v1/"}
    assert routes[0]["route"]["prefix_rewrite"] == "/"
    assert routes[1]["direct_response"] == {"status": 404}
    assert "request_headers_to_remove" not in source and "request_headers_to_add" not in source
