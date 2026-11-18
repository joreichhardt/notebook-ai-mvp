import datetime as dt

import pytest

from deploy import public_release_scan as public
from deploy import test_db_secret_scan as secret

REVISION = "a" * 40


def check(kind, findings, **override):
    image = public.SCANS[kind].IMAGE
    report = {
        "SchemaVersion": 2,
        "ArtifactName": image,
        "ArtifactType": "container_image",
        "Results": [findings],
    }
    args = {
        "host": public.HOST,
        "image": image,
        "release": REVISION,
        "approved_release": REVISION,
        "today": dt.date(2026, 11, 18),
        "file_hash": secret.PUBLIC_FILE_SHA256,
        **override,
    }
    return public.evaluate(kind, report, **args)


@pytest.mark.parametrize("kind", ["auth", "db", "db-secret"])
def test_public_scope_and_exact_revision(kind):
    result = {"Class": "os-pkgs"}
    assert check(kind, result) == 0
    for override in (
        {"host": "other.example.test"},
        {"approved_release": "b" * 40},
        {"today": public.EXPIRES},
        {"image": "other"},
    ):
        with pytest.raises(ValueError):
            check(kind, result, **override)


def test_only_verified_public_file_is_accepted():
    result = {
        "Class": "secret",
        "Target": secret.TARGET,
        "Secrets": [{"RuleID": "private-key", "Severity": "HIGH"}],
    }
    assert check("db-secret", result) == 1
    with pytest.raises(ValueError):
        check("db-secret", result, file_hash="0" * 64)
    with pytest.raises(ValueError):
        check("db-secret", {**result, "Target": "other"})
    with pytest.raises(ValueError):
        check("db-secret", {**result, "Secrets": result["Secrets"] * 2})


@pytest.mark.parametrize("kind", ["auth", "db"])
def test_other_packages_remain_blocked(kind):
    with pytest.raises(ValueError, match="Unapproved finding"):
        check(kind, {"Class": "os-pkgs", "Vulnerabilities": [{"Severity": "HIGH"}]})
