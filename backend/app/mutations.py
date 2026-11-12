"""Actor-bound, DB-only HTTP writes; commit before returning materialized DTOs.

Identity verification and CSRF are outside retry. Every attempt opens a fresh
restricted session, rechecks admission and runs authorization and writes together.
Callbacks must contain no network/provider/secret operations or lazy results.
"""

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.auth.security import fail
from app.auth.supabase import Identity
from app.db.models import Admission
from app.db.quota import quota_gate
from app.db.session import authenticated_session, identity
from app.documents.transactions import QuotaUnavailable, short_transaction


@dataclass(frozen=True)
class ActorMutations:
    user: Identity

    def run[T](self, operation: Callable[[Session], T], *, require_admission: bool = True) -> T:
        def attempt() -> T:
            with contextmanager(authenticated_session)(self.user) as db:
                if require_admission and db.get(Admission, self.user.id) is None:
                    fail(403, "admission_required", "Eine angenommene Einladung ist erforderlich.")
                quota_gate(db)
                return operation(db)  # context commits/closes before attempt returns

        try:
            return short_transaction(attempt)
        except QuotaUnavailable as exc:
            if exc.code == "quota_capacity":
                fail(429, "quota_capacity", "Kapazität vorübergehend ausgeschöpft.")
            code = "quota_busy" if exc.code == "quota_gate_busy" else "database_unavailable"
            fail(503, code, "Speichern vorübergehend nicht möglich.")


def actor_mutations(user: Annotated[Identity, Depends(identity)]) -> ActorMutations:
    # Separate injectable dependency: legacy GET/connection overrides remain intact.
    return ActorMutations(user)
