"""Local one-time admin admission for an existing verified Supabase account.

Run with a separately provisioned NOTEBOOK_MIGRATION_DATABASE_URL; never expose as HTTP.
"""

import argparse
import os
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


def bootstrap(email: str, database_url: str):
    with create_engine(database_url, hide_parameters=True).begin() as db:
        user = db.execute(
            text(
                "SELECT id FROM auth.users WHERE lower(email)=lower(:email) AND email_confirmed_at IS NOT NULL"
            ),
            {"email": email},
        ).scalar_one_or_none()
        if user is None:
            raise ValueError("Exactly one verified Supabase account must exist for the email.")
        if db.execute(text("SELECT EXISTS(SELECT 1 FROM public.platform_admins)")).scalar():
            raise ValueError("Bootstrap already completed; use a reviewed administrator procedure.")
        # Verified account/privileged configuration precede the quota gate; gate
        # must precede the platform-admin lock and all admission mutations.
        db.execute(text("SELECT public.quota_gate()"))
        # Serialize competing bootstrap commands before rechecking the empty-admin condition.
        db.execute(text("LOCK TABLE public.platform_admins IN EXCLUSIVE MODE"))
        if db.execute(text("SELECT EXISTS(SELECT 1 FROM public.platform_admins)")).scalar():
            raise ValueError("Bootstrap already completed.")
        db.execute(
            text("INSERT INTO public.app_admissions VALUES(:uid) ON CONFLICT DO NOTHING"),
            {"uid": user},
        )
        db.execute(
            text("INSERT INTO public.platform_admins(user_id,role) VALUES(:uid,:role)"),
            {"uid": user, "role": "superadmin"},
        )
        db.execute(
            text(
                "INSERT INTO public.workspaces(id,name,personal_owner_id) VALUES(:id,:name,:uid) ON CONFLICT(personal_owner_id) DO NOTHING"
            ),
            {"id": uuid4(), "name": "Mein Workspace", "uid": user},
        )
        db.execute(
            text(
                "INSERT INTO public.workspace_members SELECT id,:uid,'owner' FROM public.workspaces WHERE personal_owner_id=:uid ON CONFLICT DO NOTHING"
            ),
            {"uid": user},
        )
    return user


def promote_confirmed_superadmin(email: str, database_url: str):
    """Privileged operator-only promotion of an existing confirmed app administrator."""
    with create_engine(database_url, hide_parameters=True).begin() as db:
        db.execute(text("LOCK TABLE public.platform_admins IN EXCLUSIVE MODE"))
        user = db.execute(
            text(
                """SELECT u.id FROM auth.users u
                JOIN public.app_admissions a ON a.user_id=u.id
                JOIN public.platform_admins p ON p.user_id=u.id
                WHERE lower(u.email)=lower(:email) AND u.email_confirmed_at IS NOT NULL"""
            ),
            {"email": email},
        ).scalar_one_or_none()
        if user is None:
            raise ValueError("An existing confirmed administrator is required.")
        db.execute(
            text("UPDATE public.platform_admins SET role=:role WHERE user_id=:uid"),
            {"uid": user, "role": "superadmin"},
        )
    return user


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    try:
        user = bootstrap(args.email, os.environ["NOTEBOOK_MIGRATION_DATABASE_URL"])
    except (SQLAlchemyError, ValueError, KeyError):
        parser.exit(
            1, "Bootstrap failed; verify privileged configuration and existing verified account.\n"
        )
    print(f"Administrator admitted: {user}")


if __name__ == "__main__":
    main()
