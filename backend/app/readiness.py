"""Read-only runtime adapters; deliberately not installed by application startup.

No user identity, personal secret or model request is part of these probes.
A real supervised worker/heartbeat adapter is still required for full readiness.
"""

import asyncio
import json
import os
import stat
import threading
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr
from sqlalchemy import and_, column, create_engine, func, not_, select, table
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.documents.storage import StorageConfig
from app.health import ProbeUnavailable

AsyncProbe = Callable[[], Coroutine[Any, Any, bool]]


def database_probe(database_url: SecretStr) -> AsyncProbe:
    """Independent restricted connection, never a migration/admin engine.

    A cancelled asyncio waiter cannot kill a synchronous driver/DNS operation.
    Driver timeouts bound ordinary I/O; the gate allows at most one outstanding
    synchronous check per adapter, including after its waiter is cancelled.
    """
    try:
        url = make_url(database_url.get_secret_value())
        if (
            url.drivername not in ("postgresql", "postgresql+psycopg")
            or url.username != "notebook_app"
            or not url.host
            or not url.database
        ):
            raise ProbeUnavailable()
        # Do not inherit arbitrary startup options from a different engine/pool.
        engine = create_engine(
            url.set(drivername="postgresql+psycopg"),
            poolclass=NullPool,
            hide_parameters=True,
            echo=False,
            connect_args={
                "connect_timeout": 1,
                "options": "-c default_transaction_read_only=on -c statement_timeout=500 -c lock_timeout=250",
            },
        )
    except (SQLAlchemyError, ValueError):
        raise ProbeUnavailable() from None
    gate = threading.Lock()

    def check_sync() -> bool:
        if not gate.acquire(blocking=False):
            return False
        try:
            with engine.connect() as connection:
                # Catalog presence, not migration/RLS-content attestation.
                roles = table(
                    "pg_roles",
                    column("rolname"),
                    column("rolsuper"),
                    column("rolbypassrls"),
                    schema="pg_catalog",
                )
                statement = (
                    select(
                        and_(
                            func.session_user() == func.current_user(),
                            func.current_user() == "notebook_app",
                            not_(roles.c.rolsuper),
                            not_(roles.c.rolbypassrls),
                            func.current_setting("transaction_read_only") == "on",
                            *[
                                func.to_regclass(name).is_not(None)
                                for name in (
                                    "public.app_admissions",
                                    "public.workspace_members",
                                    "public.notebooks",
                                    "public.user_provider_connections",
                                    "public.documents",
                                    "public.document_versions",
                                    "public.ingestion_jobs",
                                    "public.upload_intents",
                                )
                            ],
                            func.to_regprocedure("public.quota_gate()").is_not(None),
                        )
                    )
                    .select_from(roles)
                    .where(roles.c.rolname == func.current_user())
                )
                return connection.scalar(statement) is True
        except SQLAlchemyError:
            raise ProbeUnavailable() from None
        finally:
            gate.release()

    async def check() -> bool:
        return await asyncio.to_thread(check_sync)

    return check


async def _json_get(
    url: str, headers: dict[str, str], transport: httpx.AsyncBaseTransport | None
) -> dict:
    try:
        async with (
            asyncio.timeout(1.5),
            httpx.AsyncClient(
                timeout=1,
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            ) as client,
            client.stream(
                "GET", url, headers={**headers, "Accept-Encoding": "identity"}
            ) as response,
        ):
            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if response.status_code != 200 or media_type != "application/json":
                raise ProbeUnavailable()
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ProbeUnavailable()
            body = bytearray()
            async for chunk in response.aiter_raw():
                if len(body) + len(chunk) > 4096:
                    raise ProbeUnavailable()
                body.extend(chunk)
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ProbeUnavailable()
            return value
    except (TimeoutError, httpx.HTTPError, ValueError, RecursionError):
        raise ProbeUnavailable() from None


def auth_probe(
    origin: str,
    anon_key: SecretStr,
    *,
    expected_version: str,
    allow_internal_http: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncProbe:
    """GoTrue availability/version, not signup/OAuth/SMTP policy acceptance."""
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in ("https", "http")
        or (parsed.scheme == "http" and not allow_internal_http)
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or not anon_key.get_secret_value()
        or not expected_version
    ):
        raise ProbeUnavailable()

    async def check() -> bool:
        value = await _json_get(
            origin.rstrip("/") + "/auth/v1/health",
            {"apikey": anon_key.get_secret_value()},
            transport,
        )
        return value.get("name") == "GoTrue" and value.get("version") == expected_version

    return check


def storage_probe(
    config: StorageConfig, *, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncProbe:
    """Read bucket metadata only. No object list/read/write or actor impersonation."""

    async def check() -> bool:
        token = config.token.get_secret_value()
        value = await _json_get(
            config.endpoint + "/bucket/" + config.bucket,
            {"apikey": token, "authorization": "Bearer " + token},
            transport,
        )
        return value.get("id") == config.bucket and value.get("public") is False

    return check


def secret_files_probe(paths: Sequence[Path]) -> AsyncProbe:
    """Only explicitly assigned mounted consumer files; never fetch from Bitwarden.

    Running consumers may keep their valid mounted files during a BWS outage.
    Restart/provisioning must still retrieve/validate a complete release first.
    No global bootstrap or personal-provider secrets belong in this file list.
    """
    files = tuple(paths)
    if any(not path.is_absolute() for path in files):
        raise ProbeUnavailable()
    gate = threading.Lock()

    def check_sync() -> bool:
        if not files or len(files) > 16 or "BWS_ACCESS_TOKEN" in os.environ:
            return False
        if not gate.acquire(blocking=False):
            return False
        try:
            for path in files:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
                try:
                    info = os.fstat(fd)
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
                        or not 0 < info.st_size <= 65536
                    ):
                        return False
                    value = os.read(fd, 65537)
                    if not value.strip() or len(value) > 65536 or len(value) != info.st_size:
                        return False
                finally:
                    os.close(fd)
            return True
        except OSError:
            raise ProbeUnavailable() from None
        finally:
            gate.release()

    async def check() -> bool:
        return await asyncio.to_thread(check_sync)

    return check
