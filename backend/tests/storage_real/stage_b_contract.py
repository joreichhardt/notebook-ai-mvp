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


def canonical_absence(reply: Reply) -> bool:
    if (
        type(reply) is not Reply
        or type(reply.status) is not int
        or reply.status not in (400, 404)
        or type(reply.body) is not bytes
        or not 0 < len(reply.body) <= 16384
    ):
        return False

    def unique_keys(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("duplicate_response_key")
            data[key] = value
        return data

    try:
        data = json.loads(reply.body.decode("utf-8"), object_pairs_hook=unique_keys)
    except (ValueError, UnicodeError, RecursionError):
        return False
    return (
        type(data) is dict
        and data
        == {
            "statusCode": "404",
            "error": "not_found",
            "code": "NoSuchKey",
            "message": "Object not found",
        }
        and all(type(value) is str for value in data.values())
    )


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
    if result.status not in (200, 204) and not canonical_absence(result):
        raise ValueError("cleanup_delete_failed")
    result = await request(
        client,
        "GET",
        f"{config.endpoint}/object/authenticated/{config.bucket}/{scope.key}",
        headers=headers,
    )
    if not canonical_absence(result):
        raise ValueError("cleanup_absence_unconfirmed")
