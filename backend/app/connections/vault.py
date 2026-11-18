"""Personal secret lifecycle; deploy-time provisioning continues to use bws.

No subprocess or CLI arguments carry personal credentials. SDK state stays in memory.
"""

import os
import stat
from typing import NoReturn
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException


def fail(status: int, code: str, message: str) -> NoReturn:
    # SDK failures may embed key/token values in chained exception messages.
    raise HTTPException(status, detail={"code": code, "message": message}) from None


class BitwardenStore:
    def __init__(self, config):
        self.config = config

    def client(self):
        try:
            if not all(
                (
                    self.config.bitwarden_api_url,
                    self.config.bitwarden_identity_url,
                    self.config.bitwarden_project_id,
                    self.config.bitwarden_organization_id,
                    self.config.bitwarden_token_file,
                )
            ):
                raise ValueError()
            for url in (self.config.bitwarden_api_url, self.config.bitwarden_identity_url):
                parsed = urlsplit(url)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError()
            from bitwarden_sdk import BitwardenClient, DeviceType, client_settings_from_dict

            fd = os.open(self.config.bitwarden_token_file, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_mode & 0o077
                    or info.st_uid != os.getuid()
                ):
                    raise ValueError()
                token = stream.read(16385).strip()
            if not token or len(token) > 16384:
                raise ValueError()
            client = BitwardenClient(
                client_settings_from_dict(
                    {
                        "apiUrl": self.config.bitwarden_api_url,
                        "identityUrl": self.config.bitwarden_identity_url,
                        "deviceType": DeviceType.SDK,
                        "userAgent": "Notebook",
                    }
                )
            )
            result = client.auth().login_access_token(token)
            if not result.success:
                raise ValueError()
            return client
        except Exception:  # noqa: BLE001 - SDK errors can contain credentials; redact boundary
            fail(
                503,
                "secret_store_unavailable",
                "Verbindung konnte nicht gespeichert werden. Bitte Betreiber kontaktieren.",
            )

    def create(self, owner, connection_id, value):
        client = self.client()
        try:
            response = client.secrets().create(
                UUID(self.config.bitwarden_organization_id),
                f"notebook:{owner}:{connection_id}",
                value,
                "",
                [UUID(self.config.bitwarden_project_id)],
            )
            if not response.success or not response.data:
                raise ValueError()
            return UUID(str(response.data.id))
        except Exception:  # noqa: BLE001 - SDK errors can contain credentials; redact boundary
            fail(503, "secret_store_unavailable", "Zugang konnte nicht gespeichert werden.")

    def _get(self, client, secret_id, owner, connection_id):
        response = client.secrets().get(str(secret_id))
        if not response.success or not response.data:
            raise ValueError()
        data = response.data
        if (
            str(data.organization_id) != self.config.bitwarden_organization_id
            or str(data.project_id) != self.config.bitwarden_project_id
            or data.key != f"notebook:{owner}:{connection_id}"
        ):
            raise ValueError()
        return data.value

    def read(self, secret_id, owner, connection_id):
        client = self.client()
        try:
            return self._get(client, secret_id, owner, connection_id)
        except Exception:  # noqa: BLE001 - SDK errors can contain credentials; redact boundary
            fail(503, "secret_store_unavailable", "Zugang konnte nicht geladen werden.")

    def delete(self, secret_id, owner, connection_id):
        client = self.client()
        try:
            self._get(client, secret_id, owner, connection_id)
            result = client.secrets().delete([str(secret_id)])
            if not result.success or result.data is None:
                raise ValueError()
            results = result.data.data
            if (
                len(results) != 1
                or str(results[0].id) != str(secret_id)
                or results[0].error is not None
            ):
                raise ValueError()
        except Exception:  # noqa: BLE001 - SDK errors can contain credentials; redact boundary
            fail(
                503,
                "secret_store_unavailable",
                "Zugang konnte nicht entfernt werden. Bitte erneut versuchen.",
            )
