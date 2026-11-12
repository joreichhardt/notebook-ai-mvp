"""Bounded, lossy diagnostic seam. No runtime wiring; input is never executable.

Only fixed tokens escape. Recognized text is a hint, never trusted root-cause evidence.
The PG image has no verified Python runtime: do not mount/execute this there implicitly.
"""

import json
import os
import re
import selectors
import signal
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass

PHASES = {
    b"/docker-entrypoint-initdb.d/migrate.sh: running "
    b"/docker-entrypoint-initdb.d/init-scripts/99-roles.sql": "phase_roles",
    b"/docker-entrypoint-initdb.d/migrate.sh: running "
    b"/docker-entrypoint-initdb.d/init-scripts/99-jwt.sql": "phase_jwt",
    **{
        b"/docker-entrypoint-initdb.d/migrate.sh: running "
        b"/docker-entrypoint-initdb.d/init-scripts/" + filename: phase
        for filename, phase in (
            (b"00000000000000-initial-schema.sql", "phase_initial"),
            (b"00000000000001-auth-schema.sql", "phase_auth"),
            (b"00000000000002-storage-schema.sql", "phase_storage"),
            (b"00000000000003-post-setup.sql", "phase_post_setup"),
        )
    },
}
MAX_CHUNK = 4096
MAX_LINE = 2048
MAX_BYTES = 1024 * 1024
MAX_EVENTS = 32
ERROR_PREFIX = re.compile(rb"^(?:psql:[a-zA-Z0-9_./-]{1,200}:[0-9]{1,8}: )?ERROR:  ")


def classify(chunks: Iterable[object], *, seconds: float = 120) -> tuple[str, ...]:
    # A blocking iterator is outside this pure seam: supervise owns pipe liveness.
    if not 0 < seconds <= 120:
        return ("classifier_failure",)
    deadline = time.monotonic() + seconds
    events: list[str] = []
    line = bytearray()
    discard = False
    total = 0

    def emit(event):
        if len(events) < MAX_EVENTS and event not in events:
            events.append(event)

    def finish():
        if discard:
            emit("unknown")
            return
        raw = bytes(line)
        try:
            raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            emit("unknown")
            return
        if any(value < 32 or value == 127 for value in raw):
            emit("unknown")
        elif raw in PHASES:
            emit(PHASES[raw])
        elif ERROR_PREFIX.match(raw):
            if re.search(rb'role "[^"\n]{1,256}" does not exist$', raw):
                emit("error_missing_role")
            elif b"permission denied" in raw:
                emit("error_permission")
            elif b"extension" in raw and b"is not available" in raw:
                emit("error_extension")
            else:
                emit("error_other")
        elif raw:
            emit("unknown")

    try:
        for chunk in chunks:
            if time.monotonic() >= deadline:
                emit("limit_time")
                break
            if type(chunk) is not bytes or len(chunk) > MAX_CHUNK:
                emit("unknown")
                break
            total += len(chunk)
            if total > MAX_BYTES:
                emit("limit_bytes")
                break
            for value in chunk:
                if value == 10:
                    finish()
                    line.clear()
                    discard = False
                elif not discard:
                    if len(line) == MAX_LINE:
                        line.clear()
                        discard = True
                    else:
                        line.append(value)
        if line or discard:
            emit("unknown")  # Never interpret an unterminated/truncated record.
    except Exception:  # noqa: BLE001 -- fail closed without serializing input exceptions
        # Deliberate fail-closed boundary: never serialize iterator exception text.
        emit("classifier_failure")
    return tuple(events)


TOKENS = frozenset(PHASES.values()) | {
    "unknown",
    "error_missing_role",
    "error_permission",
    "error_extension",
    "error_other",
    "classifier_failure",
    "limit_bytes",
    "limit_time",
    "spawn_failure",
}


