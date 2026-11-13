"""Bounded HTTP checks; no redirects, environment proxies or response logging."""

import http.client
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .routing import HOST, RoutingError, origin_url, require

BLOCKED = (
    "/rest/v1/",
    "/storage/v1/",
    "/auth/v1/admin/users",
    "/studio/",
    "/pg/",
    "/api/docs",
    "/api/openapi.json",
)


def request(origin, path, *, address=None):
    url = urlsplit(origin)
    if url.scheme == "https":
        connection = http.client.HTTPSConnection(url.hostname, url.port or 443, timeout=5)
        if address is not None:
            connection.sock = ssl.create_default_context().wrap_socket(
                socket.create_connection((address, url.port or 443), timeout=5),
                server_hostname=url.hostname,
            )
    else:
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
    try:
        connection.request("GET", path, headers={"Host": HOST, "Cache-Control": "no-cache"})
        response = connection.getresponse()
        body = response.read(131073)
        require(len(body) <= 131072, "probe_body_limit")
        return response.status, {k.lower(): v for k, v in response.getheaders()}, body
    finally:
        connection.close()


def app_check(origin, release, *, address=None, fetch=request):
    status, headers, body = fetch(origin, "/api/health/ready", address=address)
    require(
        status == 200 and "application/json" in headers.get("content-type", ""), "readiness_failed"
    )
    try:
        readiness = json.loads(body)
    except (ValueError, UnicodeError):
        raise RoutingError("readiness_failed") from None
    require(readiness == {"status": "ready", "release": release}, "readiness_failed")
    require("no-store" in headers.get("cache-control", ""), "readiness_cacheable")
    status, headers, body = fetch(origin, "/", address=address)
    require(
        status == 200
        and "text/html" in headers.get("content-type", "")
        and b"<html" in body.lower(),
        "frontend_failed",
    )
    for path in BLOCKED:
        status, _, _ = fetch(origin, path, address=address)
        require(status in (403, 404), "admin_path_exposed")


def connector_probe(origin, release, pid):
    """Enter a verified cloudflared process's net namespace, never its environment.

    Operator must identify the PID belonging to the specified tunnel on kube1.
    IP-only origins avoid host/container resolver differences. No Docker mutation.
    """
    origin_url(origin)
    require(type(pid) is int and pid > 1, "connector_pid_invalid")
    process = Path("/proc") / str(pid)
    require(Path(os.readlink(process / "exe")).name == "cloudflared", "connector_process_invalid")
    with (process / "ns/net").open("rb") as namespace:
        descriptor = namespace.fileno()
        result = subprocess.run(
            [
                "nsenter",
                f"--net=/proc/self/fd/{descriptor}",
                sys.executable,
                "-m",
                "deploy.cloudflare.probes",
                origin,
                release,
            ],
            check=False,
            cwd=Path(__file__).resolve().parents[2],
            pass_fds=(descriptor,),
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    require(result.returncode == 0, "connector_probe_failed")


def public_check(release, placeholder=False, access_domain=None):
    # Require both ordinary resolver success and Cloudflare DoH; TLS retains SNI
    # and normal certificate verification for each DoH address.
    system = socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)
    require(bool(system), "system_dns_failed")
    connection = http.client.HTTPSConnection("cloudflare-dns.com", timeout=5)
    try:
        connection.request(
            "GET", f"/dns-query?name={HOST}&type=A", headers={"Accept": "application/dns-json"}
        )
        response = connection.getresponse()
        require(response.status == 200, "doh_failed")
        result = json.loads(response.read(65536))
    finally:
        connection.close()
    addresses = [x["data"] for x in result.get("Answer", []) if x.get("type") == 1]
    require(result.get("Status") == 0 and 0 < len(addresses) <= 16, "doh_failed")
    for address in addresses:
        require(ipaddress.ip_address(address).is_global, "dns_address_invalid")
    # Also exercise the system-resolved route, not only the DoH override.
    for address in [None, *addresses]:
        if access_domain is not None:
            for path in ("/", "/api/health/ready", *BLOCKED):
                status, headers, _ = request("https://" + HOST, path, address=address)
                location = urlsplit(headers.get("location", ""))
                require(
                    status == 302
                    and location.scheme == "https"
                    and location.hostname == access_domain
                    and location.port in (None, 443)
                    and not location.username
                    and location.path.startswith("/cdn-cgi/access/login/"),
                    "access_public_gate_failed",
                )
        elif placeholder:
            status, _, _ = request("https://" + HOST, "/", address=address)
            require(status == 503, "placeholder_smoke_failed")
        else:
            app_check("https://" + HOST, release, address=address)


if __name__ == "__main__":
    try:
        origin_url(sys.argv[1])
        app_check(sys.argv[1], sys.argv[2])
    except Exception:  # noqa: BLE001 - sanitize all external failures at this boundary
        raise SystemExit(1) from None
