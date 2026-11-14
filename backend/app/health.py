"""Fail-closed release/readiness contract, not a release authorization mechanism.

Only trusted runtime assembly may install Readiness on app.state.readiness.
No synthetic, liveness-only or configuration-presence checks are installed here.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from types import MappingProxyType

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/health", tags=["health"])
# Generated in the runtime image, never supplied by HTTP or a runtime environment variable.
RELEASE_FILE = Path(__file__).with_name("_release_sha")
REQUIRED_PROBES = frozenset({"database", "auth", "secrets", "storage", "worker"})
PROBE_TIMEOUT_SECONDS = 2.0
Probe = Callable[[], Awaitable[bool]]


def image_release() -> str | None:
    try:
        if RELEASE_FILE.is_symlink() or not RELEASE_FILE.is_file():
            return None
        with RELEASE_FILE.open("r", encoding="ascii") as stream:
            value = stream.read(41)
        return value if re.fullmatch(r"[0-9a-f]{40}", value) else None
    except (OSError, UnicodeError):
        return None


class ProbeUnavailable(Exception):
    """Sanitized failure contract for runtime adapters; never include secret values."""


class Readiness:
    """Snapshot of mandatory asynchronous, bounded, read-only runtime probes.

    Production adapters must bound transport/DB timeouts themselves and must not
    swallow cancellation, log secrets, mutate data or invoke a paid provider.
    Expected external failures must be converted to ProbeUnavailable without raw
    diagnostics. Unexpected adapter errors also fail closed at the TaskGroup
    boundary, after sibling cancellation, without logging raw diagnostics.
    All five checks remain required even while public document/chat gates are off.
    Missing adapters intentionally block readiness rather than weakening the profile.
    """

    def __init__(self, probes: Mapping[str, Probe]):
        self.probes = MappingProxyType(dict(probes))

    async def check(self) -> bool:
        if self.probes.keys() != REQUIRED_PROBES:
            return False

        async def check_one(probe: Probe) -> bool:
            try:
                return await asyncio.wait_for(probe(), timeout=PROBE_TIMEOUT_SECONDS) is True
            except (TimeoutError, ProbeUnavailable):
                # Only the sanitized adapter failure contract crosses this boundary.
                return False

        try:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(check_one(probe)) for probe in self.probes.values()]
        except ExceptionGroup:
            # TaskGroup has cancelled/joined siblings; arbitrary adapter errors
            # must not become credentials in HTTP or exception logger output.
            # BaseException/caller cancellation is deliberately not swallowed.
            return False
        return all(not task.cancelled() and task.result() is True for task in tasks)


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    release = image_release()
    checks = getattr(request.app.state, "readiness", None)
    if release is not None and isinstance(checks, Readiness) and await checks.check():
        return JSONResponse(
            {"status": "ready", "release": release}, headers={"Cache-Control": "no-store"}
        )
    return JSONResponse(
        {"status": "not_ready"}, status_code=503, headers={"Cache-Control": "no-store"}
    )
