"""Private operator input boundaries; all values are synthetic."""

import json
from unittest.mock import Mock

import pytest


def setup_module_api():
    from deploy import poc_setup

    return poc_setup


@pytest.mark.parametrize("form", ["raw", "dotenv"])
def test_bootstrap_requires_explicit_format_and_private_regular_file(tmp_path, form):
    m = setup_module_api()
    path = tmp_path / "bootstrap"
    path.write_text("BWS_ACCESS_TOKEN=fixture-token\n" if form == "dotenv" else "fixture-token\n")
    path.chmod(0o600)
    assert m.read_bootstrap(path, form=form) == "fixture-token"
    with pytest.raises(m.SetupError):
        m.read_bootstrap(path, form="raw" if form == "dotenv" else "dotenv")
    path.chmod(0o640)
    with pytest.raises(m.SetupError):
        m.read_bootstrap(path, form=form)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(m.SetupError):
        m.read_bootstrap(link, form=form)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "BWS_ACCESS_TOKEN=x\nOTHER=y\n",
        "export BWS_ACCESS_TOKEN=x",
        'BWS_ACCESS_TOKEN="x"',
        "BWS_ACCESS_TOKEN=x\x00",
        "BWS_ACCESS_TOKEN=x\ny",
        "BWS_ACCESS_TOKEN= ",
    ],
)
def test_bootstrap_rejects_shell_syntax_multiple_keys_and_empty(tmp_path, body):
    m = setup_module_api()
    path = tmp_path / "bootstrap"
    path.write_text(body)
    path.chmod(0o600)
    with pytest.raises(m.SetupError, match="Invalid bootstrap file"):
        m.read_bootstrap(path, form="dotenv")


def test_secret_bundle_separates_db_roles_and_has_no_runtime_bws_token():
    import jwt
    from sqlalchemy.engine import make_url

    m = setup_module_api()
    bundle = m.generate_secrets(smtp_password="fixture-mail-password", now=1800000000)
    assert set(bundle) == m.SECRET_NAMES
    app = make_url(bundle["notebook_database_url"])
    auth = make_url(bundle["notebook_auth_database_url"])
    assert (app.username, auth.username) == ("notebook_app", "supabase_auth_admin")
    assert len({app.password, auth.password, bundle["notebook_postgres_password"]}) == 3
    assert app.host == auth.host == "db"
    for name, role in [
        ("notebook_supabase_anon_key", "anon"),
        ("notebook_supabase_service_role_key", "service_role"),
    ]:
        claims = jwt.decode(
            bundle[name],
            bundle["notebook_auth_jwt_secret"],
            algorithms=["HS256"],
            options={"verify_exp": False, "verify_iat": False},
        )
        assert claims["role"] == role
        assert claims["iat"] == 1800000000
        assert claims["exp"] == claims["iat"] + 365 * 86400
        assert claims["iss"] == "supabase"
    assert "BWS_ACCESS_TOKEN" not in bundle


@pytest.mark.parametrize(
    "server_url", ["https://vault.bitwarden.eu", "https://vault.example.invalid"]
)
def test_manifest_is_closed_and_validates_secret_ids(server_url):
    m = setup_module_api()
    ids = {
        name: f"00000000-0000-4000-8000-{index:012d}"
        for index, name in enumerate(sorted(m.SECRET_NAMES), 1)
    }
    manifest = m.secret_manifest("00000000-0000-4000-8000-000000000099", ids, server_url=server_url)
    assert manifest["server_url"] == server_url
    assert manifest["secrets"] == ids
    for invalid in [
        {},
        {**ids, "foreign_secret": next(iter(ids.values()))},
        {**ids, "notebook_database_url": "bad"},
    ]:
        with pytest.raises(m.SetupError):
            m.secret_manifest(manifest["project_id"], invalid, server_url=server_url)


def test_new_auth_account_is_unconfirmed_no_reset_and_otp_is_email_bound(tmp_path):
    m = setup_module_api()
    uid = "00000000-0000-4000-8000-000000000001"
    transport = Mock()
    transport.request.side_effect = [
        {"id": uid, "email": m.ADMIN_EMAIL, "email_confirmed_at": None},
        {},
        {"access_token": "fixture-session", "user": {"id": uid}},
        {"id": uid, "email": m.ADMIN_EMAIL, "email_confirmed_at": "2026-11-16T00:00:00Z"},
    ]
    prompts = Mock(side_effect=["fixture-password-123", "fixture-password-123", "123456"])
    assert (
        m.create_confirmed_admin_identity(
            transport, prompts, journal_file=tmp_path / "identity.json"
        )
        == uid
    )
    assert m.read_identity_journal(tmp_path / "identity.json") == uid
    assert (tmp_path / "identity.json").stat().st_mode & 0o777 == 0o600
    calls = transport.request.call_args_list
    assert [c.args[:2] for c in calls] == [
        ("POST", "admin/users"),
        ("POST", "resend"),
        ("POST", "verify"),
        ("GET", "user"),
    ]
    assert calls[0].kwargs["data"]["email_confirm"] is False
    assert calls[2].kwargs["data"] == {"type": "signup", "email": m.ADMIN_EMAIL, "token": "123456"}
    assert calls[3].kwargs["token"] == "fixture-session"


def test_password_mismatch_performs_no_auth_action(tmp_path):
    m = setup_module_api()
    client = Mock()
    with pytest.raises(m.SetupError):
        m.create_confirmed_admin_identity(
            client,
            Mock(side_effect=["one-long-password", "other-password"]),
            journal_file=tmp_path / "identity.json",
        )
    client.request.assert_not_called()


