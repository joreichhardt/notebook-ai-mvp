import asyncio
import socket
from unittest.mock import patch

import httpx
import pytest
from app.providers.config import Provider
from fastapi import HTTPException


def test_dns_rebinding_cannot_change_connected_address():
    from app.providers.transport import ControlledBackend

    p = Provider(id="cloud", label="Cloud", kind="openai", base_url="https://fixture.invalid")
    calls = []

    class Stream:
        def get_extra_info(self, name):
            return ("93.184.216.34", 443) if name == "peername" else None

    async def dial(address, port, timeout, socket_options):
        calls.append(address)
        return Stream()

    async def run():
        backend = ControlledBackend(p, dial=dial)
        with patch(
            "socket.getaddrinfo",
            side_effect=[
                [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
                [(2, 1, 6, "", ("127.0.0.1", 443))],
            ],
        ):
            await backend.connect_tcp("fixture.invalid", 443)
            with pytest.raises(HTTPException):
                await backend.connect_tcp("fixture.invalid", 443)

    asyncio.run(run())
    assert calls == ["93.184.216.34"]


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:8.8.8.8", "0.0.0.0", "224.0.0.1"],
)
def test_forbidden_cloud_addresses_never_dial(address):
    from app.providers.transport import ControlledBackend

    p = Provider(id="cloud", label="Cloud", kind="openai", base_url="https://fixture.invalid")

    async def dial(*args):
        pytest.fail("forbidden address reached dial")

    async def run():
        with (
            patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", (address, 443))]),
            pytest.raises(HTTPException),
        ):
            await ControlledBackend(p, dial=dial).connect_tcp("fixture.invalid", 443)

    asyncio.run(run())


def test_internal_permission_still_forbids_metadata():
    from app.providers.transport import ControlledBackend

    p = Provider(
        id="local",
        label="Local",
        kind="ollama",
        base_url="http://fixture.invalid",
        allow_internal=True,
    )

    async def run():
        with (
            patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("169.254.169.254", 80))]),
            pytest.raises(HTTPException),
        ):
            await ControlledBackend(p).connect_tcp("fixture.invalid", 80)

    asyncio.run(run())


def test_tls_keeps_original_hostname_and_rejects_disabled_verification():
    import ssl

    from app.providers.transport import ControlledBackend

    p = Provider(id="cloud", label="Cloud", kind="openai", base_url="https://fixture.invalid")
    names = []

    class Stream:
        def get_extra_info(self, name):
            return ("93.184.216.34", 443) if name == "peername" else None

        async def start_tls(self, ssl_context, server_hostname, timeout):
            assert ssl_context.check_hostname
            assert ssl_context.verify_mode == ssl.CERT_REQUIRED
            names.append(server_hostname)
            raise ssl.SSLCertVerificationError("synthetic mismatch")

        async def aclose(self):
            pass

    async def dial(address, *args):
        assert address == "93.184.216.34"
        return Stream()

    # httpcore owns the TLS upgrade and passes URL host, not the pinned IP.
    import httpcore

    async def run():
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            async with httpcore.AsyncConnectionPool(
                network_backend=ControlledBackend(p, dial=dial)
            ) as pool:
                with pytest.raises(ssl.SSLCertVerificationError):
                    await pool.request("GET", p.base_url)

    asyncio.run(run())
    assert names == ["fixture.invalid"]


def test_real_loopback_http_transport():
    from app.providers.transport import ControlledTransport

    async def run():
        async def handle(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        p = Provider(
            id="local",
            label="Local",
            kind="openai",
            base_url=f"http://127.0.0.1:{port}",
            allow_internal=True,
        )
        async with (
            server,
            httpx.AsyncClient(transport=ControlledTransport(p), trust_env=False) as client,
        ):
            assert (await client.get(p.base_url)).text == "ok"

    asyncio.run(run())
