"""Synthetic-only diagnostic seam tests: never execute SQL, Docker or remote code."""

import json
import sys

import pytest

CANARY = "SYNTHETIC_STREAM_CANARY_74a"


def test_native_start_detach_seam_preserves_container_client_boundary():
    import signal

    from backend.tests.storage_real.diagnostics import selected_state, supervise

    assert selected_state(b'{"ExitCode":0,"Running":true,"Health":"healthy"}') == (
        "exit_0",
        "running_true",
        "health_healthy",
    )
    result = supervise(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        seconds=1,
        ready=lambda: True,
        detach=True,
    )
    assert result.exit_code == -signal.SIGKILL
    assert "health_ready" in result.events


def test_known_missing_native_role_has_fixed_nonsecret_category():
    from backend.tests.storage_real.diagnostics import classify

    assert classify(
        [b'psql:99-roles.sql:7: ERROR:  role "supabase_functions_admin" does not exist\n']
    ) == ("error_functions_admin_missing",)


def test_start_wiring_never_requests_raw_state_or_enables_docker_logs():
    from pathlib import Path

    from backend.tests.storage_real.launch import STATE_FORMAT

    assert ".Config" not in STATE_FORMAT and ".Env" not in STATE_FORMAT
    assert ".State.Health.Log" not in STATE_FORMAT
    assert "{{.State.Error}}" not in STATE_FORMAT
    assert "{{if .State.Error}}present{{end}}" in STATE_FORMAT
    compose = Path(__file__).with_name("compose.yaml").read_text()
    assert "driver: none" in compose
    assert ">/dev/null 2>&1" not in compose


def test_official_webhook_dependency_is_mounted_before_password_roles():
    import hashlib
    from pathlib import Path

    base = Path(__file__).parent
    compose = base.joinpath("compose.yaml").read_text()
    lock = json.loads(base.joinpath("lock.json").read_text())
    assert "./webhooks.sql:/docker-entrypoint-initdb.d/init-scripts/98-webhooks.sql:ro" in compose
    data = base.joinpath("webhooks.sql").read_bytes()
    assert hashlib.sha256(data).hexdigest() == lock["native_init_sha256"]["webhooks.sql"]
    assert b"CREATE USER supabase_functions_admin" in data


def test_stream_split_phase_and_error_never_exposes_canary(capsys):
    from backend.tests.storage_real.diagnostics import classify

    canary = b"SYNTHETIC_STREAM_CANARY_74a"
    events = classify(
        [
            b"/docker-entrypoint-initdb.d/migrate.sh: running /docker-entrypoint-initdb.d/",
            b'init-scripts/99-roles.sql\npsql:99-roles.sql:4: ERROR:  role "' + canary,
            b'" does not exist\n',
        ]
    )
    assert events == ("phase_roles", "error_missing_role")
    assert capsys.readouterr() == ("", "")
    assert canary.decode() not in repr(events)


@pytest.mark.parametrize(
    "payload",
    [
        b"ERROR:  permission denied " + CANARY.encode() + b"\n",
        b"ERROR:  extension synthetic is not available " + CANARY.encode() + b"\n",
        b"ERROR:  other " + CANARY.encode() + b"\n",
        b"not-a-phase " + CANARY.encode() + b"\n",
        b"ERROR:  42501: " + CANARY.encode() + b"\n",
        b"ERROR:  ZZZZZ: " + CANARY.encode() + b"\n",
        b"\x00ERROR:  permission denied " + CANARY.encode() + b"\n",
        b"\xffERROR:  permission denied " + CANARY.encode() + b"\n",
        b"\x1b[31mERROR:  permission denied " + CANARY.encode() + b"\n",
        b"x" * 3000 + CANARY.encode() + b"\n",
        b"ERROR:  permission denied " + CANARY.encode(),
        b"x" * 5000,
    ],
)
def test_untrusted_records_only_produce_fixed_tokens(payload, capsys, tmp_path):
    from backend.tests.storage_real.diagnostics import classify, render

    events = classify([payload])
    output = render(events)
    assert CANARY not in output
    assert "permission denied" not in output
    assert "ERROR:" not in output
    assert "42501" not in output and "ZZZZZ" not in output
    assert len(output) < 2048
    assert capsys.readouterr() == ("", "")
    assert list(tmp_path.iterdir()) == []


def test_forged_phase_and_multiline_are_not_echoed():
    from backend.tests.storage_real.diagnostics import classify

    assert classify([b"phase_roles\nSQLSTATE=42501\n" + CANARY.encode() + b"\n"]) == ("unknown",)
    assert classify(
        [
            b"/docker-entrypoint-initdb.d/migrate.sh: running "
            b"/docker-entrypoint-initdb.d/init-scripts/99-roles.sql " + CANARY.encode() + b"\n"
        ]
    ) == ("unknown",)
    assert classify([b"ERROR:  permission denied\nCONTEXT: " + CANARY.encode() + b"\n"]) == (
        "error_permission",
        "unknown",
    )


def test_bounds_and_iterable_failure():
    from backend.tests.storage_real.diagnostics import classify

    assert "limit_bytes" in classify([b"x\n" * 2048] * 257)
    assert classify([b"x" * 2048, b"x\n"]) == ("unknown",)
    assert classify(["not bytes"]) == ("unknown",)

    def broken():
        yield b"\n"
        raise RuntimeError(CANARY)

    assert classify(broken()) == ("classifier_failure",)


