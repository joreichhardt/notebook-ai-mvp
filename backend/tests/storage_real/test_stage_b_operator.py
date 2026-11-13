"""Host reporter tests use only synthetic Python subprocesses, never Docker."""

import json
import sys

import pytest


def test_nonassigned_path_refuses_before_any_container_command(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from backend.tests.storage_real import stage_b_launch as module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["stage_b_launch.py", "--run-stage-b"])
    trap = Mock(side_effect=AssertionError("container_command_not_allowed"))
    monkeypatch.setattr(module, "inventory", trap)
    assert module.main() == 2
    trap.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["constructor", "register"])
def test_setup_failure_reaps_owned_child(failure, monkeypatch):
    from backend.tests.storage_real import stage_b_launch as module

    children = []
    original = module.subprocess.Popen

    def track(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    class Broken:
        def __init__(self):
            if failure == "constructor":
                raise OSError("SETUP_CANARY")

        def register(self, *args):
            raise OSError("SETUP_CANARY")

        def close(self):
            pass

    monkeypatch.setattr(module.subprocess, "Popen", track)
    monkeypatch.setattr(module.selectors, "DefaultSelector", Broken)
    with pytest.raises(ValueError, match="fixed_child_failure"):
        module.fixed_run(
            [sys.executable, "-c", "import time;time.sleep(10)"], {}, 1, lambda record: None
        )
    assert len(children) == 1 and children[0].poll() is not None


def test_quiet_normal_exit_after_eof_preserves_zero():
    from backend.tests.storage_real.stage_b_launch import fixed_run

    assert (
        fixed_run(
            [sys.executable, "-c", "import os,time;os.close(1);os.close(2);time.sleep(0.15)"],
            {},
            1,
            lambda record: None,
        )
        == 0
    )


def test_generated_credentials_are_separated_private_and_removed(tmp_path):
    import base64
    import hashlib
    import hmac
    import os
    import shutil

    from backend.tests.storage_real.stage_b_launch import credentials

    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    previous_umask = os.umask(0o077)
    try:
        credentials(root)
        checks = []
        checks.append(
            all(p.stat().st_mode & 0o777 == 0o600 for p in root.rglob("*") if p.is_file())
        )
        data = json.loads((root / "storage/environment.json").read_text())
        bootstrap = (root / "db/bootstrap").read_text()
        checks.append(bootstrap not in data["DATABASE_URL"])
        checks.append(
            data["DATABASE_URL"] == "postgresql://supabase_storage_admin@db:5432/postgres"
        )
        passfile = root / "storage/database.pgpass"
        owner = (root / "runner/owner-password").read_text()
        checks.append(passfile.exists())
        if passfile.exists():
            checks.append(
                passfile.read_text() == "db:5432:postgres:supabase_storage_admin:" + owner + "\n"
            )
            checks.append(owner not in (root / "storage/environment.json").read_text())
        checks.append(set(data) == {"DATABASE_URL", "AUTH_JWT_SECRET", "ANON_KEY", "SERVICE_KEY"})
        raw = (root / "runner/service.jwt").read_text().split(".")
        signature = (
            base64.urlsafe_b64encode(
                hmac.new(
                    data["AUTH_JWT_SECRET"].encode(), ".".join(raw[:2]).encode(), hashlib.sha256
                ).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        checks.append(hmac.compare_digest(signature, raw[2]))
        claims = json.loads(base64.urlsafe_b64decode(raw[1] + "=" * (-len(raw[1]) % 4)))
        checks.append(claims["role"] == "service_role" and claims["exp"] - claims["iat"] == 1800)
        checks.append(not (root / "runner/environment.json").exists())
        assert all(checks), "synthetic_credential_boundary_failed"
    finally:
        os.umask(previous_umask)
        shutil.rmtree(root)
    assert not root.exists()


def test_unknown_and_credential_shaped_lines_never_escape(tmp_path):
    from backend.tests.storage_real.stage_b_launch import fixed_run

    records = []
    code = "print('DRIVER_DSN_CANARY'); print('STAGE_B_CASE=complete')"
    assert fixed_run([sys.executable, "-c", code], {}, 2, records.append) == 0
    assert records == [{"case": "complete"}]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("stage,sqlstate", [("CANARY", "42501"), ("cleanup", "CANARY")])
def test_failure_metadata_domain_is_closed(stage, sqlstate):
    from backend.tests.storage_real.stage_b_launch import safe_line

    line = "STAGE_B_FAILURE=" + json.dumps(
        {"stage": stage, "type": "unknown", "sqlstate": sqlstate}
    )
    assert safe_line(line) is None


def test_closed_pipe_hang_and_line_flood_are_bounded_without_raw_output(capsys):
    from backend.tests.storage_real.stage_b_launch import fixed_run

    for code in ("import os,time;os.close(1);os.close(2);time.sleep(10)", "print('CANARY'*1000)"):
        with pytest.raises(ValueError, match="fixed_child_failure"):
            fixed_run([sys.executable, "-c", code], {}, 0.2, lambda record: None)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
