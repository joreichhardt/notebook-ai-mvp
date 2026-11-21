from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.auth.security import fail


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOTEBOOK_")
    public_origin: str = "https://note.yokoma.de"
    supabase_url: str = ""
    supabase_anon_key: SecretStr = SecretStr("")
    database_url: SecretStr = SecretStr("")
    database_url_file: str = ""
    supabase_anon_key_file: str = ""
    invitation_smtp_host: str = ""
    invitation_smtp_port: int = 587
    invitation_smtp_sender: str = ""
    invitation_smtp_password_file: str = ""

    def read_invitation_smtp_credential(self) -> SecretStr:
        if not self.invitation_smtp_password_file:
            return SecretStr("")
        try:
            value = Path(self.invitation_smtp_password_file).read_text().rstrip("\r\n")
        except (OSError, UnicodeError):
            return SecretStr("")
        return SecretStr(value)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    for name in ("database_url", "supabase_anon_key"):
        path = getattr(settings, name + "_file")
        if path:
            try:
                value = Path(path).read_text().strip()
                if not value:
                    raise ValueError("empty")
                setattr(settings, name, SecretStr(value))
            except (OSError, ValueError):
                fail(503, "configuration_unavailable", "Dienstkonfiguration nicht verfügbar.")
    return settings