def test_existing_account_error_does_not_reset_or_confirm(tmp_path):
    m = setup_module_api()
    client = Mock()
    client.request.side_effect = m.SetupError("Auth operation failed")
    with pytest.raises(m.SetupError):
        m.create_confirmed_admin_identity(
            client,
            Mock(return_value="fixture-password-123"),
            journal_file=tmp_path / "identity.json",
        )
    assert client.request.call_count == 1


@pytest.mark.parametrize(
    "user",
    [
        {"id": "wrong", "email": "johannes@yokoma.de", "email_confirmed_at": "yes"},
        {
            "id": "00000000-0000-4000-8000-000000000001",
            "email": "other@example.invalid",
            "email_confirmed_at": "yes",
        },
        {"id": "00000000-0000-4000-8000-000000000001", "email": "johannes@yokoma.de"},
    ],
)
def test_confirmation_requires_same_real_identity(user, tmp_path):
    m = setup_module_api()
    client = Mock()
    client.request.side_effect = [
        {"id": "00000000-0000-4000-8000-000000000001", "email": m.ADMIN_EMAIL},
        {},
        {"access_token": "fixture-session"},
        user,
    ]
    with pytest.raises(m.SetupError):
        m.create_confirmed_admin_identity(
            client,
            Mock(side_effect=["fixture-password-123", "fixture-password-123", "123456"]),
            journal_file=tmp_path / "identity.json",
        )


@pytest.mark.parametrize(
    "server_url",
    [
        "http://vault.example.invalid",
        "https://user:pass@vault.example.invalid",
        "https://vault.example.invalid?token=x",
        "https://vault.example.invalid/#frag",
        "https://vault.example.invalid\n",
    ],
)
def test_manifest_rejects_unsafe_server_url(server_url):
    m = setup_module_api()
    ids = {
        name: f"00000000-0000-4000-8000-{index:012d}"
        for index, name in enumerate(sorted(m.SECRET_NAMES), 1)
    }
    with pytest.raises(m.SetupError):
        m.secret_manifest("00000000-0000-4000-8000-000000000099", ids, server_url=server_url)


def test_interrupted_resend_preserves_journal_then_resumes_only_real_otp(tmp_path):
    m = setup_module_api()
    uid = "00000000-0000-4000-8000-000000000001"
    path = tmp_path / "identity.json"
    client = Mock()
    client.request.side_effect = [
        {"id": uid, "email": m.ADMIN_EMAIL},
        m.SetupError("mail unavailable"),
    ]
    with pytest.raises(m.SetupError):
        m.create_confirmed_admin_identity(
            client, Mock(return_value="fixture-password-123"), journal_file=path
        )
    assert m.read_identity_journal(path) == uid
    assert set(json.loads(path.read_text())) == {"profile", "email", "auth_user_id"}
    resumed = Mock()
    resumed.request.side_effect = [
        {"id": uid, "email": m.ADMIN_EMAIL},
        {},
        {"access_token": "fixture-session"},
        {"id": uid, "email": m.ADMIN_EMAIL, "email_confirmed_at": "2026-11-16T00:00:00Z"},
    ]
    assert m.resume_confirmed_admin_identity(resumed, path, Mock(return_value="123456")) == uid
    assert [call.args[:2] for call in resumed.request.call_args_list] == [
        ("GET", "admin/users/" + uid),
        ("POST", "resend"),
        ("POST", "verify"),
        ("GET", "user"),
    ]
    assert "fixture-" not in path.read_text()
    assert "123456" not in path.read_text()


@pytest.mark.parametrize("mutation", ["foreign_id", "foreign_email", "confirmed"])
def test_resume_rejects_mismatched_or_already_confirmed_provider_identity(tmp_path, mutation):
    m = setup_module_api()
    uid = "00000000-0000-4000-8000-000000000001"
    path = tmp_path / "identity.json"
    path.write_text(
        json.dumps({"profile": "login-workspace-v1", "email": m.ADMIN_EMAIL, "auth_user_id": uid})
    )
    path.chmod(0o600)
    user = {"id": uid, "email": m.ADMIN_EMAIL}
    if mutation == "foreign_id":
        user["id"] = "00000000-0000-4000-8000-000000000002"
    elif mutation == "foreign_email":
        user["email"] = "other@example.invalid"
    else:
        user["email_confirmed_at"] = "2026-11-16T00:00:00Z"
    client = Mock()
    client.request.return_value = user
    prompt = Mock()
    with pytest.raises(m.SetupError):
        m.resume_confirmed_admin_identity(client, path, prompt)
    assert client.request.call_count == 1
    prompt.assert_not_called()


def test_existing_journal_or_symlink_cannot_create_another_account(tmp_path):
    m = setup_module_api()
    path = tmp_path / "identity.json"
    path.symlink_to(tmp_path / "absent")
    client = Mock()
    with pytest.raises(m.SetupError):
        m.create_confirmed_admin_identity(client, Mock(), journal_file=path)
    client.request.assert_not_called()


def test_private_auth_client_rejects_unbound_admin_uuid_without_io():
    m = setup_module_api()
    client = m.PrivateAuthClient(
        "fixture-service-key", expected_uid="00000000-0000-4000-8000-000000000001"
    )
    with pytest.raises(m.SetupError):
        client.request("GET", "admin/users/00000000-0000-4000-8000-000000000002")
