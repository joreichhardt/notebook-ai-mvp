"""Bounded first-owner setup. Never accepts a password/token on the command line.

This module does not deploy, publish, reset existing users, or grant app admission.
After real mail confirmation, the operator separately runs app.auth.bootstrap.
"""

import argparse
import getpass
import json
import os
import re
import secrets
import stat
import sys
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import jwt

ADMIN_EMAIL = "johannes@yokoma.de"
SECRET_NAMES = frozenset(
    {
        "notebook_postgres_password",
        "notebook_auth_database_url",
        "notebook_database_url",
        "notebook_auth_jwt_secret",
        "notebook_supabase_anon_key",
        "notebook_supabase_service_role_key",
        "notebook_smtp_password",
    }
)


class SetupError(Exception):
    """Only fixed non-secret messages may cross the operator boundary."""


def private_value(path: Path) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
            ):
                raise SetupError("Invalid bootstrap file")
            value = stream.read(16385)
        if not value or len(value) > 16384 or "\x00" in value:
            raise SetupError("Invalid bootstrap file")
        return value
    except (OSError, UnicodeError):
        raise SetupError("Invalid bootstrap file") from None


def read_bootstrap(path: Path, *, form: str) -> str:
    value = private_value(path).removesuffix("\n")
    if form == "dotenv":
        prefix = "BWS_ACCESS_TOKEN="
        if not value.startswith(prefix):
            raise SetupError("Invalid bootstrap file")
        value = value[len(prefix) :]
    elif form != "raw":
        raise SetupError("Invalid bootstrap file")
    if not re.fullmatch(r"[A-Za-z0-9._:/+\-=]+", value) or value.startswith("BWS_ACCESS_TOKEN="):
        raise SetupError("Invalid bootstrap file")
    return value


def generate_secrets(*, smtp_password: str, now: int | None = None) -> dict[str, str]:
    if not smtp_password or any(c in smtp_password for c in "\x00\n\r"):
        raise SetupError("Dedicated SMTP password required")
    issued = int(time.time()) if now is None else now
    signing = secrets.token_urlsafe(48)
    values = {
        "notebook_postgres_password": secrets.token_urlsafe(36),
        "notebook_auth_database_url": "postgres://supabase_auth_admin:"
        + secrets.token_urlsafe(36)
        + "@db:5432/postgres",
        "notebook_database_url": "postgresql+psycopg://notebook_app:"
        + secrets.token_urlsafe(36)
        + "@db:5432/postgres",
        "notebook_auth_jwt_secret": signing,
        "notebook_smtp_password": smtp_password,
    }
    for name, role in (
        ("notebook_supabase_anon_key", "anon"),
        ("notebook_supabase_service_role_key", "service_role"),
    ):
        values[name] = jwt.encode(
            {"role": role, "iss": "supabase", "iat": issued, "exp": issued + 365 * 86400},
            signing,
            algorithm="HS256",
        )
    return values


def secret_manifest(project_id: str, secret_ids: dict[str, str], *, server_url: str) -> dict:
    try:
        url = urlsplit(server_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path not in ("", "/")
            or any(ord(c) <= 32 or ord(c) == 127 for c in server_url)
        ):
            raise ValueError()
        project_id = str(UUID(project_id))
        if set(secret_ids) != SECRET_NAMES or len(set(secret_ids.values())) != len(SECRET_NAMES):
            raise ValueError()
        for value in secret_ids.values():
            if str(UUID(value)) != value:
                raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise SetupError("Invalid project secret manifest") from None
    return {"server_url": server_url, "project_id": project_id, "secrets": dict(secret_ids)}


class AuthRequests(Protocol):
    def request(
        self, method: str, path: str, *, data: dict | None = None, token: str = ""
    ) -> dict: ...


class PrivateAuthClient:
    """Only the owned network's GoTrue endpoint; never redirects or inherits proxies."""

    def __init__(self, service_key: str, *, expected_uid: str | None = None):
        self._service_key = service_key
        self._expected_uid = expected_uid

    def request(self, method: str, path: str, *, data: dict | None = None, token: str = "") -> dict:
        expected_admin_get = (
            method == "GET"
            and self._expected_uid is not None
            and path == "admin/users/" + self._expected_uid
        )
        if not expected_admin_get and (method, path) not in {
            ("POST", "admin/users"),
            ("POST", "resend"),
            ("POST", "verify"),
            ("GET", "user"),
        }:
            raise SetupError("Unsupported Auth operation")
        headers = {}
        if path == "admin/users" or expected_admin_get:
            headers["Authorization"] = "Bearer " + self._service_key
        elif token:
            headers["Authorization"] = "Bearer " + token
        try:
            with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
                response = client.request(
                    method, "http://auth:9999/" + path, json=data, headers=headers
                )
            if response.status_code >= 300:
                raise SetupError("Auth operation failed; existing accounts are never reset")
            result = response.json()
            if not isinstance(result, dict):
                raise SetupError("Invalid Auth response")
            return result
        except (httpx.HTTPError, ValueError):
            raise SetupError("Auth operation failed") from None


