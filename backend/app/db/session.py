from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
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
    parsed = make_url(url)
    # Use the project's pinned psycopg3 driver, preserving TLS and other DSN options.
    if parsed.drivername not in ("postgresql", "postgresql+psycopg"):
        fail(503, "database_unconfigured", "Datenbankkonfiguration nicht unterstützt.")
    parsed = parsed.set(drivername="postgresql+psycopg")
    options = parsed.query.get("options", "")
    if not isinstance(options, str):
        fail(503, "database_unconfigured", "Datenbankkonfiguration nicht unterstützt.")
    # Final startup -c values override earlier DSN settings. No idle-in-transaction
    # cutoff: legacy Connections can perform non-retried external work while idle.
    return create_engine(
        parsed,
        pool_pre_ping=True,
        hide_parameters=True,
        pool_timeout=1,
        connect_args={
            "connect_timeout": 3,
            "options": f"{options} -c statement_timeout=3000 -c lock_timeout=1000".strip(),
        },
    )


async def identity(request: Request) -> Identity:
    return await SupabaseAuth(get_settings()).identity(request.cookies.get(SESSION_COOKIE, ""))


def authenticated_session(user: Annotated[Identity, Depends(identity)]):
    with Session(engine()) as session, session.begin():
        # Require a direct restricted login, not SET ROLE from a privileged session.
        # Equal current/session principals share the checked non-super/NOBYPASS flags.
        safe = session.execute(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls AND "
                "rolname = 'notebook_app' AND session_user=current_user "
                "FROM pg_roles WHERE rolname=current_user"
            )
        ).scalar()
        if safe is not True:
            fail(503, "unsafe_database_role", "Datenbankrolle nicht freigegeben.")
        session.execute(
            text("SELECT set_config('request.jwt.claim.sub', :uid, true)"), {"uid": str(user.id)}
        )
        session.execute(
            text("SELECT set_config('request.jwt.claim.email', :email, true)"),
            {"email": user.email},
        )
        yield session


def user_session(
    session: Annotated[Session, Depends(authenticated_session)],
    user: Annotated[Identity, Depends(identity)],
):
    if session.get(Admission, user.id) is None:
        fail(403, "admission_required", "Eine angenommene Einladung ist erforderlich.")
    yield session
