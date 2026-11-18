"""Personal execution boundary. Inputs are IDs and text; outputs contain no secrets.

A lookup opens a NEW short restricted-role transaction for every authorization check.
Callers must also check current workspace/notebook/job authorization (B-owned policy).
Never pass a request Session or ORM identity-map object to this service.
"""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.auth.security import fail
from app.auth.supabase import Identity
from app.connections.router import provider_config
from app.connections.supabase_store import SupabaseStore
from app.db.models import Admission, ProviderConnection
from app.db.session import engine
from app.providers.adapter import Adapter
from app.providers.types import EmbeddingResult, Message, StreamEvent


@dataclass(frozen=True)
class ConnectionState:
    id: UUID
    user_id: UUID
    provider_id: str
    secret_id: UUID = field(repr=False)
    status: str


def _lookup(actor_id: UUID, connection_id: UUID, *, expected: ConnectionState | None = None):
    with Session(engine()) as db, db.begin():
        safe = db.execute(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls AND rolname = 'notebook_app' AND session_user=current_user "
                "FROM pg_roles WHERE rolname=current_user"
            )
        ).scalar()
        if safe is not True:
            fail(503, "unsafe_database_role", "Datenbankrolle nicht freigegeben.")
        db.execute(
            text("SELECT set_config('request.jwt.claim.sub', :uid, true)"), {"uid": str(actor_id)}
        )
        if db.get(Admission, actor_id) is None:
            fail(403, "admission_required", "Eine angenommene Einladung ist erforderlich.")
        row = db.scalar(
            select(ProviderConnection).where(
                ProviderConnection.id == connection_id,
                ProviderConnection.user_id == actor_id,
            )
        )
        state = (
            ConnectionState(row.id, row.user_id, row.provider_id, row.secret_id, row.status)
            if row is not None
            else None
        )
        if expected is not None:
            if state != expected or expected.status != "active":
                fail(409, "provider_key_unavailable", "Die Modellverbindung wurde geändert.")
            return SupabaseStore(db).read(expected.id, expected.secret_id)
        return state


async def lookup_connection(actor_id, connection_id):
    return await asyncio.to_thread(_lookup, actor_id, connection_id)


async def read_secret(state):
    return await asyncio.to_thread(_lookup, state.user_id, state.id, expected=state)


@contextmanager
def discovery_session(user: Identity):
    """Fresh verified-actor transaction; never used across external I/O."""
    with Session(engine().execution_options(isolation_level="READ COMMITTED")) as db, db.begin():
        safe = db.execute(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls AND "
                "rolname = 'notebook_app' AND session_user=current_user "
                "FROM pg_roles WHERE rolname=current_user"
            )
        ).scalar()
        if safe is not True:
            fail(503, "unsafe_database_role", "Datenbankrolle nicht freigegeben.")
        db.execute(
            text("SELECT set_config('request.jwt.claim.sub', :uid, true)"),
            {"uid": str(user.id)},
        )
        db.execute(
            text("SELECT set_config('request.jwt.claim.email', :email, true)"),
            {"email": user.email},
        )
        if db.get(Admission, user.id) is None:
            fail(403, "admission_required", "Eine angenommene Einladung ist erforderlich.")
        yield db


class DiscoveryRepository:
    def __init__(self, user: Identity, session_factory=discovery_session):
        # Identity must come from the route's verifier, not caller-supplied IDs.
        self.user = user
        self.session_factory = session_factory

    def lookup(self, actor_id, connection_id):
        if actor_id != self.user.id:
            fail(403, "connection_forbidden", "Verbindung nicht freigegeben.")
        with self.session_factory(self.user) as db:
            row = db.scalar(
                select(ProviderConnection).where(
                    ProviderConnection.id == connection_id,
                    ProviderConnection.user_id == actor_id,
                )
            )
            if row is None:
                return None
            return ConnectionState(row.id, row.user_id, row.provider_id, row.secret_id, row.status)

    def publish(self, state, selected_model):
        from app.connections.router import metadata

        with self.session_factory(self.user) as db:
            row = db.scalar(
                select(ProviderConnection)
                .where(
                    ProviderConnection.id == state.id,
                    ProviderConnection.user_id == self.user.id,
                )
                .with_for_update()
            )
            if (
                state.user_id != self.user.id
                or row is None
                or ConnectionState(row.id, row.user_id, row.provider_id, row.secret_id, row.status)
                != state
                or db.scalar(select(Admission.user_id).where(Admission.user_id == self.user.id))
                is None
            ):
                fail(409, "connection_inactive", "Verbindung wurde geändert oder getrennt.")
            values = (
                {"last_tested_at": datetime.now(UTC)}
                if selected_model is None
                else {"model": selected_model}
            )
            # No admission row lock: SELECT-only grants. A later admission delete
            # can race this statement snapshot/commit under READ COMMITTED.
            changed = db.scalar(
                update(ProviderConnection)
                .where(
                    ProviderConnection.id == state.id,
                    ProviderConnection.user_id == self.user.id,
                    ProviderConnection.provider_id == state.provider_id,
                    ProviderConnection.secret_id == state.secret_id,
                    ProviderConnection.status == "active",
                    select(Admission.user_id).where(Admission.user_id == self.user.id).exists(),
                )
                .values(**values)
                .returning(ProviderConnection.id)
                .execution_options(synchronize_session=False)
            )
            if changed is None:
                fail(409, "connection_inactive", "Verbindung wurde geändert oder getrennt.")
            # Materialize safe fields only; no expired ORM object escapes commit.
            result = metadata(row)
            result.update(values)
        return result  # factory has committed; failure/unknown outcome is not replayed


