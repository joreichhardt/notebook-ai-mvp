"""Real loopback TLS with ephemeral synthetic certificate, never a provider call."""

import asyncio
import ssl
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
from app.providers.config import Provider
from app.providers.transport import ControlledTransport
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@pytest.mark.parametrize("valid_name", [True, False])
def test_loopback_tls_verifies_original_hostname(tmp_path, valid_name):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.touch(mode=0o600)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    client_context = ssl.create_default_context(cafile=str(cert_path))

    async def run():
        async def handle(reader, writer):
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                assert b"Host: localhost:" in request
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                await writer.drain()
            finally:
                writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_context)
        port = server.sockets[0].getsockname()[1]
        host = "localhost" if valid_name else "127.0.0.1"
        provider = Provider(
            id="tls",
            label="TLS fixture",
            kind="openai",
            base_url=f"https://{host}:{port}",
            allow_internal=True,
        )
        # Only trust our ephemeral CA; verification and SNI stay enabled.
        with patch(
            "app.providers.transport.ssl.create_default_context", return_value=client_context
        ):
            transport = ControlledTransport(provider)
        async with server, httpx.AsyncClient(transport=transport, trust_env=False) as client:
            # Deterministic IPv4 localhost, not host-specific IPv6 preference.
            with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", port))]):
                if valid_name:
                    assert (await client.get(provider.base_url)).text == "ok"
                else:
                    with pytest.raises(ssl.SSLCertVerificationError):
                        await client.get(provider.base_url)

    asyncio.run(run())
