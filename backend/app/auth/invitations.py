from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.invitation_mail import InvitationMailer, InvitationMailUnavailable
from app.auth.security import check_csrf, fail, invitation_hash, invitation_token
from app.auth.settings import get_settings
from app.auth.supabase import Identity
from app.db.models import PlatformAdmin
from app.db.session import identity, user_session
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


class InviteCreated(BaseModel):
    id: UUID
    expires_at: str
    delivery: Literal["accepted_by_smtp", "unconfirmed"]


@router.post("/invitations", status_code=201, response_model=InviteCreated)
def create(
    body: InviteInput,
    request: Request,
    mutations: Annotated[ActorMutations, Depends(actor_mutations)],
):
    settings = get_settings()
    check_csrf(request, settings.public_origin)
    credential = settings.read_invitation_smtp_credential()
    if not (settings.invitation_smtp_host and settings.invitation_smtp_sender and credential):
        fail(503, "invitation_mail_unavailable", "E-Mail-Versand ist nicht verfügbar.")
    mailer = InvitationMailer(
        settings.public_origin, settings.invitation_smtp_host, settings.invitation_smtp_port,
        settings.invitation_smtp_sender, credential.get_secret_value(),
    )
    email = body.email.lower()
    token, digest = invitation_token()
    invite_id = uuid4()

    def create_invitation(db: Session):
        expiry = rpc(
            db,
            "SELECT public.notebook_create_invite(:id,:hash,:email,:workspace,:role)",
            {
                "id": invite_id,
                "hash": digest,
                "email": email,
                "workspace": body.workspace_id,
                "role": body.role,
            },
        ).scalar_one()
        return expiry.isoformat()

    expires_at = mutations.run(create_invitation)
    delivery = "accepted_by_smtp"
    try:
        mailer.send(email, token)
    except InvitationMailUnavailable:
        delivery = "unconfirmed"
    return {"id": str(invite_id), "expires_at": expires_at, "delivery": delivery}


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
def admission(
    db: Annotated[Session, Depends(user_session)],
    user: Annotated[Identity, Depends(identity)],
):
    role = db.scalar(select(PlatformAdmin.role).where(PlatformAdmin.user_id == user.id))
    return {"admitted": True, "platform_role": role}
