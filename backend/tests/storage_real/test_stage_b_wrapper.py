"""Actual unchanged wrapper body, mapped real FS and spawn sentinel; no DB/network."""

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest

BASE = Path(__file__).parent
URL = "postgresql://supabase_storage_admin@db:5432/postgres"
PREFIX = "db:5432:postgres:supabase_storage_admin:"
NEGATIVES = (
    "password_empty",
    "password_nonempty",
    "passfile_empty",
    "passfile_wrong",
    "node_options_empty",
    "node_options_nonempty",
    "node_path",
    "node_extra_ca",
    "alternate_pool",
    "inherited_url",
    "tls_override",
    "tenant_override",
    "vector_override",
    "queue_override",
    "roles_override",
    "missing",
    "mode",
    "json_mode",
    "parent_mode",
    "host",
    "port",
    "database",
    "role",
    "wildcard",
    "empty_secret",
    "malformed",
    "two_rows",
    "trailing_newline",
    "pass_symlink",
    "json_symlink",
    "parent_symlink",
    "hardlink",
    "json_hardlink",
    "oversize",
    "json_oversize",
    "wrong_json_url",
    "uid_mismatch",
)


def private_write(path, text):
    with open(path, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
        stream.write(text)


def run_wrapper(root, environment, profile, wrong_uid=False):
    result = subprocess.run(
        [
            shutil.which("node") or "/nonexistent-node",
            str(BASE / "stage_b_wrapper_probe.cjs"),
            str(BASE / "stage_b_wrapper.cjs"),
        ],
        input=json.dumps(
            {
                "root": str(root),
                "environment": environment,
                "profile": profile,
                "wrong_uid": wrong_uid,
            }
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=3,
        env={"PATH": os.defpath},
        check=False,
    )
    assert result.returncode == 0, "wrapper_probe_failed"
    assert len(result.stdout) < 128, "wrapper_probe_output_exceeded"
    return json.loads(result.stdout)


def probe(tmp_path, case):
    root = tmp_path / "secrets"
    root.mkdir(mode=0o700)
    owner = secrets.token_hex(32)
    row = PREFIX + owner + "\n"
    data = {
        "DATABASE_URL": URL,
        "AUTH_JWT_SECRET": secrets.token_hex(32),
        "ANON_KEY": secrets.token_hex(32),
        "SERVICE_KEY": secrets.token_hex(32),
    }
    profile = json.loads((BASE / "stage_b.compose.json").read_text())["services"]["storage"][
        "environment"
    ]
    environment = dict(profile, UNRELATED_INHERITED="SYNTHETIC_NOT_FOR_CHILD", NODE_VERSION="26")
    override = {
        "password_empty": ("PGPASSWORD", ""),
        "password_nonempty": ("PGPASSWORD", "SYNTHETIC"),
        "passfile_empty": ("PGPASSFILE", ""),
        "passfile_wrong": ("PGPASSFILE", "/wrong"),
        "node_options_empty": ("NODE_OPTIONS", ""),
        "node_options_nonempty": ("NODE_OPTIONS", "--require=wrong"),
        "node_path": ("NODE_PATH", ""),
        "node_extra_ca": ("NODE_EXTRA_CA_CERTS", ""),
        "alternate_pool": ("DATABASE_POOL_URL", URL),
        "inherited_url": ("DATABASE_URL", URL),
        "tls_override": ("DATABASE_SSL_ROOT_CERT", ""),
        "tenant_override": ("IS_MULTITENANT", "true"),
        "vector_override": ("VECTOR_BUCKETS_ENABLED", "true"),
        "queue_override": ("PG_QUEUE_ENABLE", "true"),
        "roles_override": ("DB_INSTALL_ROLES", "true"),
    }
    if case in override:
        key, value = override[case]
        environment[key] = value
    for label, old, new in (
        ("host", "db:", "foreign:"),
        ("port", ":5432:", ":5433:"),
        ("database", ":postgres:", ":other:"),
        ("role", ":supabase_storage_admin:", ":other:"),
        ("wildcard", "db:", "*:"),
    ):
        if case == label:
            row = row.replace(old, new)
    if case == "empty_secret":
        row = PREFIX + "\n"
    if case == "malformed":
        row = "not-a-passfile\n"
    if case == "two_rows":
        row *= 2
    if case == "trailing_newline":
        row += "\n"
    if case == "oversize":
        row = "x" * 257
    if case == "wrong_json_url":
        data["DATABASE_URL"] = URL + "?sslmode=disable"
    text = json.dumps(data)
    if case == "json_oversize":
        text = " " * 16385 + text
    private_write(root / "environment.json", text)
    private_write(root / "database.pgpass", row)
    if case == "missing":
        (root / "database.pgpass").unlink()
    if case == "mode":
        (root / "database.pgpass").chmod(0o644)
    if case == "json_mode":
        (root / "environment.json").chmod(0o644)
    if case == "parent_mode":
        root.chmod(0o755)
    for label, name in (("pass_symlink", "database.pgpass"), ("json_symlink", "environment.json")):
        if case == label:
            target = root / (name + ".target")
            (root / name).rename(target)
            (root / name).symlink_to(target)
    if case in ("hardlink", "json_hardlink"):
        name = "environment.json" if case == "json_hardlink" else "database.pgpass"
        os.link(root / name, root / "second-link")
    if case == "parent_symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        selected = alias
    else:
        selected = root
    try:
        return run_wrapper(selected, environment, profile, case == "uid_mismatch")
    finally:
        if case == "parent_symlink":
            selected.unlink()
        shutil.rmtree(root)


@pytest.mark.skipif(
    not os.environ.get("A5_STAGE_B_LIBRARY_CACHE"),
    reason="explicit independent pinned-library research cache required; no downloads/installs",
)
def test_actual_pinned_driver_selects_generated_passfile_without_network(tmp_path):
    from backend.tests.storage_real.stage_b_launch import credentials

    root = tmp_path / "driver"
    root.mkdir(mode=0o700)
    try:
        credentials(root)
        result = subprocess.run(
            [shutil.which("node") or "/nonexistent-node", str(BASE / "stage_b_pgpass_probe.cjs")],
            input=json.dumps(
                {"cache": os.environ["A5_STAGE_B_LIBRARY_CACHE"], "root": str(root / "storage")}
            ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            env={"PATH": os.defpath},
            check=False,
        )
        assert result.returncode == 0, "pinned_library_proof_failed"
        assert len(result.stdout) < 128, "pinned_library_output_exceeded"
        record = json.loads(result.stdout)
        assert (
            record.get("ok") is True and record.get("checks") == 10 and record.get("attempts") == 0
        ), "pinned_library_boundary_failed"
    finally:
        shutil.rmtree(root)


def test_actual_generated_packet_passes_unchanged_wrapper(tmp_path):
    from backend.tests.storage_real.stage_b_launch import credentials

    root = tmp_path / "generated"
    root.mkdir(mode=0o700)
    try:
        credentials(root)
        profile = json.loads((BASE / "stage_b.compose.json").read_text())["services"]["storage"][
            "environment"
        ]
        assert run_wrapper(root / "storage", dict(profile), profile) == {
            "status": 0,
            "starts": 1,
            "boundary": True,
        }
    finally:
        shutil.rmtree(root)


def test_actual_wrapper_positive_child_environment_is_finite_passwordless(tmp_path):
    assert probe(tmp_path, "positive") == {"status": 0, "starts": 1, "boundary": True}


@pytest.mark.parametrize("case", NEGATIVES)
def test_actual_wrapper_rejects_before_any_child_start(tmp_path, case):
    assert probe(tmp_path, case) == {"status": 70, "starts": 0, "boundary": False}
