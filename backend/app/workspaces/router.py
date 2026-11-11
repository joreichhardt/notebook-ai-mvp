from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import fail
from app.auth.supabase import Identity
from app.db.models import Member, Workspace
from app.db.session import identity, user_session

router = APIRouter(prefix="/api", tags=["workspaces"])


def membership(db: Session, workspace_id: UUID, user_id: UUID, write=False):
    member = db.get(Member, (workspace_id, user_id))
    if member is None or (write and member.role not in ("owner", "editor")):
        fail(403, "workspace_access_denied", "Keine Berechtigung für diesen Workspace.")
    return member


@router.get("/workspaces")
def workspaces(
    db: Annotated[Session, Depends(user_session)], user: Annotated[Identity, Depends(identity)]
):
    rows = db.execute(select(Workspace, Member.role).join(Member).where(Member.user_id == user.id))
    return {"items": [{"id": str(w.id), "name": w.name, "role": role} for w, role in rows]}