class ExecutionService:
    def __init__(
        self,
        *,
        lookup=lookup_connection,
        read_secret=read_secret,
        adapter_factory: Callable[..., Any] = Adapter,
        resolve_provider=provider_config,
        poll_interval=0.25,
    ):
        self.lookup = lookup
        self.read_secret = read_secret
        self.adapter_factory = adapter_factory
        self.resolve_provider = resolve_provider
        self.poll_interval = poll_interval

    async def _owned(self, actor_id, connection_id):
        state = await self.lookup(actor_id, connection_id)
        if (
            state is None
            or state.id != connection_id
            or state.user_id != actor_id
            or state.status != "active"
        ):
            raise HTTPException(
                409,
                detail={
                    "code": "connection_inactive",
                    "message": "Eigene aktive Verbindung erforderlich.",
                },
            )
        return state

    async def _unchanged(self, actor_id, connection_id, state):
        if await self._owned(actor_id, connection_id) != state:
            fail(409, "connection_inactive", "Verbindung wurde geändert oder getrennt.")

    async def _resolve(self, actor_id, connection_id):
        state = await self._owned(actor_id, connection_id)
        # No key-bearing object is returned to callers/jobs/DTOs.
        key = await self.read_secret(state)
        await self._unchanged(actor_id, connection_id, state)
        return state, self.adapter_factory(self.resolve_provider(state.provider_id), key)

    async def _watch(self, actor_id, connection_id, state):
        while True:
            await asyncio.sleep(self.poll_interval)
            await self._unchanged(actor_id, connection_id, state)

    async def _guard(self, operation, actor_id, connection_id, state):
        task = asyncio.create_task(operation)
        watch = asyncio.create_task(self._watch(actor_id, connection_id, state))
        try:
            done, _ = await asyncio.wait((task, watch), return_when=asyncio.FIRST_COMPLETED)
            if watch in done:
                await watch
            result = await task
            # Recheck also after short operations, before exposing the result.
            await self._unchanged(actor_id, connection_id, state)
            return result
        finally:
            for pending in (task, watch):
                pending.cancel()
            await asyncio.gather(task, watch, return_exceptions=True)

    async def discover(self, *, actor_id, connection_id, publish, selected_model=None):
        try:
            state, adapter = await self._resolve(actor_id, connection_id)
            models = await self._guard(adapter.models(), actor_id, connection_id, state)
            if selected_model is not None and selected_model not in {m["id"] for m in models}:
                fail(400, "model_unavailable", "Bitte ein verfügbares Modell auswählen.")
            result = await asyncio.to_thread(publish, state, selected_model)
            return {"models": models, "status": "ok"} if selected_model is None else result
        except HTTPException as error:
            # Only fixed application errors are safe; external injected/SDK errors
            # can otherwise carry arbitrary sensitive detail.
            codes = {
                "connection_inactive": (409, "Verbindung wurde geändert oder getrennt."),
                "admission_required": (403, "Eine angenommene Einladung ist erforderlich."),
                "unsafe_database_role": (503, "Datenbankrolle nicht freigegeben."),
                "model_unavailable": (400, "Bitte ein verfügbares Modell auswählen."),
            }
            code = None
            try:
                detail = error.detail
                # Reject subclasses: their getters/hash/equality may run foreign
                # code or make an untrusted value compare equal to a safe key.
                if type(detail) is dict:
                    candidate = dict.get(detail, "code")
                    if type(candidate) is str:
                        code = candidate
            except Exception:  # noqa: BLE001 — hostile detail getter; cancellation still propagates
                code = None
            for trusted_code, (status, message) in codes.items():
                if code == trusted_code:
                    fail(status, trusted_code, message)
            fail(503, "discovery_unavailable", "Modellabfrage derzeit nicht verfügbar.")
        except Exception:  # noqa: BLE001 — sanitize arbitrary external/driver errors at API boundary
            fail(503, "discovery_unavailable", "Modellabfrage derzeit nicht verfügbar.")

    async def embed(
        self, *, actor_id: UUID, connection_id: UUID, model: str, texts: list[str]
    ) -> EmbeddingResult:
        state, adapter = await self._resolve(actor_id, connection_id)
        return await self._guard(
            adapter.embed(model=model, texts=texts), actor_id, connection_id, state
        )

    async def stream(
        self,
        *,
        actor_id: UUID,
        connection_id: UUID,
        model: str,
        messages: list[Message],
        max_output_tokens: int,
    ) -> AsyncGenerator[StreamEvent, None]:
        state, adapter = await self._resolve(actor_id, connection_id)
        queue = asyncio.Queue(maxsize=1)

        async def produce():
            async with aclosing(
                adapter.stream(model=model, messages=messages, max_output_tokens=max_output_tokens)
            ) as upstream:
                async for event in upstream:
                    await queue.put(event)

        # Producer owns the stream/timeout context in a single task; watcher cancels
        # it even while downstream pauses at a yield or upstream stalls before data.
        guarded = asyncio.create_task(self._guard(produce(), actor_id, connection_id, state))
        receive = None
        try:
            while True:
                receive = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    (receive, guarded), return_when=asyncio.FIRST_COMPLETED
                )
                if guarded in done:
                    await guarded  # discard buffered output on authorization/error failure
                    if receive not in done and queue.empty():
                        return
                event = await receive
                await self._unchanged(actor_id, connection_id, state)
                yield event
                if guarded.done() and queue.empty():
                    await guarded
                    return
        finally:
            guarded.cancel()
            if receive is not None:
                receive.cancel()
            await asyncio.gather(guarded, *([receive] if receive else []), return_exceptions=True)
