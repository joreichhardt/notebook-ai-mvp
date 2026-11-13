"""Hostile error-object regressions, synthetic only; assertions never print raw errors."""

import asyncio
from uuid import uuid4

import pytest
from app.connections.execution import ExecutionService
from fastapi import HTTPException

CANARY = "SYNTHETIC_REVIEW_CANARY"
UNKNOWN = {
    "code": "discovery_unavailable",
    "message": "Modellabfrage derzeit nicht verfügbar.",
}
SAFE = {
    "connection_inactive": (409, "Verbindung wurde geändert oder getrennt."),
    "admission_required": (403, "Eine angenommene Einladung ist erforderlich."),
    "unsafe_database_role": (503, "Datenbankrolle nicht freigegeben."),
    "model_unavailable": (400, "Bitte ein verfügbares Modell auswählen."),
}


class UnexpectedCode(str):
    def __hash__(self):
        return hash("connection_inactive")

    def __eq__(self, other):
        return True


class HashFailure(str):
    def __hash__(self):
        raise RuntimeError(CANARY)


class EqualityFailure(str):
    def __hash__(self):
        return hash("connection_inactive")

    def __eq__(self, other):
        raise RuntimeError(CANARY)


class StringFailure(str):
    def __str__(self):
        raise RuntimeError(CANARY)


class DictSubclass(dict):
    pass


class GetterFailure(dict):
    def get(self, *args):
        raise RuntimeError(CANARY)


class DetailFailure(HTTPException):
    def __getattribute__(self, name):
        if name == "detail":
            raise RuntimeError(CANARY)
        return super().__getattribute__(name)


class DetailCancellation(HTTPException):
    def __getattribute__(self, name):
        if name == "detail":
            raise asyncio.CancelledError
        return super().__getattribute__(name)


def hostile_error(case):
    if case == "detail-getter":
        return DetailFailure(503, CANARY)
    detail = {
        "code-subclass": {"code": UnexpectedCode(CANARY)},
        "hash-failure": {"code": HashFailure(CANARY)},
        "equality-failure": {"code": EqualityFailure(CANARY)},
        "str-failure": {"code": StringFailure("connection_inactive")},
        "dict-subclass": DictSubclass(code="connection_inactive"),
        "dict-getter": GetterFailure(code="connection_inactive"),
        "nonhashable-list": {"code": [CANARY]},
        "nonhashable-dict": {"code": {"private": CANARY}},
        "unknown-code": {"code": CANARY},
        "no-code": {"message": CANARY},
        "string-detail": CANARY,
        "string-subclass-detail": StringFailure(CANARY),
        "list-detail": [CANARY],
        "null-detail": None,
    }[case]
    return HTTPException(503, detail)


async def observe(error):
    async def lookup(*args):
        raise error

    service = ExecutionService(lookup=lookup)
    try:
        await service.discover(actor_id=uuid4(), connection_id=uuid4(), publish=None)
    except asyncio.CancelledError:
        return "cancelled", None
    except HTTPException as result:
        # Do not stringify an exception, detail or code, even on regression.
        try:
            detail = result.detail
            if type(detail) is not dict:
                return "unsafe-detail", None
            code = dict.get(detail, "code")
            message = dict.get(detail, "message")
            if type(code) is not str or type(message) is not str:
                return "unsafe-field", None
            if CANARY in code or CANARY in message:
                return "canary-leak", None
            return result.status_code, detail
        except Exception:  # noqa: BLE001 — report only a fixed sentinel, never raw errors
            return "unsafe-getter", None
    except Exception:  # noqa: BLE001 — report only a fixed sentinel, never raw errors
        return "escaped-error", None
    return "unexpected-success", None


@pytest.mark.parametrize(
    "case",
    [
        "code-subclass",
        "hash-failure",
        "equality-failure",
        "str-failure",
        "dict-subclass",
        "dict-getter",
        "detail-getter",
        "nonhashable-list",
        "nonhashable-dict",
        "unknown-code",
        "no-code",
        "string-detail",
        "string-subclass-detail",
        "list-detail",
        "null-detail",
    ],
)
def test_hostile_error_objects_become_fixed_503(case):
    status, detail = asyncio.run(observe(hostile_error(case)))
    safe_result = status == 503 and detail == UNKNOWN
    assert safe_result, "hostile error did not become the fixed 503 response"


@pytest.mark.parametrize("code", list(SAFE))
def test_ordinary_safe_application_errors_preserve_fixed_status(code):
    status, detail = asyncio.run(observe(HTTPException(599, {"code": code, "message": CANARY})))
    expected_status, expected_message = SAFE[code]
    safe_result = status == expected_status and detail == {
        "code": code,
        "message": expected_message,
    }
    assert safe_result, "safe application error status/message changed"


@pytest.mark.parametrize("getter", [False, True])
def test_error_boundary_preserves_cancellation(getter):
    error = DetailCancellation(503, CANARY) if getter else asyncio.CancelledError()
    status, _ = asyncio.run(observe(error))
    assert status == "cancelled", "cancellation was swallowed or converted"
