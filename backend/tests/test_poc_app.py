"""Login-only composition; synthetic probes are never runtime acceptance."""

import asyncio
import importlib
import subprocess
import sys

import httpx
import pytest
from app.auth.settings import Settings
from app.health import ProbeUnavailable
from pydantic import SecretStr


def poc():
    return importlib.import_module("app.poc")


def request(path, *, method="GET", **kwargs):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=poc().app), base_url="https://note.yokoma.de"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def test_default_closed_and_profile_not_a_ready_switch(monkeypatch):
    poc()
    monkeypatch.setenv("NOTEBOOK_POC_READY", "true")
    for path in ("/api/poc/ready?ready=true", "/api/health/ready"):
        assert request(path).status_code == 503
    caps = request("/api/capabilities").json()
    assert caps["documents"] is caps["chat"] is False
    assert caps["auth"]["registration"] is False
    assert (
        request("/api/notebooks/00000000-0000-4000-8000-000000000001/documents").status_code == 404
    )
    assert request("/api/connections").status_code == 404
    assert request("/auth/v1/admin/users").status_code == 404
    subprocess.run(
        [
            sys.executable,
            "-c",
            'import sys, app.poc; assert "app.workers.supervisor" not in sys.modules',
        ],
        check=True,
        timeout=10,
    )


def test_secret_configuration_does_not_activate_login_profile(monkeypatch):
    m = poc()
    monkeypatch.setattr(
        m,
        "get_settings",
        lambda: Settings(
            supabase_url="http://untrusted:9999", supabase_anon_key=SecretStr("fixture")
        ),
    )

    async def run():
        async with (
            m.app.router.lifespan_context(m.app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=m.app), base_url="https://note.yokoma.de"
            ) as client,
        ):
            assert (await client.get("/api/poc/ready")).status_code == 503

    asyncio.run(run())


def test_origin_validation_and_password_errors_remain_sanitized():
    response = request(
        "/api/auth/login",
        method="POST",
        json={"email": "user@example.invalid", "password": "never-echo-this"},
        headers={"origin": "https://foreign.invalid"},
    )
    assert response.status_code == 403
    assert "never-echo-this" not in response.text
    response = request(
        "/api/auth/login",
        method="POST",
        json={"email": "x", "password": {"secret": "never-echo-this"}},
    )
    assert response.status_code == 422
    assert "never-echo-this" not in response.text


@pytest.mark.parametrize("failure", ["database", "auth", "secrets", "exception", "cancelled"])
def test_fixed_runtime_checks_failure_and_joins(monkeypatch, failure):
    m = poc()
    completed = []

    def probe(name):
        async def check():
            try:
                await asyncio.sleep(0)
                if name == failure:
                    return False
                if name == "auth" and failure == "exception":
                    raise ProbeUnavailable()
                if name == "auth" and failure == "cancelled":
                    raise asyncio.CancelledError()
                return True
            finally:
                completed.append(name)

        return check

    for name in ("database", "auth", "secret_files"):
        monkeypatch.setattr(
            m,
            name + "_probe",
            lambda *a, name=name, **kw: probe("secrets" if name == "secret_files" else name),
        )
    runtime = m._Runtime(valid_settings())
    assert asyncio.run(runtime.check()) is False
    assert set(completed) == {"database", "auth", "secrets"}


def valid_settings():
    return Settings(
        public_origin="https://note.yokoma.de",
        supabase_url="http://frontend:8081",
        supabase_anon_key=SecretStr("fixture"),
        database_url=SecretStr("postgresql://notebook_app:fixture@db:5432/postgres"),
        database_url_file="/run/secrets/notebook_database_url",
        supabase_anon_key_file="/run/secrets/notebook_supabase_anon_key",
    )


def test_lifespan_installs_only_real_factories_current_check_and_stamp(monkeypatch):
    m = poc()
    state = {"healthy": True, "calls": 0}

    async def check():
        state["calls"] += 1
        return state["healthy"]

    for name in ("database", "auth", "secret_files"):
        monkeypatch.setattr(m, name + "_probe", lambda *a, **kw: check)
    monkeypatch.setattr(m, "get_settings", valid_settings)
    monkeypatch.setattr(m, "image_release", lambda: "a" * 40)

    async def run():
        async with (
            m.app.router.lifespan_context(m.app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=m.app), base_url="https://note.yokoma.de"
            ) as client,
        ):
            r = await client.get("/api/poc/ready")
            assert r.status_code == 200
            assert r.json() == {
                "status": "ready",
                "profile": "login-workspace-v1",
                "release": "a" * 40,
                "documents": False,
                "chat": False,
            }
            assert (await client.get("/api/health/ready")).status_code == 503
            state["healthy"] = False
            assert (await client.get("/api/poc/ready")).status_code == 503
            state["healthy"] = True
            monkeypatch.setattr(m, "image_release", lambda: None)
            assert (await client.get("/api/poc/ready")).status_code == 503
        assert m._runtime is None

    asyncio.run(run())
    assert state["calls"] == 6
