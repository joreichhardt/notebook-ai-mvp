"""Fixture-only bounded real HTTP and claim-key cleanup, not a production worker."""

import asyncio
import json
from dataclasses import dataclass, field

import httpx
from app.documents.storage import ObjectScope, StorageConfig


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes = field(repr=False)


async def request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> Reply:
    if type(method) is not str or method not in ("GET", "POST", "DELETE"):
        raise ValueError("fixture_method_rejected")
    async with asyncio.timeout(30), client.stream(method, url, **kwargs) as response:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > 16384:
                raise ValueError("fixture_response_bound")
            body.extend(chunk)
        print(
            "STAGE_B_HTTP="
            + json.dumps({"method": method, "status": response.status_code}, separators=(",", ":")),
            flush=True,
        )
        return Reply(response.status_code, bytes(body))


async def delete_claimed(client, config: StorageConfig, claim, scope: ObjectScope) -> None:
    if type(claim.storage_key) is not str or claim.storage_key != scope.key:
        raise ValueError("cleanup_scope_mismatch")
    headers = {"authorization": "Bearer " + config.token.get_secret_value()}
    result = await request(
        client, "DELETE", f"{config.endpoint}/object/{config.bucket}/{scope.key}", headers=headers
    )
    if result.status not in (200, 204, 404):
        raise ValueError("cleanup_delete_failed")
    result = await request(
        client,
        "GET",
        f"{config.endpoint}/object/authenticated/{config.bucket}/{scope.key}",
        headers=headers,
    )
    if result.status != 404:
        raise ValueError("cleanup_absence_unconfirmed")
