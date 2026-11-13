"""Local no-DB checks for the opt-in packet's pre-connection guards."""

import pytest
import test_postgres_acceptance as packet


@pytest.fixture
def assigned(monkeypatch, tmp_path):
    values = {
        "F4_ASSIGNED_DATABASE": "notebook_f4_0123456789ab",
        "F4_ASSIGNED_HOST": "db",
        "F4_APP_URL": "postgresql://notebook_app@db:5432/notebook_f4_0123456789ab",
        "F4_ADMIN_URL": "postgresql://postgres@db:5432/notebook_f4_0123456789ab",
    }
    for role in ("APP", "ADMIN"):
        path = tmp_path / role
        path.write_text("SYNTHETIC_UNUSED_PASSFILE\n")
        path.chmod(0o600)
        values[f"F4_{role}_PASSFILE"] = str(path)
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    def forbidden(*args, **kwargs):
        pytest.fail("configuration attempted engine creation", pytrace=False)

    monkeypatch.setattr(packet, "create_engine", forbidden)
    return values


def test_assigned_config_validates_without_engine(assigned):
    app, admin = packet.configuration()
    assert app[0].username == "notebook_app"
    assert admin[0].username == "postgres"
    assert app[0].password is None


@pytest.mark.parametrize(
    "fault", ["assignment", "host", "password", "role", "query", "mode", "symlink"]
)
def test_invalid_assignment_fails_closed_without_engine(assigned, monkeypatch, tmp_path, fault):
    from pathlib import Path

    if fault == "assignment":
        monkeypatch.delenv("F4_ASSIGNED_DATABASE")
    elif fault == "host":
        monkeypatch.setenv("F4_ASSIGNED_HOST", "localhost")
    elif fault == "password":
        monkeypatch.setenv(
            "F4_APP_URL", assigned["F4_APP_URL"].replace("notebook_app@", "notebook_app:synthetic@")
        )
    elif fault == "role":
        monkeypatch.setenv(
            "F4_APP_URL", assigned["F4_APP_URL"].replace("notebook_app@", "postgres@")
        )
    elif fault == "query":
        monkeypatch.setenv("F4_APP_URL", assigned["F4_APP_URL"] + "?options=synthetic")
    elif fault == "mode":
        Path(assigned["F4_APP_PASSFILE"]).chmod(0o644)
    else:
        link = tmp_path / "link"
        link.symlink_to(assigned["F4_APP_PASSFILE"])
        monkeypatch.setenv("F4_APP_PASSFILE", str(link))
    with pytest.raises(pytest.fail.Exception):
        packet.configuration()
