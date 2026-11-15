#!/opt/notebook-poc/venv/bin/python
"""Root-installed allowlisted entry point, executed only as the dedicated runtime user.

One argument: an operator-approved public APP commit. No source checkout, arbitrary
URLs, shell, caller environment, host provisioning, publication or account creation.
"""

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import jwt

# This installation tree and all ancestors must be root-owned and non-writable.
sys.path.insert(0, "/opt/notebook-poc/source")
from deploy.poc_publication import AUTH_IMAGE, DB_IMAGE
from deploy.poc_setup import SECRET_NAMES, SetupError, private_value, read_bootstrap
from deploy.provision_secrets import ProvisionError, provision

APPROVAL = Path("/etc/notebook-poc/approved-release.json")
ARCHIVES = Path("/var/lib/notebook-poc-approved")
HOME = Path("/var/lib/notebook-poc")
PROVIDER = Path("/usr/libexec/docker/cli-plugins/docker-compose")
BWS = Path("/opt/notebook-poc/bin/bws")
PODMAN = "/usr/bin/podman"
RUNTIME_SECRET_NAMES = SECRET_NAMES - {"notebook_supabase_service_role_key"}


class DeploymentHold(Exception):
    """Never include upstream output, credentials or arbitrary exception text."""


def trusted_file(path: Path, *, digest: str | None = None) -> bytes:
    for parent in (path, *path.parents):
        info = parent.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise DeploymentHold("Untrusted approved input")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise DeploymentHold("Invalid approved input")
        value = stream.read(128 * 1024 * 1024 + 1)
    if len(value) > 128 * 1024 * 1024:
        raise DeploymentHold("Approved input too large")
    if digest is not None and hashlib.sha256(value).hexdigest() != digest:
        raise DeploymentHold("Approved input digest mismatch")
    return value


def validate_approval(value: dict, commit: str, *, now: int, uid: int, gid: int) -> dict:
    try:
        fields = {
            "commit",
            "archive_sha256",
            "approved_at",
            "expires_at",
            "runtime_uid",
            "runtime_gid",
            "runner_uid",
            "postgres_uid",
            "postgres_gid",
            "compose_provider_sha256",
            "compose_provider_version",
            "bws_sha256",
            "images",
            "bws_manifest",
            "smtp_host",
        }
        if (
            set(value) != fields
            or not re.fullmatch(r"[0-9a-f]{40}", commit)
            or value["commit"] != commit
        ):
            raise ValueError()
        for field in ("archive_sha256", "compose_provider_sha256", "bws_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", value[field]):
                raise ValueError()
        for field in ("runtime_uid", "runtime_gid", "runner_uid", "postgres_uid", "postgres_gid"):
            if type(value[field]) is not int or not 0 < value[field] < 2**31:
                raise ValueError()
        if (
            uid != value["runtime_uid"]
            or gid != value["runtime_gid"]
            or uid in (0, 1001, value["runner_uid"])
            or value["runner_uid"] in (0, 1001)
        ):
            raise ValueError()
        # Bound to the exact vendor digest above; observed via isolated `id`, not
        # inferred from host mappings or distribution defaults.
        if value["postgres_uid"] != 100 or value["postgres_gid"] != 101:
            raise ValueError()
        if (
            type(value["approved_at"]) is not int
            or type(value["expires_at"]) is not int
            or not value["approved_at"] <= now < value["expires_at"]
            or value["expires_at"] - value["approved_at"] > 86400
        ):
            raise ValueError()
        if value["compose_provider_version"] != "2.40.3+ds1-0ubuntu1":
            raise ValueError()
        images = value["images"]
        if (
            set(images) != {"api", "frontend", "auth", "db"}
            or images["auth"] != AUTH_IMAGE
            or images["db"] != DB_IMAGE
        ):
            raise ValueError()
        for role in ("api", "frontend"):
            if not re.fullmatch(
                r"ghcr\.io/joreichhardt/notebook-ai-poc-" + role + r"@sha256:[0-9a-f]{64}",
                images[role],
            ):
                raise ValueError()
        manifest = value["bws_manifest"]
        if (
            set(manifest) != {"server_url", "project_id", "secrets"}
            or set(manifest["secrets"]) != RUNTIME_SECRET_NAMES
        ):
            raise ValueError()
        UUID(manifest["project_id"])
        if len(set(manifest["secrets"].values())) != len(RUNTIME_SECRET_NAMES):
            raise ValueError()
        for secret_id in manifest["secrets"].values():
            if str(UUID(secret_id)) != secret_id:
                raise ValueError()
        server = urlsplit(manifest["server_url"])
        if (
            server.scheme != "https"
            or not server.hostname
            or server.username is not None
            or server.password is not None
            or server.query
            or server.fragment
            or server.path not in ("", "/")
            or any(ord(c) <= 32 or ord(c) == 127 for c in manifest["server_url"])
        ):
            raise ValueError()
        if not re.fullmatch(r"(?=.{1,253}$)[a-z0-9]+(?:[.-][a-z0-9]+)+", value["smtp_host"]):
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise DeploymentHold("Invalid or expired host approval") from None
    return value