def read_identity_journal(path: Path) -> str:
    try:
        value = json.loads(private_value(path))
        if (
            set(value) != {"profile", "email", "auth_user_id"}
            or value["profile"] != "login-workspace-v1"
            or value["email"] != ADMIN_EMAIL
        ):
            raise ValueError()
        uid = value["auth_user_id"]
        if str(UUID(uid)) != uid:
            raise ValueError()
        return uid
    except (KeyError, TypeError, ValueError, AttributeError):
        raise SetupError("Invalid private identity journal") from None


def create_confirmed_admin_identity(
    client: AuthRequests, prompt=getpass.getpass, *, journal_file: Path
) -> str:
    parent = journal_file.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
        or journal_file.exists()
        or journal_file.is_symlink()
    ):
        raise SetupError("New journal in an owned private directory required")
    password = prompt("New account password (private): ")
    confirm = prompt("Repeat password (private): ")
    if password != confirm or len(password) < 12:
        raise SetupError("Passwords must match and contain at least 12 characters")
    created = client.request(
        "POST",
        "admin/users",
        data={"email": ADMIN_EMAIL, "password": password, "email_confirm": False},
    )
    try:
        uid = str(UUID(created["id"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise SetupError("Invalid Auth identity") from None
    if created.get("email") != ADMIN_EMAIL or created.get("email_confirmed_at"):
        raise SetupError("Expected a new unconfirmed account")
    # Persist provider identity before mail delivery; never store password/OTP/session.
    fd = os.open(journal_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(
            {"profile": "login-workspace-v1", "email": ADMIN_EMAIL, "auth_user_id": uid}, stream
        )
        stream.flush()
        os.fsync(stream.fileno())
    return _confirm_identity(client, uid, prompt)


def resume_confirmed_admin_identity(
    client: AuthRequests, journal_file: Path, prompt=getpass.getpass
) -> str:
    uid = read_identity_journal(journal_file)
    user = client.request("GET", "admin/users/" + uid)
    if user.get("id") != uid or user.get("email") != ADMIN_EMAIL:
        raise SetupError("Created identity mismatch")
    if user.get("email_confirmed_at"):
        raise SetupError("Already confirmed; use ordinary login and explicit admission")
    return _confirm_identity(client, uid, prompt)


def _confirm_identity(client: AuthRequests, uid: str, prompt) -> str:
    client.request("POST", "resend", data={"type": "signup", "email": ADMIN_EMAIL})
    otp = prompt("Confirmation code received in your email (private): ")
    if not re.fullmatch(r"[0-9]{6}", otp):
        raise SetupError("Invalid confirmation code")
    result = client.request(
        "POST", "verify", data={"type": "signup", "email": ADMIN_EMAIL, "token": otp}
    )
    token = result.get("access_token")
    if not isinstance(token, str) or not token:
        raise SetupError("Missing verified Auth session")
    user = client.request("GET", "user", token=token)
    if (
        user.get("id") != uid
        or user.get("email") != ADMIN_EMAIL
        or not user.get("email_confirmed_at")
    ):
        raise SetupError("Confirmed Auth identity mismatch")
    return uid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["create-confirmed-owner", "confirm-existing-unconfirmed-owner"]
    )
    parser.add_argument("--service-key-file", type=Path, required=True)
    parser.add_argument("--identity-journal", type=Path, required=True)
    args = parser.parse_args()
    try:
        if not sys.stdin.isatty():
            raise SetupError("Private interactive terminal required")
        key = private_value(args.service_key_file).strip()
        if args.action == "create-confirmed-owner":
            create_confirmed_admin_identity(
                PrivateAuthClient(key), journal_file=args.identity_journal
            )
        else:
            uid = read_identity_journal(args.identity_journal)
            resume_confirmed_admin_identity(
                PrivateAuthClient(key, expected_uid=uid), args.identity_journal
            )
    except (SetupError, EOFError, OSError):
        print(
            "Owner setup failed; no app admission granted. Inspect account state before retry.",
            file=sys.stderr,
        )
        return 1
    print("Email confirmed by Auth. Separate explicit first-admin admission is still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
