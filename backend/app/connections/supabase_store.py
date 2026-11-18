"""Personal key access within an authenticated, caller-owned database transaction."""

from typing import NoReturn
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.security import fail
from app.providers.config import settings


def _unavailable() -> NoReturn:
    fail(503, "provider_vault_unavailable", "Der Schlüsselspeicher ist nicht verfügbar.")


class SupabaseStore:
    def __init__(self, db: Session):
        if not settings().configured:
            _unavailable()
        self.db = db

    def create(self, connection_id: UUID, value: str) -> UUID:
        # Check before binding any secret. Business SQL is still parameterized.
        try:
            safe = self.db.scalar(
                text(
                    "SELECT current_setting('log_parameter_max_length') = '0' "
                    "AND current_setting('log_parameter_max_length_on_error') = '0'"
                )
            )
            if safe is not True:
                _unavailable()
            secret_id = self.db.scalar(
                text("SELECT notebook_provider.create_key(:connection_id, :value)"),
                {"connection_id": connection_id, "value": value},
            )
            if not isinstance(secret_id, UUID):
                _unavailable()
            return secret_id
        except SQLAlchemyError:
            _unavailable()

    def read(self, connection_id, secret_id):
        key = self._call(
            "SELECT notebook_provider.read_key(:connection_id, :secret_id)",
            connection_id,
            secret_id,
        )
        if not key:
            fail(409, "provider_key_unavailable", "Bitte den Modellschlüssel erneut hinterlegen.")
        return key

    def delete(self, connection_id, secret_id):
        return self._call(
            "SELECT notebook_provider.delete_key(:connection_id, :secret_id)",
            connection_id,
            secret_id,
        )

    def _call(self, statement, connection_id, secret_id):
        try:
            return self.db.scalar(
                text(statement), {"connection_id": connection_id, "secret_id": secret_id}
            )
        except SQLAlchemyError:
            _unavailable()
