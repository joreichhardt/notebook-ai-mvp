"""Configuration and settlement only; never import/run the parked text worker."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app.workers import local_runtime as module
from sqlalchemy.engine import URL


@pytest.fixture
def configured(tmp_path, monkeypatch):
    root = tmp_path / "originals"
    root.mkdir(mode=0o700)
    paths = []
    for name in ("notebook_app", "notebook_text_scheduler"):
        path = tmp_path / name
        path.touch(mode=0o600)
        path.write_text(URL.create("postgresql", username=name, host="db").render_as_string())
        paths.append(str(path))
    settings = module.WorkerSettings(
        local_text_worker_enabled=True,
        local_storage_root=str(root),
        database_url_file=paths[0],
        text_scheduler_database_url_file=paths[1],
    )
    monkeypatch.setattr(module, "WorkerSettings", lambda: settings)
    engines = [MagicMock(), MagicMock()]
    constructor = MagicMock(side_effect=engines)
    monkeypatch.setattr(module, "create_engine", constructor)
    return settings, engines, constructor


def test_disabled_reads_no_file_or_worker(monkeypatch):
    settings = module.WorkerSettings(local_text_worker_enabled=False)
    monkeypatch.setattr(module, "WorkerSettings", lambda: settings)
    read = MagicMock(side_effect=AssertionError("must not read"))
    monkeypatch.setattr(module, "_secret", read)
    before = tuple(sys.modules.get(name) for name in ("app.workers.text", "app.workers.supervisor"))
    assert module.configured_runtime() is None
    read.assert_not_called()
    after = tuple(sys.modules.get(name) for name in ("app.workers.text", "app.workers.supervisor"))
    assert after == before


def test_configured_engines_are_separate_bounded_and_redacted(configured):
    settings, engines, constructor = configured
    runtime = module.configured_runtime()
    assert runtime is not None
    assert runtime.app_engine is engines[0] and runtime.scheduler_engine is engines[1]
    assert runtime.storage.root == module.Path(settings.local_storage_root)
    assert "app_engine=" not in repr(runtime) and "scheduler_engine=" not in repr(runtime)
    assert constructor.call_count == 2
    for call in constructor.call_args_list:
        options = call.kwargs
        assert options["pool_size"] == 1 and options["max_overflow"] == 0
        assert options["pool_timeout"] == 1 and options["hide_parameters"] is True
        assert options["connect_args"]["connect_timeout"] == 3
        assert options["connect_args"]["options"].endswith(
            "-c statement_timeout=3000 -c lock_timeout=1000"
        )
    for engine in engines:
        engine.connect.assert_not_called()
    runtime.dispose()
    for engine in engines:
        engine.dispose.assert_called_once()


@pytest.mark.parametrize(
    "field", ["local_storage_root", "database_url_file", "text_scheduler_database_url_file"]
)
def test_missing_configuration_is_opaque(configured, field):
    settings, _, _ = configured
    setattr(settings, field, "")
    with pytest.raises(module.WorkerUnavailable, match="^configuration_unavailable$"):
        module.configured_runtime()


def test_wrong_login_is_rejected_before_engine_creation(configured):
    settings, _, constructor = configured
    module.Path(settings.database_url_file).write_text(
        URL.create("postgresql", username="postgres", host="db").render_as_string()
    )
    with pytest.raises(module.WorkerUnavailable, match="^configuration_unavailable$"):
        module.configured_runtime()
    constructor.assert_not_called()


def test_query_cannot_override_checked_login(configured):
    settings, _, constructor = configured
    module.Path(settings.database_url_file).write_text(
        URL.create(
            "postgresql", username="notebook_app", host="db", query={"user": "postgres"}
        ).render_as_string()
    )
    with pytest.raises(module.WorkerUnavailable, match="^configuration_unavailable$"):
        module.configured_runtime()
    constructor.assert_not_called()


def test_insecure_scheduler_file_disposes_first_engine(configured):
    settings, engines, constructor = configured
    module.Path(settings.text_scheduler_database_url_file).chmod(0o644)
    with pytest.raises(module.WorkerUnavailable, match="^configuration_unavailable$"):
        module.configured_runtime()
    assert constructor.call_count == 1
    engines[0].dispose.assert_called_once()


def test_second_engine_failure_is_opaque_and_disposes_first(configured):
    _, engines, constructor = configured
    constructor.side_effect = [engines[0], RuntimeError("private driver details")]
    with pytest.raises(module.WorkerUnavailable, match="^configuration_unavailable$"):
        module.configured_runtime()
    engines[0].dispose.assert_called_once()


@pytest.mark.parametrize("result", ["idle", "succeeded", "failed", "cancelled"])
def test_only_confirmed_quiescent_outcomes_are_eligible_for_next_process(result):
    consumer, runner = cycle(result)
    assert asyncio.run(module._finish_one(consumer, runner)) == result
    assert consumer.calls == 1 and consumer.stopped and runner.closes == 1


@pytest.mark.parametrize(
    "mutation",
    [
        {"closed": False},
        {"pending": True},
        {"outcome_unknown": True},
        {"lease_failed": True},
        {"cancelled": True},
        {"renewal_active": True},
        {"terminal_observed": False},
        {"acknowledgment": "unconfirmed"},
        {"phase": "uncertain"},
        {"result": "yielded"},
        {"result": "not_claimed"},
        {"result": "lease_lost"},
        {"raises": True},
    ],
)
def test_uncertainty_is_held_without_second_delivery(mutation):
    consumer, runner = cycle("succeeded", **mutation)
    assert asyncio.run(module._finish_one(consumer, runner)) == "held"
    assert consumer.calls == 1 and consumer.stopped and runner.closes == 1


def cycle(initial, **changes):
    state = {
        "result": initial,
        "phase": "idle" if initial == "idle" else "settled",
        "terminal_observed": True,
        "acknowledgment": "confirmed",
        "outcome_unknown": False,
        "lease_failed": False,
        "cancelled": False,
        "renewal_active": False,
        "pending": False,
        "closed": True,
        "raises": False,
    }
    state.update(changes)
    snapshot = SimpleNamespace(**state)

    class Consumer:
        calls = 0
        stopped = False

        async def run_one(self):
            self.calls += 1
            if snapshot.raises:
                raise RuntimeError("private operation details")
            return snapshot.result

        def request_stop(self):
            self.stopped = True

        def snapshot(self):
            return snapshot

    class Runner:
        closes = 0

        async def close(self, *, timeout):
            assert timeout == 3
            self.closes += 1
            return snapshot.closed

        def snapshot(self):
            return snapshot

    return Consumer(), Runner()


@pytest.mark.parametrize("failure", [False, True])
def test_cli_never_outputs_arbitrary_backend_details(monkeypatch, capsys, failure):
    monkeypatch.setattr(sys, "argv", ["worker"])
    monkeypatch.setattr(module, "configured_runtime", lambda: object())

    async def fake_execute(runtime):
        if failure:
            raise RuntimeError("private operation details")
        return "private operation details"

    monkeypatch.setattr(module, "execute", fake_execute)
    assert module.main() == 75
    assert capsys.readouterr().out == '{"status": "held"}\n'


def test_cli_rejects_identity_arguments_without_echo(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["worker", "untrusted-identity"])
    assert module.main() == 64
    assert capsys.readouterr().out == '{"status": "invalid_arguments"}\n'
