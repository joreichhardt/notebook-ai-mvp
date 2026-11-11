"""Resolve, validate, connect the numeric address, verify peer; TLS uses original host.

No process-global DNS monkeypatch, proxy, redirects, retries or connection sharing.
httpcore's public network-backend seam leaves HTTP framing to its maintained stack.
"""

import asyncio
import ipaddress
import socket
import ssl
from typing import NoReturn
from urllib.parse import urlsplit

import httpcore
import httpx

from app.auth.security import fail


def denied() -> NoReturn:
    fail(503, "provider_configuration", "Anbieterziel ist nicht freigegeben.")
    raise AssertionError("unreachable")


def check_addresses(provider, addresses):
    if not addresses:
        denied()
    for address in addresses:
        ip = ipaddress.ip_address(address)
        # Even explicit internal endpoints may not reach metadata, multicast,
        # unspecified or IPv4-mapped IPv6 destinations.
        if (
            ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or getattr(ip, "ipv4_mapped", None)
            or (not provider.allow_internal and not ip.is_global)
        ):
            denied()


class NetworkStream(httpcore.AsyncNetworkStream):
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    async def read(self, max_bytes, timeout=None):
        async with asyncio.timeout(timeout):
            return await self.reader.read(max_bytes)

    async def write(self, buffer, timeout=None):
        async with asyncio.timeout(timeout):
            self.writer.write(buffer)
            await self.writer.drain()

    async def aclose(self):
        self.writer.close()
        # Do not block cancellation on a malicious peer's TLS close_notify.

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        if (
            not ssl_context.check_hostname
            or ssl_context.verify_mode != ssl.CERT_REQUIRED
            or not server_hostname
        ):
            denied()
        try:
            async with asyncio.timeout(timeout):
                await self.writer.start_tls(
                    ssl_context,
                    server_hostname=server_hostname,
                    ssl_handshake_timeout=timeout,
                )
            return self
        except BaseException:
            await self.aclose()
            raise

    def get_extra_info(self, info):
        if info == "is_readable":
            return self.reader.at_eof()
        return self.writer.get_extra_info(info)


async def dial_numeric(address, port, timeout, socket_options):
    ip = ipaddress.ip_address(address)
    sock = socket.socket(socket.AF_INET6 if ip.version == 6 else socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        for option in socket_options or ():
            sock.setsockopt(*option)
        async with asyncio.timeout(timeout):
            # Numeric address only: asyncio bypasses getaddrinfo here.
            await asyncio.get_running_loop().sock_connect(sock, (str(ip), port))
            reader, writer = await asyncio.open_connection(sock=sock)
        return NetworkStream(reader, writer)
    except BaseException:
        sock.close()
        raise


class ControlledBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, provider, *, dial=dial_numeric):
        self.provider, self.dial = provider, dial
        self.url = urlsplit(provider.base_url)

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        expected_port = self.url.port or (443 if self.url.scheme == "https" else 80)
        if (
            host != self.url.hostname
            or port != expected_port
            or local_address
            or (self.url.scheme != "https" and not self.provider.allow_internal)
        ):
            denied()
        async with asyncio.timeout(timeout):
            rows = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(row[4][0] for row in rows))
            check_addresses(self.provider, addresses)
            # No implicit destination fallback. A failed address fails the request.
            address = addresses[0]
            stream = await self.dial(address, port, timeout, socket_options)
            peer = stream.get_extra_info("peername")
            if not peer or ipaddress.ip_address(peer[0]) != ipaddress.ip_address(address):
                await stream.aclose()
                denied()
            return stream

    async def connect_unix_socket(self, *args, **kwargs) -> httpcore.AsyncNetworkStream:
        denied()

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        async for chunk in self.stream:
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class ControlledTransport(httpx.AsyncBaseTransport):
    def __init__(self, provider):
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=ControlledBackend(provider),
            retries=0,
            max_connections=1,
            max_keepalive_connections=0,
            http2=False,
        )

    async def handle_async_request(self, request):
        # Ignore caller extensions such as sni_hostname; TLS must verify URL host.
        core_request = httpcore.Request(
            method=request.method,
            url=str(request.url),
            headers=request.headers.raw,
            content=request.stream,
            extensions={"timeout": request.extensions.get("timeout", {})},
        )
        response = await self.pool.handle_async_request(core_request)
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=ResponseStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self):
        await self.pool.aclose()
