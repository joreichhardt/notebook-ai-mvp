from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.security import check_csrf, fail, invitation_hash, invitation_token
from app.auth.settings import get_settings
from app.db.session import user_session
from app.mutations import ActorMutations, actor_mutations

router = APIRouter(prefix="/api", tags=["invitations", "members"])


class InviteInput(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    workspace_id: UUID | None = None
    role: Literal["owner", "editor", "viewer"] = "viewer"


class AcceptInput(BaseModel):
    token: str = Field(min_length=32, max_length=128)


class RoleInput(BaseModel):
    role: Literal["owner", "editor", "viewer"]


def rpc(db: Session, statement: str, params: dict):
    try:
        return db.execute(text(statement), params)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42501":
            fail(403, "permission_denied", "Keine Berechtigung für diese Aktion.")
        if getattr(exc.orig, "sqlstate", None) == "22023":
            fail(
                409,
                "invitation_or_membership_invalid",
                "Einladung oder Mitgliedsänderung ist nicht gültig.",
            )
        raise


@router.post("/invitations", status_code=201)
def create(
    body: InviteInput,
    request: Request,
    mutations: Annotated[ActorMutations, Depends(actor_mutations)],
):
    check_csrf(request, get_settings().public_origin)
    token, digest = invitation_token()
    invite_id = uuid4()

    def create_invitation(db: Session):
        expiry = rpc(
            db,
            "SELECT public.notebook_create_invite(:id,:hash,:email,:workspace,:role)",
            {
                "id": invite_id,
                "hash": digest,
                "email": body.email.lower(),
                "workspace": body.workspace_id,
                "role": body.role,
            },
        ).scalar_one()
        return {"id": str(invite_id), "token": token, "expires_at": expiry.isoformat()}

    return mutations.run(create_invitation)


@router.post("/invitations/accept")
def accept(
    body: AcceptInput,
    request: Request,
    mutations: Annotated[ActorMutations, Depends(actor_mutations)],
):
    check_csrf(request, get_settings().public_origin)
    digest = invitation_hash(body.token)

    def accept_invitation(db: Session):
        row = (
            rpc(db, "SELECT * FROM public.notebook_accept_invite(:hash)", {"hash": digest})
            .mappings()
            .one()
        )
        return dict(row)

    # Accepting an invitation is precisely how a verified user gains admission.
    return mutations.run(accept_invitation, require_admission=False)


@router.get("/workspaces/{workspace_id}/members")
def members(workspace_id: UUID, db: Annotated[Session, Depends(user_session)]):
    rows = rpc(
        db, "SELECT * FROM public.notebook_members(:workspace)", {"workspace": workspace_id}
    ).mappings()
    return {"items": [dict(row) for row in rows]}


@router.patch("/workspaces/{workspace_id}/members/{user_id}")
def change(
    workspace_id: UUID,
    user_id: UUID,
    body: RoleInput,
    request: Request,
    mutations: Annotated[ActorMutations, Depends(actor_mutations)],
):
    check_csrf(request, get_settings().public_origin)

    def change_member(db: Session):
        rpc(
            db,
            "SELECT public.notebook_change_member(:workspace,:user,:role)",
            {"workspace": workspace_id, "user": user_id, "role": body.role},
        )
        return {"user_id": str(user_id), "role": body.role}

    return mutations.run(change_member)


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
def remove(
    workspace_id: UUID,
    user_id: UUID,
    request: Request,
    mutations: Annotated[ActorMutations, Depends(actor_mutations)],
):
    check_csrf(request, get_settings().public_origin)

    def remove_member(db: Session):
        rpc(
            db,
            "SELECT public.notebook_change_member(:workspace,:user,:role)",
            {"workspace": workspace_id, "user": user_id, "role": None},
        )

    mutations.run(remove_member)
    return Response(status_code=204)


@router.get("/auth/admission")
def admission(db: Annotated[Session, Depends(user_session)]):
    return {"admitted": True}
