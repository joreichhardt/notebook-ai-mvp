"""Validate the merged upload test project with inert paths, without starting services."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_merged_upload_test_profile_is_private_and_separates_worker():
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker Compose required for merged-config test")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "NOTEBOOK_POC_API_IMAGE": "example.invalid/test-api:unused",
        "NOTEBOOK_POC_FRONTEND_IMAGE": "example.invalid/test-web:unused",
        "NOTEBOOK_POC_SMTP_HOST": "example.invalid",
        "NOTEBOOK_POC_SECRETS": "/nonexistent/test-release",
        "NOTEBOOK_UPLOAD_TEST_ORIGINALS": "/nonexistent/test-originals",
        "NOTEBOOK_UPLOAD_TEST_IO_FILE": "/nonexistent/test-io",
        "NOTEBOOK_UPLOAD_TEST_SCHEDULER_FILE": "/nonexistent/test-scheduler",
    }
    command = [docker, "compose", "-f", "compose.poc.yaml", "-f", "compose.upload-test.yaml"]

    def config(*args):
        return subprocess.check_output([*command, *args], env=env, cwd=ROOT, text=True, timeout=15)

    assert set(config("config", "--services").split()) == {"api", "db", "auth"}
    doc = json.loads(config("--profile", "*", "config", "--format", "json"))
    assert doc["name"] == "notebook-upload-test"
    assert doc["networks"]["private"]["internal"] is True
    assert doc["volumes"]["postgres"]["name"] == "notebook-upload-test_postgres"
    worker = doc["services"]["text-worker"]
    assert not worker.get("ports") and set(worker["networks"]) == {"private"}
    mounts = worker["volumes"]
    assert {m["source"] for m in mounts} == {
        env["NOTEBOOK_UPLOAD_TEST_ORIGINALS"],
        env["NOTEBOOK_UPLOAD_TEST_SCHEDULER_FILE"],
        env["NOTEBOOK_POC_SECRETS"] + "/api/notebook_database_url",
    }
    # Some Compose releases omit false booleans in their normalized JSON.
    # Verify every worker bind explicitly opts out in the authored overlay too.
    worker_source = (ROOT / "compose.upload-test.yaml").read_text().split("\n  text-worker:\n")[1]
    blocks = worker_source.split("\n      - type: bind\n")[1:]
    assert len(blocks) == len(mounts) == 3
    assert all(
        "\n        bind:\n          create_host_path: false\n" in block + "\n" for block in blocks
    )
    assert all(
        m["read_only"] and m.get("bind", {}).get("create_host_path", False) is False for m in mounts
    )
    assert not any("BWS_ACCESS_TOKEN" in s.get("environment", {}) for s in doc["services"].values())
