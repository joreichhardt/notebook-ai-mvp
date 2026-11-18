from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth.security import check_csrf, fail
from app.auth.settings import get_settings
from app.auth.supabase import Identity
from app.connections.supabase_store import SupabaseStore
from app.db.models import ProviderConnection
from app.db.session import identity, user_session
from app.providers.config import settings

router = APIRouter(prefix="/api", tags=["connections"])
User = Annotated[Identity, Depends(identity)]
DB = Annotated[Session, Depends(user_session)]


def metadata(row):
    return {
        "id": str(row.id),
        "provider_id": row.provider_id,
        "label": row.label,
        "model": row.model,
        "status": row.status,
        "last_tested_at": row.last_tested_at,
    }


def owned(db, user, connection_id) -> ProviderConnection:
    row = db.scalar(
        select(ProviderConnection)
        .where(ProviderConnection.id == connection_id, ProviderConnection.user_id == user.id)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(
            404, detail={"code": "connection_not_found", "message": "Verbindung nicht gefunden."}
        )
    return row


def provider_config(provider_id):
    provider = next((p for p in settings().endpoints if p.id == provider_id), None)
    if provider is None:
        fail(400, "provider_not_allowed", "Bitte einen verfügbaren Anbieter auswählen.")
    return provider


@router.get("/providers")
def providers(user: User, db: DB):
    config = settings()
    return {
        "items": [
            {"id": p.id, "label": p.label, "kind": p.kind, "requires_api_key": True}
            for p in config.endpoints
        ]
        if config.configured
        else []
    }


@router.get("/connections")
def connections(user: User, db: DB):
    rows = db.scalars(select(ProviderConnection).where(ProviderConnection.user_id == user.id))
    return {"items": [metadata(r) for r in rows]}


class Connect(BaseModel):
    provider_id: str = Field(max_length=64)
    label: str = Field(min_length=1, max_length=120)
    api_key: SecretStr = Field(min_length=1, max_length=16384)


@router.post("/connections", status_code=201)
def connect(body: Connect, request: Request, user: User, db: DB):
    check_csrf(request, get_settings().public_origin)
    provider_config(body.provider_id)
    connection_id = uuid4()
    secret_id = SupabaseStore(db).create(connection_id, body.api_key.get_secret_value())
    row = ProviderConnection(
        id=connection_id,
        user_id=user.id,
        provider_id=body.provider_id,
        label=body.label,
        secret_id=secret_id,
        status="active",
    )
    db.add(row)
    db.flush()
    return metadata(row)


def discovery_repository(user: User):
    # Local import breaks execution's existing provider_config dependency cycle.
    from app.connections.execution import DiscoveryRepository

    return DiscoveryRepository(user)


DiscoveryRepo = Annotated[Any, Depends(discovery_repository)]


def discovery_service(repository: DiscoveryRepo):
    from app.connections.execution import ExecutionService

    async def lookup(actor_id, connection_id):
        return await run_in_threadpool(repository.lookup, actor_id, connection_id)

    return ExecutionService(lookup=lookup)


Discovery = Annotated[Any, Depends(discovery_service)]


@router.post("/connections/{connection_id}/test")
async def test(
    connection_id: UUID,
    request: Request,
    user: User,
    service: Discovery,
    repository: DiscoveryRepo,
):
    check_csrf(request, get_settings().public_origin)
    return await service.discover(
        actor_id=user.id, connection_id=connection_id, publish=repository.publish
    )


class SelectModel(BaseModel):
    model: str = Field(min_length=1, max_length=200)


@router.patch("/connections/{connection_id}")
async def select_model(
    connection_id: UUID,
    body: SelectModel,
    request: Request,
    user: User,
    service: Discovery,
    repository: DiscoveryRepo,
):
    check_csrf(request, get_settings().public_origin)
    return await service.discover(
        actor_id=user.id,
        connection_id=connection_id,
        selected_model=body.model,
        publish=repository.publish,
    )


class ReplaceKey(BaseModel):
    api_key: SecretStr = Field(min_length=1, max_length=16384)


@router.put("/connections/{connection_id}/key")
def replace_key(connection_id: UUID, body: ReplaceKey, request: Request, user: User, db: DB):
    check_csrf(request, get_settings().public_origin)
    row = owned(db, user, connection_id)
    provider_config(row.provider_id)
    store = SupabaseStore(db)
    store.delete(row.id, row.secret_id)
    row.secret_id = store.create(row.id, body.api_key.get_secret_value())
    row.last_tested_at = None
    db.flush()
    return metadata(row)


@router.delete("/connections/{connection_id}", status_code=204)
def disconnect(connection_id: UUID, request: Request, user: User, db: DB):
    check_csrf(request, get_settings().public_origin)
    row = owned(db, user, connection_id)
    SupabaseStore(db).delete(row.id, row.secret_id)
    db.delete(row)
    db.flush()
    return Response(status_code=204)
