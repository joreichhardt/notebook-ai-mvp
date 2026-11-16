"""Publication is a proposal after operator-reviewed, release-bound evidence."""

import importlib

import pytest


def module():
    return importlib.import_module("deploy.poc_publication")


def evidence(m):
    return {
        "profile": "login-workspace-v1",
        "hostname": "note.yokoma.de",
        "commit": "a" * 40,
        "completed_at": 1800000000,
        "first_admin_email": "johannes@yokoma.de",
        "checks": {
            name: {"passed": True, "artifact_sha256": "b" * 64} for name in m.REQUIRED_CHECKS
        },
        "images": {
            "api": "ghcr.io/joreichhardt/notebook-ai-mvp-api@sha256:" + "1" * 64,
            "frontend": "ghcr.io/joreichhardt/notebook-ai-mvp-frontend@sha256:" + "2" * 64,
            "auth": m.AUTH_IMAGE,
            "db": m.DB_IMAGE,
        },
    }


@pytest.mark.parametrize("roles", [("api",), ("frontend",), ("api", "frontend")])
def test_publication_rejects_old_poc_image_namespace(roles):
    m = module()
    value = evidence(m)
    for role in roles:
        value["images"][role] = value["images"][role].replace(
            "notebook-ai-mvp-", "notebook-ai-poc-"
        )
    with pytest.raises(m.PublicationError):
        m.propose(
            value,
            approved_commit="a" * 40,
            live={
                "status": "ready",
                "profile": "login-workspace-v1",
                "release": "a" * 40,
                "documents": False,
                "chat": False,
            },
            now=1800000010,
        )


def test_valid_evidence_emits_only_fixed_proposal():
    m = module()
    result = m.propose(
        evidence(m),
        approved_commit="a" * 40,
        live={
            "status": "ready",
            "profile": "login-workspace-v1",
            "release": "a" * 40,
            "documents": False,
            "chat": False,
        },
        now=1800000010,
    )
    assert result == {
        "hostname": "note.yokoma.de",
        "service": "http://127.0.0.1:18191",
        "httpHostHeader": "note.yokoma.de",
        "profile": "login-workspace-v1",
        "commit": "a" * 40,
        "action": "proposal-only",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "commit",
        "host",
        "profile",
        "stale",
        "future",
        "bool",
        "missing",
        "digest",
        "auth",
        "artifact",
        "extra",
    ],
)
def test_evidence_fails_closed(mutation):
    m = module()
    value = evidence(m)
    if mutation == "commit":
        value["commit"] = "c" * 40
    elif mutation == "host":
        value["hostname"] = "other.invalid"
    elif mutation == "profile":
        value["profile"] = "production"
    elif mutation == "stale":
        value["completed_at"] -= 3601
    elif mutation == "future":
        value["completed_at"] += 300
    elif mutation == "bool":
        value["checks"]["real_email_confirmation"]["passed"] = "true"
    elif mutation == "missing":
        del value["checks"]["cross_user_isolation"]
    elif mutation == "digest":
        value["images"]["api"] = "ghcr.io/joreichhardt/notebook-ai-mvp-api:latest"
    elif mutation == "auth":
        value["images"]["auth"] = "supabase/gotrue@sha256:" + "3" * 64
    elif mutation == "artifact":
        value["checks"]["real_email_confirmation"]["artifact_sha256"] = ""
    elif mutation == "extra":
        value["images"]["worker"] = value["images"]["api"]
    with pytest.raises(m.PublicationError):
        m.propose(
            value,
            approved_commit="a" * 40,
            live={
                "status": "ready",
                "profile": "login-workspace-v1",
                "release": "a" * 40,
                "documents": False,
                "chat": False,
            },
            now=1800000010,
        )


@pytest.mark.parametrize(
    "live",
    [
        {"status": "ok"},
        {"status": "ready", "release": "a" * 40},
        {
            "status": "ready",
            "profile": "login-workspace-v1",
            "release": "c" * 40,
            "documents": False,
            "chat": False,
        },
        {
            "status": "ready",
            "profile": "login-workspace-v1",
            "release": "a" * 40,
            "documents": True,
            "chat": False,
        },
    ],
)
def test_liveness_or_production_or_different_release_never_authorizes(live):
    m = module()
    with pytest.raises(m.PublicationError):
        m.propose(evidence(m), approved_commit="a" * 40, live=live, now=1800000010)