def render(events: Iterable[str]) -> str:
    """Return only allowlisted constants, never serialize input values or errors."""
    safe = []
    try:
        for index, event in enumerate(events):
            if index >= MAX_EVENTS:
                break
            safe.append(event if type(event) is str and event in TOKENS else "unknown")
    except Exception:  # noqa: BLE001 -- arbitrary iterator errors must not escape as raw text
        return '["classifier_failure"]'  # No arbitrary iterator error is formatted.
    return json.dumps(safe, separators=(",", ":"))


def selected_state(raw: bytes) -> tuple[str, ...]:
    """Accept ONLY a preselected flat metadata object, never full Docker inspect.

    Future supervisor must request selected fields directly; this function does
    not invoke Docker or make full inspect acceptable. No raw Error is returned.
    """
    if type(raw) is not bytes or len(raw) > 4096:
        return ("metadata_unknown",)
    try:
        state = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return ("metadata_unknown",)
    if type(state) is not dict or set(state) - {"ExitCode", "OOMKilled", "Health", "Error"}:
        return ("metadata_unknown",)
    if not state:
        return ("metadata_unknown",)
    output = []
    if "ExitCode" in state:
        value = state["ExitCode"]
        if type(value) is not int or not 0 <= value <= 255:
            return ("metadata_unknown",)
        output.append(f"exit_{value}")  # Finite numeric domain, not arbitrary stream text.
    if "OOMKilled" in state:
        if type(state["OOMKilled"]) is not bool:
            return ("metadata_unknown",)
        output.append("oom_true" if state["OOMKilled"] else "oom_false")
    if "Health" in state:
        health = state["Health"]
        if type(health) is not str or health not in {"healthy", "unhealthy", "starting", "absent"}:
            return ("metadata_unknown",)
        output.append(
            {
                "healthy": "health_healthy",
                "unhealthy": "health_unhealthy",
                "starting": "health_starting",
                "absent": "health_absent",
            }[health]
        )
    if "Error" in state:
        if type(state["Error"]) is not str:
            return ("metadata_unknown",)
        output.append("engine_error" if state["Error"] else "engine_error_absent")
    return tuple(output)


@dataclass(frozen=True)
class Outcome:
    exit_code: int | None
    events: tuple[str, ...]


def supervise(argv: list[str], *, seconds: float = 120) -> Outcome:
    """Trusted-caller child stream seam; NO CLI and no command parsed from input.

    Not wired to the DB: requires separately reviewed image dependency/mount
    design. Unit tests use only synthetic local Python children.
    """
    if not 0 < seconds <= 120:
        return Outcome(None, ("classifier_failure",))
    try:
        child = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={"PATH": os.defpath},  # No inherited application credentials.
        )
    except (OSError, ValueError):
        return Outcome(None, ("spawn_failure",))
    deadline = time.monotonic() + seconds
    terminal_events = []
    poller = selectors.DefaultSelector()
    stream = child.stdout
    assert stream is not None
    descriptor = stream.fileno()
    poller.register(stream, selectors.EVENT_READ)

    def chunks():
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                terminal_events.append("limit_time")
                return
            ready = poller.select(min(remaining, 0.1))
            if not ready:
                continue
            chunk = os.read(descriptor, MAX_CHUNK)
            if not chunk:
                return
            total += len(chunk)
            if total > MAX_BYTES:
                terminal_events.append("limit_bytes")
                return
            yield chunk

    try:
        try:
            events = classify(chunks())
            # Revalidate output even if classifier implementation fails or is replaced.
            safe = tuple(json.loads(render(events)))
        except Exception:  # noqa: BLE001 -- sanitize all classifier failures before cleanup
            # This security boundary must not format arbitrary exception/stream values.
            safe = ("classifier_failure",)
    finally:
        poller.close()
        stream.close()
        try:
            child.wait(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
        # Also terminate descendants retaining the pipe after their parent exited.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            terminal_events.append("classifier_failure")
        try:
            child.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=0.2)
            except (OSError, subprocess.TimeoutExpired):
                terminal_events.append("classifier_failure")
        # Parent exit does not establish descendant quiescence; kill its own group.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            terminal_events.append("classifier_failure")
    return Outcome(child.returncode, tuple(dict.fromkeys((*safe, *terminal_events))))
