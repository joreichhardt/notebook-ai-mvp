"""Local fixture-scheduler regression; no PostgreSQL, Alembic or worker-body acceptance.

Collect the unchanged worker module with pytest's public CLI/importer, then re-export
its actual fixture objects into a tiny test module. Its database skip mark stays on
the original module; no environment URL or skip override is used. Only fixture
globals and external dependencies receive synthetic in-memory doubles. Nested
pytest has no terminal/cache/plugins, and only closed phase outcomes leave it.
"""

import contextlib
import importlib
import io
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import psycopg
import pytest
import sqlalchemy
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]
WORKER = ROOT / "backend/tests/ingestion/test_worker.py"
PDF = "test_pdf_job_has_explicit_unsupported_failure_without_io"
HOOKS = ("claim_job", "heartbeat_job", "lock_job_for_publish", "finish_job")
BASE = "postgresql://synthetic@invalid.invalid/base"
MINIATURE = """from worker_fixture_exports import historical_pdf_database, scene, probe


def test_pdf_job_has_explicit_unsupported_failure_without_io(scene):
    probe.pdf_call(scene)


def test_subsequent_non_pdf(scene):
    probe.non_pdf_call(scene)
"""


class InjectedFailure(Exception):
    """Fixed local failure, never a database exception."""


class WorkerCollection:
    def pytest_collection_modifyitems(self, items):
        self.module = items[0].module
        self.names = [item.name for item in items]


class FixtureProbe:
    def __init__(self, module, scenario):
        import app.workers.text as worker

        from backend.tests.auth import leases_0005

        self.module, self.scenario = module, scenario
        self.worker, self.legacy = worker, leases_0005
        self.original_hooks = {name: getattr(worker, name) for name in HOOKS}
        self.events, self.reports, self.restored = [], [], []
        self.boundaries = []
        self.drop_state = []
        self.target = None
        self.case = 0
        self.scene_connections = 0
        self.admin_connections = 0

    def hooks_match(self, historical):
        return all(
            getattr(self.worker, name)
            is (getattr(self.legacy, name) if historical else self.original_hooks[name])
            for name in HOOKS
        )

    def check_scene_state(self):
        historical = self.case == 0
        assert self.module.URL == (self.target if historical else BASE)
        assert self.hooks_match(historical)

    @contextlib.contextmanager
    def admin_connect(self, url, *, autocommit):
        assert url == BASE and autocommit is True
        self.admin_connections += 1
        stage = "create" if self.admin_connections == 1 else "drop"
        assert self.admin_connections <= 2

        def execute(query):
            # The actual helper supplies SQL; never render, execute or retain it.
            self.events.append(stage)
            if stage == "drop":
                # Still patched here: monkeypatch must finalize AFTER this fixture.
                self.drop_state.append(self.module.URL == self.target and self.hooks_match(True))
                if self.scenario == "drop_failure":
                    raise InjectedFailure()

        yield SimpleNamespace(execute=execute)

    def migration_run(self, argv, **kwargs):
        assert argv == [
            str(ROOT / ".venv/bin/alembic"),
            "-c",
            "backend/migrations/alembic.ini",
            "upgrade",
            "0005",
        ]
        assert kwargs["cwd"] == ROOT
        assert kwargs["capture_output"] is True and kwargs["text"] is True
        assert kwargs["check"] is False
        target = make_url(kwargs["env"]["NOTEBOOK_MIGRATION_DATABASE_URL"])
        assert isinstance(target.database, str) and target.database.startswith("quota_version_")
        assert target.database != "base"
        self.target = target.set(drivername="postgresql").render_as_string(hide_password=False)
        self.events.append("migration")
        return SimpleNamespace(returncode=0)

    @contextlib.contextmanager
    def scene_connect(self, url):
        self.check_scene_state()
        assert url == self.module.URL
        self.scene_connections += 1
        stage = "scene_setup" if self.scene_connections == 1 else "scene_cleanup"
        assert self.scene_connections <= 2
        self.events.append(stage)
        statements = []

        def execute(query, params):
            self.check_scene_state()
            statements.append(True)

        yield SimpleNamespace(execute=execute)
        assert len(statements) == (8 if stage == "scene_setup" else 4)
        self.check_scene_state()
        self.events.append(stage + "_closed")

    def engine(self, url, **kwargs):
        self.check_scene_state()
        assert url == make_url(self.module.URL).set(
            drivername="postgresql+psycopg", username="notebook_app"
        )
        assert kwargs == {"hide_parameters": True}
        self.events.append("engine")
        if self.case == 0 and self.scenario == "scene_setup_failure":
            raise InjectedFailure()
        return SimpleNamespace(dispose=self.dispose)

    def dispose(self):
        self.check_scene_state()
        self.events.append("dispose")

    def pdf_call(self, scene):
        self.check_scene_state()
        assert scene[-1].engine.dispose == self.dispose
        self.events.append("pdf_call")
        if self.scenario == "call_failure":
            raise InjectedFailure()

    def non_pdf_call(self, scene):
        self.check_scene_state()
        assert self.admin_connections == 2
        assert scene[-1].engine.dispose == self.dispose
        self.events.append("non_pdf_call")

    def pytest_runtest_logreport(self, report):
        self.reports.append((self.case, report.when, report.outcome))
        if report.when == "teardown":
            self.restored.append(self.module.URL == BASE and self.hooks_match(False))
            self.events.append("restored")
            self.case += 1
            self.scene_connections = 0

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        # Inspect only in memory; never stringify an exception, SQL or traceback.
        if call.excinfo is not None:
            boundary = "unexpected"
            if call.excinfo.type is InjectedFailure:
                boundary = {
                    "engine": "scene_setup",
                    "pdf_call": "test_call",
                    "execute": "drop",
                }.get(call.excinfo.traceback[-1].name, "unexpected")
            elif call.excinfo.type is AttributeError and any(
                frame.name == "historical_pdf_database" and frame.lineno == 33
                for frame in call.excinfo.traceback
            ):
                boundary = "url_patch_wrong_namespace"
            self.boundaries.append(boundary)
            # DROP can mask the original setup error; retain only its closed boundary.
            cause = call.excinfo.value.__context__
            if isinstance(cause, AttributeError):
                trace = cause.__traceback__
                while trace is not None:
                    if (
                        trace.tb_frame.f_code.co_name == "historical_pdf_database"
                        and trace.tb_lineno == 34
                    ):
                        self.boundaries.append("url_patch_wrong_namespace")
                        break
                    trace = trace.tb_next
        yield


