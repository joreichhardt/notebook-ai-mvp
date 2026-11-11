from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.auth.security import SESSION_COOKIE, fail
from app.auth.settings import get_settings
from app.auth.supabase import Identity, SupabaseAuth
from app.db.models import Admission


@lru_cache
def engine():
    url = get_settings().database_url.get_secret_value()
    if not url:
        fail(503, "database_unconfigured", "Datenbank ist noch nicht eingerichtet.")
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)


async def identity(request: Request) -> Identity:
    return await SupabaseAuth(get_settings()).identity(request.cookies.get(SESSION_COOKIE, ""))


def user_session(user: Annotated[Identity, Depends(identity)]):
    with Session(engine()) as session, session.begin():
        # The connection role itself must be non-owner, non-superuser and NOBYPASSRLS.
        safe = session.execute(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls AND "
                "rolname = 'notebook_app' FROM pg_roles WHERE rolname=current_user"
            )
        ).scalar()
        if safe is not True:
            fail(503, "unsafe_database_role", "Datenbankrolle nicht freigegeben.")
        session.execute(
            text("SELECT set_config('request.jwt.claim.sub', :uid, true)"), {"uid": str(user.id)}
        )
        if session.get(Admission, user.id) is None:
            fail(403, "admission_required", "Eine angenommene Einladung ist erforderlich.")
        yield session
