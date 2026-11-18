from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Provider(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    label: str
    kind: str = Field(pattern=r"^(openai|anthropic|ollama)$")
    base_url: str
    allow_internal: bool = False

    @field_validator("base_url")
    @classmethod
    def url(cls, value):
        url = urlsplit(value)
        if (
            url.scheme not in ("https", "http")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Invalid provider URL")
        return value.rstrip("/")


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOTEBOOK_PROVIDER_")
    endpoints: list[Provider] = []
    vault_enabled: bool = False
    bitwarden_api_url: str = ""
    bitwarden_identity_url: str = ""
    bitwarden_project_id: str = ""
    bitwarden_organization_id: str = ""
    bitwarden_token_file: str = ""

    @property
    def configured(self):
        return self.vault_enabled


def settings():
    return ProviderSettings()