def test_selected_metadata_drops_all_untrusted_fields():
    from backend.tests.storage_real.diagnostics import selected_state

    raw = json.dumps(
        {"ExitCode": 3, "OOMKilled": False, "Health": "unhealthy", "Error": CANARY}
    ).encode()
    assert selected_state(raw) == ("exit_3", "oom_false", "health_unhealthy", "engine_error")
    for bad in (b"", b"[]", b"null", b"{", b"\xff", b"x" * 4097):
        assert selected_state(bad) == ("metadata_unknown",)
    for bad in (
        {"ExitCode": True},
        {"ExitCode": -1},
        {"ExitCode": 999},
        {"Health": CANARY},
        {"Env": [CANARY]},
    ):
        assert selected_state(json.dumps(bad).encode()) == ("metadata_unknown",)


@pytest.mark.parametrize("code,expected", [("0", 0), ("3", 3)])
def test_synthetic_child_exit_and_stream(code, expected, capsys, tmp_path):
    from backend.tests.storage_real.diagnostics import supervise

    child = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('" + CANARY + "\\n'); sys.exit(" + code + ")",
    ]
    result = supervise(child, seconds=2)
    assert result.exit_code == expected
    assert CANARY not in repr(result)
    assert capsys.readouterr() == ("", "")
    assert list(tmp_path.iterdir()) == []


def test_synthetic_timeout_signal_and_spawn_failure():
    from backend.tests.storage_real.diagnostics import supervise

    result = supervise([sys.executable, "-c", "import time; time.sleep(10)"], seconds=0.05)
    assert result.exit_code is not None and result.exit_code < 0
    assert "limit_time" in result.events
    absent = supervise(["/a5/synthetic/nonexistent/program"], seconds=0.1)
    assert absent.exit_code is None and absent.events == ("spawn_failure",)


def test_classifier_failure_never_raw_fallback(monkeypatch, capsys):
    from backend.tests.storage_real import diagnostics

    def broken(_):
        raise KeyError(CANARY)

    monkeypatch.setattr(diagnostics, "classify", broken)
    result = diagnostics.supervise([sys.executable, "-c", "print('" + CANARY + "')"], seconds=1)
    assert result.events == ("classifier_failure",)
    assert CANARY not in repr(result)
    assert capsys.readouterr() == ("", "")


def test_signal_is_preserved_without_reinterpreting_as_success():
    import signal

    from backend.tests.storage_real.diagnostics import supervise

    result = supervise(
        [sys.executable, "-c", "import os,signal; os.kill(os.getpid(),signal.SIGTERM)"], seconds=1
    )
    assert result.exit_code == -signal.SIGTERM


def test_synthetic_flood_and_closed_pipe_have_finite_limits():
    import time

    from backend.tests.storage_real.diagnostics import supervise

    start = time.monotonic()
    flood = supervise([sys.executable, "-c", "import os; os.write(1,b'x'*1100000)"], seconds=1)
    assert "limit_bytes" in flood.events
    closed = supervise(
        [sys.executable, "-c", "import os,time; os.close(1); os.close(2); time.sleep(10)"],
        seconds=0.1,
    )
    assert closed.exit_code is not None and closed.exit_code < 0
    assert time.monotonic() - start < 3


def test_renderer_and_selected_metadata_only_return_finite_domains():
    from backend.tests.storage_real.diagnostics import render, selected_state

    assert render([CANARY] * 1000) == "[" + ",".join(['"unknown"'] * 32) + "]"
    assert selected_state(b'{"Health":"absent","Error":""}') == (
        "health_absent",
        "engine_error_absent",
    )
    assert selected_state(b'{"OOMKilled":true,"ExitCode":137}') == ("exit_137", "oom_true")
    assert selected_state(b'{"Error":{}}') == ("metadata_unknown",)
    assert selected_state(b'{"OOMKilled":1}') == ("metadata_unknown",)
    assert selected_state(b"[" * 1100 + b"]" * 1100) == ("metadata_unknown",)


def test_bytewise_splits_and_known_categories():
    from backend.tests.storage_real.diagnostics import classify

    record = (
        b"/docker-entrypoint-initdb.d/migrate.sh: running "
        b"/docker-entrypoint-initdb.d/init-scripts/99-jwt.sql\n"
    )
    assert classify([record[i : i + 1] for i in range(len(record))]) == ("phase_jwt",)
    assert classify([b"ERROR:  permission denied\n"]) == ("error_permission",)
    assert classify([b"ERROR:  extension synthetic is not available\n"]) == ("error_extension",)
    assert classify([b"ERROR:  arbitrary data\n"]) == ("error_other",)


def test_no_stream_execution_or_environment_inheritance(monkeypatch):
    from backend.tests.storage_real.diagnostics import classify, supervise

    monkeypatch.setenv("A5_SYNTHETIC_CANARY", CANARY)
    assert classify([b"$(touch /a5/not-executed)\nSELECT dangerous();\n"]) == ("unknown",)
    result = supervise(
        [
            sys.executable,
            "-c",
            "import os,sys; sys.exit(9 if 'A5_SYNTHETIC_CANARY' in os.environ else 0)",
        ],
        seconds=1,
    )
    assert result.exit_code == 0


def test_classifier_time_and_renderer_exception_are_failclosed(monkeypatch):
    from backend.tests.storage_real import diagnostics

    ticks = iter([0, 2])
    monkeypatch.setattr(diagnostics.time, "monotonic", lambda: next(ticks))
    assert diagnostics.classify([b""], seconds=1) == ("limit_time",)
    assert diagnostics.classify([], seconds=0) == ("classifier_failure",)

    def broken():
        raise KeyError(CANARY)
        yield "unknown"

    assert diagnostics.render(broken()) == '["classifier_failure"]'
