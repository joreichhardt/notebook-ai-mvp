"""Never run against the default PostgreSQL16 fixtures or inferred URLs."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("A5_STORAGE_PHASE_A") != "1"
    or os.environ.get("A5_STORAGE_PHASE_A_URL") != "postgresql://db:5432/postgres",
    reason="requires explicit isolated real Supabase Phase A enable and URL",
)


def test_real_upstream_acl_boundary():
    from backend.tests.storage_real.phase_a import verify_phase_a

    verify_phase_a()
