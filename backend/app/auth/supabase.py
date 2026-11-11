from dataclasses import dataclass
from uuid import UUID

import httpx

from app.auth.security import fail
from app.auth.settings import Settings


@dataclass(frozen=True)
class Identity:
    id: UUID
    email: str


class SupabaseAuth:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def request(self, method: str, path: str, token: str = "", data=None):
        if not self.settings.supabase_url or not self.settings.supabase_anon_key.get_secret_value():
            fail(503, "auth_unconfigured", "Anmeldung ist noch nicht eingerichtet.")
        headers = {"apikey": self.settings.supabase_anon_key.get_secret_value()}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.request(
                    method,
                    self.settings.supabase_url.rstrip("/") + "/auth/v1/" + path,
                    headers=headers,
                    json=data,
                )
            if response.status_code in (400, 401, 403, 422):
                fail(401, "invalid_credentials", "Anmeldung oder Sitzung ungültig.")
            if response.status_code >= 300:
                fail(503, "auth_unavailable", "Anmeldedienst nicht verfügbar.")
            return response.json() if response.content else {}
        except (httpx.HTTPError, ValueError):
            fail(503, "auth_unavailable", "Anmeldedienst nicht verfügbar.")

    async def identity(self, token: str) -> Identity:
        if not token:
            fail(401, "authentication_required", "Bitte anmelden.")
        user = await self.request("GET", "user", token)
        try:
            if not user.get("email_confirmed_at") or not user.get("email"):
                fail(403, "email_unverified", "E-Mail-Adresse ist nicht bestätigt.")
            return Identity(UUID(user["id"]), user["email"])
        except (KeyError, ValueError, TypeError):
            fail(401, "invalid_identity", "Sitzung ungültig.")