def run_pytest(path, mode, root, plugin, *, collect=False):
    args = [
        str(path),
        "-c",
        str(ROOT / "pyproject.toml"),
        "--rootdir",
        str(root),
        "--import-mode=" + mode,
        "--noconftest",
        "--assert=plain",
        "-o",
        "addopts=",
        "-p",
        "no:terminal",
        "-p",
        "no:cacheprovider",
    ]
    if collect:
        args.append("--collect-only")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return pytest.main(args, plugins=[plugin])


@contextlib.contextmanager
def isolated_worker_imports():
    """Do not reuse or mutate worker modules already collected by the outer suite."""
    ingestion = importlib.import_module("backend.tests.ingestion")

    names = ("backend.tests.ingestion.test_worker", "test_worker", "test_scheduled_worker_fixture")
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in names}
    parent_attribute = getattr(ingestion, "test_worker", missing)
    environment = dict(os.environ)
    with pytest.MonkeyPatch.context() as patch:
        # Set then delete also registers ownership when the original entry was absent.
        for name in names:
            patch.setitem(sys.modules, name, None)
            patch.delitem(sys.modules, name)
        patch.setattr(ingestion, "test_worker", None, raising=False)
        patch.delattr(ingestion, "test_worker")
        patch.setattr(sys, "path", sys.path.copy())
        for key in tuple(os.environ):
            if "DATABASE_URL" in key or "POSTGRES_URL" in key or key.startswith("PG"):
                patch.delenv(key)
        patch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
        patch.delenv("PYTEST_ADDOPTS", raising=False)
        patch.delenv("PYTEST_PLUGINS", raising=False)
        # Nested pytest owns this key during its calls, but must not erase the outer one.
        patch.setenv("PYTEST_CURRENT_TEST", "synthetic")
        patch.delenv("PYTEST_CURRENT_TEST")
        yield
    assert all(sys.modules.get(name, missing) is old for name, old in previous.items())
    assert getattr(ingestion, "test_worker", missing) is parent_attribute
    environment_restored = dict(os.environ) == environment
    assert environment_restored


def scheduled_case(mode, scenario, tmp_path):
    collection = WorkerCollection()
    assert run_pytest(WORKER, mode, ROOT, collection, collect=True) == 0
    module = collection.module
    assert module.__name__ == (
        "backend.tests.ingestion.test_worker" if mode == "importlib" else "test_worker"
    )
    assert len(collection.names) == 20 and collection.names[17] == PDF
    original_mark = module.pytestmark
    assert original_mark.name == "skipif" and original_mark.args == (True,)
    assert not module.URL

    from backend.tests.auth import database_versions

    probe = FixtureProbe(module, scenario)
    # Re-export exactly the fixture objects, not __wrapped__ calls or copied bodies.
    exports = ModuleType("worker_fixture_exports")
    vars(exports).update(
        historical_pdf_database=module.historical_pdf_database, scene=module.scene, probe=probe
    )
    miniature = tmp_path / "test_scheduled_worker_fixture.py"
    miniature.write_text(MINIATURE)
    with pytest.MonkeyPatch.context() as wiring:
        wiring.setitem(sys.modules, exports.__name__, exports)
        wiring.delitem(sys.modules, miniature.stem, raising=False)
        wiring.setattr(module, "URL", BASE)
        wiring.setattr(module, "psycopg", SimpleNamespace(connect=probe.scene_connect))
        wiring.setattr(module, "create_engine", probe.engine)
        wiring.setattr(database_versions, "psycopg", SimpleNamespace(connect=probe.admin_connect))
        wiring.setattr(database_versions, "subprocess", SimpleNamespace(run=probe.migration_run))
        result = run_pytest(miniature, mode, tmp_path, probe)
        final_restored = module.URL == BASE and probe.hooks_match(False)
    assert module.URL == "" and module.pytestmark is original_mark
    return probe, result, final_restored


