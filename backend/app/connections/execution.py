"""Personal execution boundary. Inputs are IDs and text; outputs contain no secrets.

A lookup opens a NEW short restricted-role transaction for every authorization check.
Callers must also check current workspace/notebook/job authorization (B-owned policy).
Never pass a request Session or ORM identity-map object to this service.
"""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth.security import fail
from app.connections.router import provider_config
from app.connections.vault import BitwardenStore
from app.db.models import Admission, ProviderConnection
from app.db.session import engine
from app.providers.adapter import Adapter
from app.providers.config import settings
from app.providers.types import EmbeddingResult, Message, StreamEvent


@dataclass(frozen=True)
class ConnectionState:
    id: UUID
    user_id: UUID
    provider_id: str
    secret_id: UUID = field(repr=False)
    status: str


def _lookup(actor_id: UUID, connection_id: UUID) -> ConnectionState | None:
    with Session(engine()) as db, db.begin():
        safe = db.execute(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls AND rolname = 'notebook_app' "
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
        if row is None:
            return None
        return ConnectionState(row.id, row.user_id, row.provider_id, row.secret_id, row.status)


async def lookup_connection(actor_id, connection_id):
    return await asyncio.to_thread(_lookup, actor_id, connection_id)


async def read_secret(state):
    return await asyncio.to_thread(
        BitwardenStore(settings()).read,
        state.secret_id,
        state.user_id,
        state.id,
    )


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
