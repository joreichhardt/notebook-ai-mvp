"""Pure configuration/DB-call contracts; no Podman, PostgreSQL or SMTP proof."""

import importlib.util
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]


def admin_module():
    spec = importlib.util.spec_from_file_location("poc_admin", ROOT / "docker/poc-admin.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Cursor:
    def __init__(self, role, forbidden):
        self.results = iter([role, (forbidden,)])
        self.calls = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def execute(self, query, params=None):
        self.calls.append((query, params))

    def fetchone(self):
        return next(self.results)


class Connection:
    def __init__(self, cursor):
        self.owned_cursor = cursor
        self.closed = False
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        self.closed = True
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None

    def cursor(self):
        return self.owned_cursor


@pytest.mark.parametrize("failure", [None, "superuser", "replication", "capability"])
def test_api_role_literal_binding_and_transaction(monkeypatch, failure):
    m = admin_module()
    role = ["notebook_app", False, False, False, False, False]
    if failure == "superuser":
        role[1] = True
    if failure == "replication":
        role[5] = True
    cursor = Cursor(tuple(role), 1 if failure == "capability" else 0)
    connection = Connection(cursor)
    connect = Mock(return_value=connection)
    monkeypatch.setattr(m.psycopg, "connect", connect)
    password = "p" * 48
    url = m.migration_url("q" * 48)
    if failure:
        with pytest.raises(m.DatabaseSetupError):
            m.activate_api_role(url, password)
    else:
        m.activate_api_role(url, password)
    assert connect.call_args.kwargs == {"cursor_factory": m.ClientCursor, "connect_timeout": 5}
    assert connect.call_args.args[0].startswith("postgresql://postgres:")
    assert "psycopg" not in connect.call_args.args[0]
    assert cursor.calls[:3] == [
        ("SET LOCAL log_statement = 'none'", None),
        ("SET LOCAL log_min_error_statement = 'panic'", None),
        ("SET LOCAL log_parameter_max_length_on_error = 0", None),
    ]
    alterations = [(sql, params) for sql, params in cursor.calls if sql.startswith("ALTER")]
    assert alterations == (
        []
        if failure in ("superuser", "replication")
        else [("ALTER ROLE notebook_app LOGIN PASSWORD %s", (password,))]
    )
    assert all(password not in sql for sql, _ in cursor.calls)
    assert connection.closed and cursor.closed
    assert connection.committed is (failure is None)
    assert connection.rolled_back is (failure is not None)


def test_invalid_password_rejected_before_connect(monkeypatch):
    m = admin_module()
    connect = Mock()
    monkeypatch.setattr(m.psycopg, "connect", connect)
    with pytest.raises(m.DatabaseSetupError):
        m.activate_api_role(m.migration_url("q" * 48), "secret';SELECT 1;")
    connect.assert_not_called()


def test_db_driver_error_never_echoes_secret(monkeypatch, capsys):
    m = admin_module()
    canary = "never-echo-password-or-dsn"
    monkeypatch.setattr(m.sys, "argv", ["poc-admin.py", "activate-api-role"])
    monkeypatch.setattr(m.logging, "disable", Mock())
    monkeypatch.setattr(
        m,
        "private_value",
        lambda path: (
            "q" * 48
            if path.name == "notebook_postgres_password"
            else "postgresql+psycopg://notebook_app:" + "p" * 48 + "@db:5432/postgres"
        ),
    )
    monkeypatch.setattr(m.psycopg, "connect", Mock(side_effect=m.psycopg.Error(canary)))
    assert m.main() == 1
    output = capsys.readouterr()
    assert canary not in output.out + output.err
    assert output.out == ""
    assert output.err == "Private database operation failed; no diagnostics disclosed.\n"


def test_compose_is_login_only_private_pinned_and_no_build():
    source = (ROOT / "compose.poc.yaml").read_text()
    for forbidden in (
        "build:",
        "privileged:",
        "BWS_ACCESS_TOKEN",
        "notebook_supabase_service_role_key",
        "storage:",
        "worker:",
        "0.0.0.0:18191",
        "docker.sock",
        "podman.sock",
    ):
        assert forbidden not in source
    assert 'ports: ["127.0.0.1:18191:8080"]' in source
    assert "app.poc:app" in source
    assert 'GOTRUE_MAILER_AUTOCONFIRM: "false"' in source
    assert 'GOTRUE_DISABLE_SIGNUP: "true"' in source
    assert "NOTEBOOK_POC_API_IMAGE:?" in source
    assert source.count("image:") == 4
    assert "internal: true" in source
    assert "./docker/poc-db-init:/docker-entrypoint-initdb.d:ro" in source
    blocks = re.split(
        r"(?m)^  (db|auth|api|frontend):\n",
        source.split("services:\n", 1)[1].split("\nnetworks:", 1)[0],
    )
    services = dict(zip(blocks[1::2], blocks[2::2], strict=True))
    assert set(services) == {"db", "auth", "api", "frontend"}
    expected_mounts = {
        "db": [
            "postgres:/var/lib/postgresql/data",
            "./docker/poc-db-init:/docker-entrypoint-initdb.d:ro",
            "${NOTEBOOK_POC_SECRETS:?private release required}/db:/run/secrets:ro",
        ],
        "auth": [
            "./docker/poc-auth.sh:/opt/poc-auth.sh:ro",
            "${NOTEBOOK_POC_SECRETS:?private release required}/auth:/run/secrets:ro",
        ],
        "api": ["${NOTEBOOK_POC_SECRETS:?private release required}/api:/run/secrets:ro"],
        "frontend": [
            "./docker/nginx.poc.conf:/etc/nginx/nginx.conf:ro",
            "./docker/poc-confirmation.html:/opt/poc-confirmation.html:ro",
        ],
    }
    for name, block in services.items():
        match = re.search(r"(?m)^    volumes:\n((?:      - .*\n)+)", block)
        assert match is not None
        assert [line.removeprefix("      - ") for line in match[1].splitlines()] == expected_mounts[
            name
        ]
        assert "env_file:" not in block
    assert source.count("service_role") == 1
    assert "GOTRUE_JWT_ADMIN_ROLES: service_role" in services["auth"]
    assert '"Host: note.yokoma.de"' in services["frontend"]


def test_database_init_client_uses_the_configured_private_socket():
    source = (ROOT / "compose.poc.yaml").read_text()
    db = source.split("  db:\n", 1)[1].split("  auth:\n", 1)[0]
    assert "      PGHOST: /tmp\n" in db
    assert "      - unix_socket_directories=/tmp\n" in db


def test_nginx_has_no_public_auth_or_readiness_alias_and_otp_is_internal():
    config = (ROOT / "docker/nginx.poc.conf").read_text()
    public, private = config.split("# Unpublished container port")
    assert "location /auth/ { return 503; }" in public
    assert "location = /confirmation.html { return 404; }" in public
    assert "proxy_pass http://api:8000;" in public
    assert "return 200" not in config
    assert "listen 8081;" in private
    assert "(token|user|logout|health)" in private
    assert "admin" not in private
    template = (ROOT / "docker/poc-confirmation.html").read_text()
    assert "{{ .Token }}" in template
    assert "ConfirmationURL" not in template


def test_public_nginx_reserves_infrastructure_prefixes_before_spa():
    rule = "location ~ ^/(?:rest/v1|storage/v1|studio|pg|auth/v1/admin)(?:/|$) { return 404; }"
    for name in ("nginx.conf", "nginx.poc.conf"):
        config = (ROOT / "docker" / name).read_text().split("# Unpublished container port")[0]
        assert config.count(rule) == 1
        assert config.index(rule) < config.index("try_files")
