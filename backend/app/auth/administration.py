"""Global role administration; database RPCs enforce superadmin authority."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.security import check_csrf, fail
from app.auth.settings import get_settings
from app.db.session import user_session
from app.mutations import ActorMutations, actor_mutations

router = APIRouter(prefix="/api/admin", tags=["administration"])


class AdminGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID


def admin_rpc(db: Session, user_id: UUID | None = None):
    try:
        if user_id is None:
            return db.execute(text("SELECT * FROM public.notebook_platform_administrators()"))
        return db.execute(
            text("SELECT * FROM public.notebook_grant_app_admin(:user_id)"),
            {"user_id": user_id},
        )
    except DBAPIError as exc:
        code = getattr(exc.orig, "sqlstate", None)
        if code == "42501":
            fail(403, "superadmin_required", "Nur Superadmins dürfen Adminrechte verwalten.")
        if code == "22023":
            fail(409, "admitted_user_required", "Der Nutzer muss zuerst eine Einladung annehmen.")
        raise


@router.get("/administrators")
def administrators(db: Annotated[Session, Depends(user_session)]):
    rows = admin_rpc(db)
    return {"items": [dict(row) for row in rows.mappings()]}


@router.post("/administrators")
def grant_administrator(
    body: AdminGrant,
    request: Request,
    mutations: Annotated[ActorMutations, Depends(actor_mutations)],
):
    check_csrf(request, get_settings().public_origin)

    def grant(db: Session):
        row = admin_rpc(db, body.user_id).mappings().one()
        return dict(row)

    return mutations.run(grant)
