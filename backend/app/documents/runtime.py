"""Opt-in local upload dependencies; no provisioning or automatic fallback.

The operator supplies a private original volume and a separate restricted IO
login through a protected secret file. The service still checks database roles
and fresh actor permissions on every transaction. Credentials rotate by restart.
"""

import atexit
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.documents.local_storage import LocalStorage, LocalStorageConfig
from app.documents.service import DocumentError
from app.documents.storage import Authorize


class UploadSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOTEBOOK_")
    local_uploads_enabled: bool = False
    local_storage_root: str = ""
    storage_io_database_url_file: str = ""


def upload_settings() -> UploadSettings:
    return UploadSettings()


@dataclass(frozen=True)
class LocalUploadRuntime:
    config: LocalStorageConfig
    engine: Engine = field(repr=False)

    @contextmanager
    def io_tx(self):
        with Session(self.engine) as db, db.begin():
            role = db.scalar(
                text("SELECT pg_catalog.set_config('role', :role, true)"),
                {"role": "notebook_storage_io"},
            )
            if role != "notebook_storage_io":
                raise DocumentError("unsafe_database_role")
            yield db

    def store_factory(self, authorize: Authorize) -> LocalStorage:
        return LocalStorage(self.config, authorize=authorize)


def _secret(path: str) -> str:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_nlink != 1
            or not 0 < info.st_size <= 16 * 1024
        ):
            raise DocumentError("storage_unconfigured")
        value = source.read(16 * 1024 + 1)
        if len(value) > 16 * 1024:
            raise DocumentError("storage_unconfigured")
        return value.decode("utf-8").strip()


@lru_cache(maxsize=1)
def local_upload_runtime() -> LocalUploadRuntime | None:
    try:
        settings = upload_settings()
        if not settings.local_uploads_enabled:
            return None
        if not settings.local_storage_root or not settings.storage_io_database_url_file:
            raise DocumentError("storage_unconfigured")
        if not Path(settings.storage_io_database_url_file).is_absolute():
            raise DocumentError("storage_unconfigured")
        config = LocalStorageConfig(Path(settings.local_storage_root))
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(config.root, flags)
        try:
            LocalStorage._private(fd, directory=True)
        finally:
            os.close(fd)
        parsed = make_url(_secret(settings.storage_io_database_url_file))
        if (
            parsed.drivername not in ("postgresql", "postgresql+psycopg")
            or not parsed.username
            or parsed.username
            in ("notebook_app", "postgres", "supabase_admin", "supabase_auth_admin")
        ):
            raise DocumentError("storage_unconfigured")
        options = parsed.query.get("options", "")
        if not isinstance(options, str):
            raise DocumentError("storage_unconfigured")
        engine = create_engine(
            parsed.set(drivername="postgresql+psycopg"),
            hide_parameters=True,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=0,
            pool_timeout=1,
            connect_args={
                "connect_timeout": 3,
                "options": f"{options} -c statement_timeout=3000 -c lock_timeout=1000".strip(),
            },
        )
        atexit.register(engine.dispose)
        return LocalUploadRuntime(config, engine)
    except (OSError, ValueError, SQLAlchemyError):
        raise DocumentError("storage_unconfigured") from None
