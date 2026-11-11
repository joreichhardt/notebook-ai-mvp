"""Provider requests use only administrator-owned endpoints; redirects are forbidden."""

import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

from app.auth.security import fail


def validate_destination(provider):
    url = urlsplit(provider.base_url)
    if url.scheme != "https" and not provider.allow_internal:
        fail(503, "provider_configuration", "Anbieter ist nicht erreichbar.")
    try:
        addresses = socket.getaddrinfo(
            url.hostname, url.port or (443 if url.scheme == "https" else 80)
        )
    except OSError:
        fail(503, "provider_unavailable", "Anbieter ist nicht erreichbar.")
    if not provider.allow_internal and any(
        not ipaddress.ip_address(a[4][0]).is_global for a in addresses
    ):
        fail(503, "provider_configuration", "Anbieter ist nicht erreichbar.")


class Adapter:
    def __init__(self, provider, key, transport=None):
        self.provider = provider
        self.key = key
        self.transport = transport

    async def models(self):
        validate_destination(self.provider)
        headers = {"Authorization": f"Bearer {self.key}"}
        path = "/models"
        if self.provider.kind == "anthropic":
            headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01"}
        elif self.provider.kind == "ollama":
            path = "/api/tags"
        try:
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=False, trust_env=False, transport=self.transport
            ) as client:
                response = await client.get(self.provider.base_url + path, headers=headers)
                if response.status_code in (401, 403):
                    fail(400, "provider_credentials", "Zugang wurde vom Anbieter abgewiesen.")
                response.raise_for_status()
                data = response.json()
                rows = (
                    data.get("models", [])
                    if self.provider.kind == "ollama"
                    else data.get("data", [])
                )
                models = [
                    {"id": r.get("name") if self.provider.kind == "ollama" else r.get("id")}
                    for r in rows
                ]
                return [m for m in models if isinstance(m["id"], str) and 0 < len(m["id"]) <= 200][
                    :500
                ]
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            fail(
                503,
                "provider_unavailable",
                "Verbindungstest fehlgeschlagen. Bitte erneut versuchen.",
            )