def assert_scheduled_case(probe, result, final_restored):
    scenario = probe.scenario
    expected_boundary = {
        "success": [],
        "call_failure": ["test_call"],
        "scene_setup_failure": ["scene_setup"],
        "drop_failure": ["drop"],
    }[scenario]
    assert probe.boundaries == expected_boundary
    assert result == (0 if scenario == "success" else 1)
    first = [(0, "setup", "passed"), (0, "call", "passed"), (0, "teardown", "passed")]
    if scenario == "call_failure":
        first[1] = (0, "call", "failed")
    elif scenario == "scene_setup_failure":
        first = [(0, "setup", "failed"), (0, "teardown", "passed")]
    elif scenario == "drop_failure":
        first[2] = (0, "teardown", "failed")
    assert probe.reports == first + [
        (1, "setup", "passed"),
        (1, "call", "passed"),
        (1, "teardown", "passed"),
    ]
    scene_start = ["scene_setup", "scene_setup_closed", "engine"]
    scene_end = ["dispose", "scene_cleanup", "scene_cleanup_closed"]
    pdf_events = scene_start + (
        [] if scenario == "scene_setup_failure" else ["pdf_call"] + scene_end
    )
    assert probe.events == (
        ["create", "migration"]
        + pdf_events
        + ["drop", "restored"]
        + scene_start
        + ["non_pdf_call"]
        + scene_end
        + ["restored"]
    )
    assert probe.restored == [True, True] and final_restored
    assert probe.drop_state == [True]


@pytest.mark.parametrize("mode", ["importlib", "prepend"])
@pytest.mark.parametrize(
    "scenario", ["success", "call_failure", "scene_setup_failure", "drop_failure"]
)
def test_historical_pdf_database_scheduler(mode, scenario, tmp_path, monkeypatch, record_property):
    denied = []

    def deny(*args, **kwargs):
        denied.append(True)
        raise AssertionError("real I/O forbidden")

    for owner, names in (
        (psycopg, ("connect",)),
        (psycopg.Connection, ("connect",)),
        (psycopg.AsyncConnection, ("connect",)),
        (sqlalchemy, ("create_engine",)),
        (
            socket,
            ("socket", "create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"),
        ),
        (subprocess, ("Popen", "run")),
        (os, ("system", "popen")),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, deny)

    results = []
    with isolated_worker_imports():
        fresh = tmp_path / "fresh"
        fresh.mkdir()
        results.append(scheduled_case(mode, scenario, fresh))

    # Model native default collection without ever installing a DB URL in the
    # process environment. os.environ here is a PRIVATE dict, not os._Environ;
    # removing/restoring its synthetic values cannot call putenv or enable skips.
    ingestion = importlib.import_module("backend.tests.ingestion")
    private_environment = {
        "NOTEBOOK_TEST_POSTGRES_URL": BASE,
        "NOTEBOOK_MIGRATION_DATABASE_URL": BASE,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    cached = ModuleType("backend.tests.ingestion.test_worker")
    cached_mark = pytest.mark.skipif(False, reason="Synthetic cached collection")
    vars(cached).update(URL=BASE, pytestmark=cached_mark)
    with pytest.MonkeyPatch.context() as incoming:
        incoming.setattr(os, "environ", private_environment)
        incoming.setitem(sys.modules, cached.__name__, cached)
        incoming.setitem(sys.modules, "test_worker", cached)
        incoming.setattr(ingestion, "test_worker", cached, raising=False)
        with isolated_worker_imports():
            cached_path = tmp_path / "cached"
            cached_path.mkdir()
            results.append(scheduled_case(mode, scenario, cached_path))
        assert os.environ is private_environment
        assert private_environment["NOTEBOOK_TEST_POSTGRES_URL"] == BASE
        assert private_environment["NOTEBOOK_MIGRATION_DATABASE_URL"] == BASE
        assert vars(cached) == {
            "__name__": cached.__name__,
            "__doc__": None,
            "__package__": None,
            "__loader__": None,
            "__spec__": None,
            "URL": BASE,
            "pytestmark": cached_mark,
        }
    record_property(
        "closed_fixture_outcomes",
        {
            "runs": [
                {
                    "phases": probe.reports,
                    "boundaries": probe.boundaries,
                    "restored": probe.restored,
                }
                for probe, _, _ in results
            ],
            "denied_io": len(denied),
            "incoming_state_restored": True,
        },
    )
    assert not denied
    for result in results:
        assert_scheduled_case(*result)