def clean_environment(uid: int) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(HOME),
        "LANG": "C.UTF-8",
        "XDG_RUNTIME_DIR": f"/run/user/{uid}",
        "DOCKER_HOST": f"unix:///run/user/{uid}/podman/podman.sock",
        "PODMAN_COMPOSE_PROVIDER": str(PROVIDER),
    }


def run(argv: list[str], env: dict[str, str], *, timeout: int = 120) -> bytes:
    try:
        result = subprocess.run(argv, env=env, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        raise DeploymentHold("Owned runtime command failed") from None
    if result.returncode:
        raise DeploymentHold("Owned runtime command failed")
    return result.stdout


def private_directory(path: Path, uid: int) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise DeploymentHold("Invalid private runtime directory")


def extract_archive(archive: Path, target: Path) -> None:
    with tarfile.open(archive, "r:") as stream:
        members = stream.getmembers()
        total = 0
        for member in members:
            path = Path(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or path.parts[0] in (".git", ".pi")
                or not (member.isfile() or member.isdir())
            ):
                raise DeploymentHold("Unsafe reviewed archive")
            total += member.size
            if total > 256 * 1024 * 1024:
                raise DeploymentHold("Reviewed archive too large")
            member.mode = 0o755 if member.isdir() or member.mode & 0o111 else 0o644
        stream.extractall(target, members=members, filter="data")


def validate_runtime_values(values: dict[str, str]) -> None:
    try:
        if set(values) != RUNTIME_SECRET_NAMES:
            raise ValueError()
        postgres_password = values["notebook_postgres_password"]
        signing = values["notebook_auth_jwt_secret"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{48}", postgres_password) or not re.fullmatch(
            r"[A-Za-z0-9_-]{64}", signing
        ):
            raise ValueError()
        passwords = {postgres_password}
        for name, scheme, user in (
            ("notebook_auth_database_url", "postgres", "supabase_auth_admin"),
            ("notebook_database_url", "postgresql+psycopg", "notebook_app"),
        ):
            url = urlsplit(values[name])
            if (
                url.scheme != scheme
                or url.username != user
                or url.hostname != "db"
                or url.port != 5432
                or url.path != "/postgres"
                or url.query
                or url.fragment
                or url.password is None
                or not re.fullmatch(r"[A-Za-z0-9_-]{48}", url.password)
            ):
                raise ValueError()
            passwords.add(url.password)
        if len(passwords) != 3:
            raise ValueError()
        smtp = values["notebook_smtp_password"]
        if not smtp or any(c in smtp for c in "\r\n\x00"):
            raise ValueError()
        claims = jwt.decode(
            values["notebook_supabase_anon_key"],
            signing,
            algorithms=["HS256"],
            issuer="supabase",
            options={"require": ["iat", "exp", "role", "iss"]},
        )
        if claims["role"] != "anon":
            raise ValueError()
    except (ValueError, TypeError, KeyError, jwt.PyJWTError):
        raise DeploymentHold("Invalid dedicated runtime credentials") from None


def stage_consumers(
    raw: Path, target: Path, *, postgres_uid: int, postgres_gid: int, env: dict[str, str]
) -> None:
    groups = {
        "db": (
            postgres_uid,
            postgres_gid,
            ["notebook_postgres_password", "notebook_auth_database_url"],
        ),
        "auth": (
            1000,
            1000,
            ["notebook_auth_database_url", "notebook_auth_jwt_secret", "notebook_smtp_password"],
        ),
        "api": (10001, 10001, ["notebook_database_url", "notebook_supabase_anon_key"]),
        "admin": (10001, 10001, ["notebook_postgres_password", "notebook_database_url"]),
    }
    target.mkdir(mode=0o700)
    for name, (uid, gid, files) in groups.items():
        directory = target / name
        directory.mkdir(mode=0o700)
        for filename in files:
            with (directory / filename).open("xb") as stream:
                stream.write((raw / filename).read_bytes())
                os.fchmod(stream.fileno(), 0o400)
        # Map UID/GID using the actual rootless user namespace, never host math.
        run(
            [
                PODMAN,
                "unshare",
                "/usr/bin/chown",
                "--recursive",
                "--no-dereference",
                f"{uid}:{gid}",
                str(directory),
            ],
            env,
        )


def sync_home() -> None:
    fd = os.open(HOME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def deploy(commit: str) -> None:
    uid, gid = os.getuid(), os.getgid()
    approval = validate_approval(
        json.loads(trusted_file(APPROVAL)), commit, now=int(time.time()), uid=uid, gid=gid
    )
    archive = ARCHIVES / (commit + ".tar")
    trusted_file(archive, digest=approval["archive_sha256"])
    trusted_file(PROVIDER, digest=approval["compose_provider_sha256"])
    trusted_file(BWS, digest=approval["bws_sha256"])
    trusted_file(Path(PODMAN))
    private_directory(HOME, uid)
    runtime = Path(f"/run/user/{uid}")
    private_directory(runtime, uid)
    socket = runtime / "podman/podman.sock"
    info = socket.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise DeploymentHold("Dedicated rootless socket unavailable")
    env = clean_environment(uid)
    os.environ.clear()
    os.environ.update(env)
    if (
        run([str(PROVIDER), "version", "--short"], env).decode().strip()
        != approval["compose_provider_version"]
    ):
        raise DeploymentHold("Compose provider mismatch")
    info = json.loads(run([PODMAN, "info", "--format", "json"], env))
    if info.get("host", {}).get("security", {}).get("rootless") is not True:
        raise DeploymentHold("Rootless Podman required")
    fd = os.open(HOME / "deploy.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pending = HOME / "deployment-pending.json"
        try:
            pending.lstat()
        except FileNotFoundError:
            pass
        else:
            raise DeploymentHold("Unresolved deployment requires operator inspection")
        source = Path(tempfile.mkdtemp(prefix="release-" + commit + "-", dir=HOME))
        extract_archive(archive, source)
        transient = Path(tempfile.mkdtemp(prefix="notebook-poc-", dir=runtime))
        raw = transient / "raw"
        consumers = transient / "consumers"
        cleanup_attempted = False
        admin_unknown = False
        admin_containers = {
            action: f"notebook-poc-admin-{source.name}-{action}"
            for action in ("migrate", "activate-api-role")
        }

        def remove_admin() -> None:
            nonlocal cleanup_attempted
            cleanup_attempted = True
            admin = consumers / "admin"
            try:
                metadata = admin.lstat()
            except FileNotFoundError:
                return
            if not stat.S_ISDIR(metadata.st_mode):
                raise DeploymentHold("Invalid one-shot credential directory")
            run([PODMAN, "unshare", "/usr/bin/rm", "-r", "--", str(admin)], env)
            try:
                admin.lstat()
            except FileNotFoundError:
                return
            raise DeploymentHold("One-shot credential cleanup incomplete")

        try:
            # Retrieval/validation completes before even a pull or service change.
            read_bootstrap(HOME / "bws-reader-token", form="raw")
            try:
                provision(approval["bws_manifest"], HOME / "bws-reader-token", raw, bws=str(BWS))
                validate_runtime_values(
                    {name: private_value(raw / name) for name in RUNTIME_SECRET_NAMES}
                )
                stage_consumers(
                    raw,
                    consumers,
                    postgres_uid=approval["postgres_uid"],
                    postgres_gid=approval["postgres_gid"],
                    env=env,
                )
            finally:
                if raw.exists():
                    shutil.rmtree(raw)
            environment = source / "runtime.env"
            environment.write_text(
                "\n".join(
                    [
                        "NOTEBOOK_POC_API_IMAGE=" + approval["images"]["api"],
                        "NOTEBOOK_POC_FRONTEND_IMAGE=" + approval["images"]["frontend"],
                        "NOTEBOOK_POC_SMTP_HOST=" + approval["smtp_host"],
                        "NOTEBOOK_POC_SECRETS=" + str(consumers),
                    ]
                )
                + "\n"
            )
            os.chmod(environment, 0o600)
            compose = [
                PODMAN,
                "compose",
                "--env-file",
                str(environment),
                "--project-name",
                "notebook-poc",
                "-f",
                str(source / "compose.poc.yaml"),
            ]
            run([*compose, "config", "--quiet"], env)
            run([*compose, "pull"], env, timeout=600)
            fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(
                    {
                        "commit": commit,
                        "phase": "service-changes-pending",
                        "admin_containers": admin_containers,
                        "consumer_directory": str(consumers),
                    },
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            sync_home()
            run([*compose, "up", "-d", "--no-build", "db", "auth"], env)
            for action in ("migrate", "activate-api-role"):
                # A lost CLI is not a stopped daemon-managed container. Only the
                # pinned provider's attached successful exit acknowledges completion.
                admin_unknown = True
                run(
                    [
                        *compose,
                        "run",
                        "--rm",
                        "--no-deps",
                        "--name",
                        admin_containers[action],
                        "--entrypoint",
                        "python",
                        "--env",
                        "PYTHONPATH=/app",
                        "--volume",
                        str(source / "deploy") + ":/app/deploy:ro",
                        "--volume",
                        str(source / "docker/poc-admin.py") + ":/opt/poc-admin.py:ro",
                        "--volume",
                        str(consumers / "admin") + ":/run/poc-admin:ro",
                        "api",
                        "/opt/poc-admin.py",
                        action,
                    ],
                    env,
                    timeout=180,
                )
                admin_unknown = False
            remove_admin()
            run([*compose, "up", "-d", "--no-build"], env)
            # First-account provisioning and acceptance stay separate, private and
            # explicit. Neither a successful Compose exit nor this record authorizes
            # publication; deploy.poc_publication requires real acceptance + readiness.
            (source / "started.json").write_text(
                json.dumps(
                    {"commit": commit, "profile": "login-workspace-v1", "publication": False}
                )
            )
            # Clearing is reachable only after acknowledged admin completion and
            # successful controlled startup, never after uncertain execution.
            pending.unlink()
            sync_home()
        finally:
            if not cleanup_attempted and not admin_unknown:
                remove_admin()


def main() -> int:
    try:
        if len(sys.argv) != 2 or not re.fullmatch(r"[0-9a-f]{40}", sys.argv[1]):
            raise DeploymentHold("One approved app commit required")
        deploy(sys.argv[1])
    except (
        DeploymentHold,
        ProvisionError,
        SetupError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        tarfile.TarError,
    ):
        print(
            "PoC deployment held; no public route changed. Operator inspection required.",
            file=sys.stderr,
        )
        return 1
    print("Owned PoC services started; acceptance and public activation remain separate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
