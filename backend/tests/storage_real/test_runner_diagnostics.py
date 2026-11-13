"""Pure synthetic runner reporting tests; no DB, container, or network activity."""

import json

import pytest


def test_fixed_stage_type_state_and_failure_exit(capsys):
    from sqlalchemy.exc import ProgrammingError

    from backend.tests.storage_real.runner_diagnostics import run_reported

    class DriverError(Exception):
        sqlstate = "42704"

    def operation(trace):
        trace.enter("alembic_upgrade")
        raise ProgrammingError(
            "SECRET_SQL_CANARY",
            {"secret": "SECRET_PARAM_CANARY"},
            DriverError("SECRET_DRIVER_CANARY"),
        )

    assert run_reported(operation) == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out.startswith("PHASE_A_FAILURE=")
    assert json.loads(output.out.split("=", 1)[1]) == {
        "stage": "alembic_upgrade",
        "type": "sqlalchemy_programming",
        "sqlstate": "42704",
    }
    assert "CANARY" not in output.out


@pytest.mark.parametrize("attribute", ["sqlstate", "diag", "orig"])
@pytest.mark.parametrize("raised", [RuntimeError, SystemExit, KeyboardInterrupt])
def test_hostile_structured_getters_never_escape_or_echo(attribute, raised, capsys, tmp_path):
    from backend.tests.storage_real.runner_diagnostics import run_reported

    class HostileError(Exception):
        def __getattribute__(self, name):
            if name == attribute:
                raise raised("GETTER_CANARY")
            return super().__getattribute__(name)

        def __str__(self):
            raise AssertionError("STR_CANARY")

        def __repr__(self):
            raise AssertionError("REPR_CANARY")

    def operation(trace):
        trace.enter("actor_denials")
        raise HostileError("ARG_CANARY")

    assert run_reported(operation) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out.split("=", 1)[1]) == {
        "stage": "actor_denials",
        "type": "unknown",
        "sqlstate": "unknown",
    }
    assert "CANARY" not in captured.out
    assert len(captured.out) < 200
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "value",
    [
        "42501",
        "42703",
        "42704",
        "42P01",
        "42883",
        "57014",
        "ZZZZZ",
        "42501\nSTATE_CANARY",
        "x" * 1000000,
        42501,
        None,
    ],
)
def test_sqlstate_is_exactly_allowlisted_not_parsed(value, capsys):
    from backend.tests.storage_real.runner_diagnostics import run_reported

    class SyntheticError(Exception):
        sqlstate = value

    def operation(trace):
        trace.enter("helper_acl")
        raise SyntheticError("DRIVER_CANARY")

    assert run_reported(operation) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out.split("=", 1)[1])
    expected = (
        value
        if type(value) is str
        and value
        in (
            "42501",
            "42703",
            "42704",
            "42P01",
            "42883",
            "57014",
        )
        else "unknown"
    )
    assert result == {"stage": "helper_acl", "type": "unknown", "sqlstate": expected}
    assert len(captured.out) < 200 and "CANARY" not in captured.out
    assert captured.err == ""


def test_nested_diag_and_orig_cycle_are_bounded(capsys):
    from sqlalchemy.exc import ProgrammingError

    from backend.tests.storage_real.runner_diagnostics import run_reported

    class Diag:
        sqlstate = "42P01"
        message_primary = "DIAG_CANARY"

    class Driver(Exception):
        diag = Diag()

    def operation(trace):
        trace.enter("migration_verify")
        raise ProgrammingError("SQL_CANARY", {"password": "PARAM_CANARY"}, Driver("DRIVER_CANARY"))

    assert run_reported(operation) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out.split("=", 1)[1])["sqlstate"] == "42P01"
    assert "CANARY" not in captured.out and captured.err == ""

    class Cycle(Exception):
        @property
        def orig(self):
            return self

    def cyclic(trace):
        trace.enter("native_catalog")
        raise Cycle("CYCLE_CANARY")

    assert run_reported(cyclic) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out.split("=", 1)[1])["sqlstate"] == "unknown"
    assert "CANARY" not in captured.out and captured.err == ""


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_cleanup_stage_restores_original_failure_unless_cleanup_itself_fails(cleanup_fails, capsys):
    from backend.tests.storage_real.runner_diagnostics import run_reported

    def operation(trace):
        trace.enter("alembic_upgrade")
        try:
            raise ValueError("ORIGINAL_CANARY")
        finally:
            with trace.cleanup("migrator_disable"):
                if cleanup_fails:
                    raise OSError("CLEANUP_CANARY")

    assert run_reported(operation) == 1
    output = capsys.readouterr()
    assert json.loads(output.out.split("=", 1)[1]) == {
        "stage": "migrator_disable" if cleanup_fails else "alembic_upgrade",
        "type": "os" if cleanup_fails else "value",
        "sqlstate": "unknown",
    }
    assert "CANARY" not in output.out and output.err == ""


def test_success_and_explicit_native_exit_preserved(capsys):
    from backend.tests.storage_real.runner_diagnostics import run_reported

    assert run_reported(lambda trace: None) == 0
    assert capsys.readouterr() == ("PHASE_A_EXIT=0\n", "")

    def exit_native(trace):
        raise SystemExit(7)

    with pytest.raises(SystemExit) as captured:
        run_reported(exit_native)
    assert captured.value.code == 7
    assert capsys.readouterr() == ("", "")


def test_all_fixed_stages_and_unknown_stage_have_bounded_output():
    from backend.tests.storage_real.runner_diagnostics import RunnerTrace

    for stage in (
        "preflight",
        "bootstrap_credentials",
        "native_catalog",
        "upstream_probe",
        "migration_owner",
        "alembic_upgrade",
        "migrator_disable",
        "migration_verify",
        "runtime_logins",
        "app_catalog",
        "actor_denials",
        "helper_acl",
        "runtime_memberships",
        "storage_graph",
        "credential_cleanup",
        "complete",
    ):
        trace = RunnerTrace()
        trace.enter(stage)
        output = trace.failure(AssertionError("STAGE_CANARY"))
        assert json.loads(output) == {"stage": stage, "type": "assertion", "sqlstate": "unknown"}
        assert len(output) < 200 and "CANARY" not in output
    trace.enter("FORGED_STAGE_CANARY")
    assert json.loads(trace.failure(AssertionError()))["stage"] == "unknown"


def test_diag_getter_value_subclasses_and_unknown_fields_fail_closed(capsys):
    from backend.tests.storage_real.runner_diagnostics import run_reported

    class Diag:
        @property
        def sqlstate(self):
            raise SystemExit("DIAG_GETTER_CANARY")

    class Error(Exception):
        diag = Diag()

    def operation(trace):
        raise Error("DRIVER_CANARY")

    assert run_reported(operation) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out.split("=", 1)[1]) == {
        "stage": "preflight",
        "type": "unknown",
        "sqlstate": "unknown",
    }
    assert "CANARY" not in captured.out and captured.err == ""


def test_unknown_stage_and_string_subclasses_are_never_serialized(capsys):
    from backend.tests.storage_real.runner_diagnostics import run_reported

    class HostileString(str):
        def __eq__(self, other):
            raise AssertionError("EQUALITY_CANARY")

    class Error(Exception):
        sqlstate = HostileString("42501")

    def operation(trace):
        trace.enter(HostileString("alembic_upgrade"))
        raise Error("ERROR_CANARY")

    assert run_reported(operation) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out.split("=", 1)[1]) == {
        "stage": "unknown",
        "type": "unknown",
        "sqlstate": "unknown",
    }
    assert "CANARY" not in captured.out and captured.err == ""
