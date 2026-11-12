from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import check_csrf, fail
from app.auth.settings import get_settings
from app.auth.supabase import Identity
from app.db.models import Notebook
from app.db.session import identity, user_session
from app.mutations import ActorMutations, actor_mutations
from app.workspaces.router import membership

router = APIRouter(prefix="/api", tags=["notebooks"])


class NotebookInput(BaseModel):
    title: str = Field(min_length=1, max_length=200, pattern=r"\S")


def serialize(n):
    return {"id": str(n.id), "workspace_id": str(n.workspace_id), "title": n.title}


@router.get("/workspaces/{workspace_id}/notebooks")
def list_notebooks(
    workspace_id: UUID,
    db: Annotated[Session, Depends(user_session)],
    user: Annotated[Identity, Depends(identity)],
):
    membership(db, workspace_id, user.id)
    return {
        "items": [
            serialize(n)
            for n in db.scalars(
                select(Notebook)
                .where(Notebook.workspace_id == workspace_id)
                .order_by(Notebook.title)
                .limit(100)
            )
        ]
    }


@router.post("/workspaces/{workspace_id}/notebooks", status_code=201)
def create_notebook(
    workspace_id: UUID,
    body: NotebookInput,
    request: Request,
    mutations: Annotated[ActorMutations, Depends(actor_mutations)],
):
    check_csrf(request, get_settings().public_origin)

    def create(db: Session):
        membership(db, workspace_id, mutations.user.id, write=True)
        n = Notebook(workspace_id=workspace_id, title=body.title.strip())
        db.add(n)
        db.flush()
        return serialize(n)

    return mutations.run(create)


def editable(db, notebook_id, user):
    n = db.get(Notebook, notebook_id)
    if n is None:
        fail(404, "notebook_not_found", "Notebook nicht gefunden.")
    membership(db, n.workspace_id, user.id, write=True)
    return n


@router.patch("/notebooks/{notebook_id}")
def rename(
    notebook_id: UUID,
    body: NotebookInput,
    request: Request,
    mutations: Annotated[ActorMutations, Depends(actor_mutations)],
):
    check_csrf(request, get_settings().public_origin)

    def update(db: Session):
        n = editable(db, notebook_id, mutations.user)
        n.title = body.title.strip()
        db.flush()
        return serialize(n)

    return mutations.run(update)


@router.delete("/notebooks/{notebook_id}", status_code=204)
def delete(
    notebook_id: UUID,
    request: Request,
    mutations: Annotated[ActorMutations, Depends(actor_mutations)],
):
    check_csrf(request, get_settings().public_origin)

    def remove(db: Session):
        db.delete(editable(db, notebook_id, mutations.user))
        db.flush()

    mutations.run(remove)
    return Response(status_code=204)
