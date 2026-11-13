"""Only the existing Notebook ingress service may change.

Cloudflare has no documented conditional configuration PUT. Callers must hold the
shared tunnel writer lock AND exclude dashboard/other writers for the operation.
Fresh reads detect observed drift; they are not a server-side compare-and-swap.
"""

import hashlib
import ipaddress
import json
import re
from copy import deepcopy
from urllib.parse import urlsplit

HOST = "note.yokoma.de"
TUNNEL = "4c7b0b51-fdb2-4890-83ec-a4ca3fe01cb4"
PLACEHOLDER = "http_status:503"


class RoutingError(Exception):
    """Fixed diagnostic only: never include upstream content."""


def require(condition, code):
    if not condition:
        raise RoutingError(code)


def origin_url(value):
    url = urlsplit(value)
    require(
        url.scheme == "http"
        and not url.username
        and not url.password
        and not url.query
        and not url.fragment
        and url.path == "",
        "origin_invalid",
    )
    address = ipaddress.ip_address(url.hostname)
    networks = (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "::1/128",
        "fc00::/7",
    )
    require(
        any(address in ipaddress.ip_network(net) for net in networks)
        and url.port is not None
        and 1024 <= url.port <= 65535,
        "origin_invalid",
    )
    return value


def validate_settings(settings):
    require(settings["hostname"] == HOST and settings["tunnel_id"] == TUNNEL, "ownership_invalid")
    for key in ("account_id", "zone_id", "dns_record_id"):
        require(re.fullmatch(r"[0-9a-f]{32}", settings[key]) is not None, "identity_invalid")
    origin_url(settings["origin"])
    require(re.fullmatch(r"[0-9a-f]{40}", settings["release"]) is not None, "release_invalid")


def fingerprint(snapshot):
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def hostname_matches(hostname):
    """Conservative overlap check; DNS names are case-insensitive.

    Treat '*' as spanning labels, including unusual wildcard placements. A false
    positive blocks an operator action rather than opening an unexpected route.
    """
    pattern = re.escape(hostname.lower().rstrip(".")).replace(r"\*", ".*")
    return re.fullmatch(pattern, HOST) is not None


def route_index(config):
    rules = config["ingress"]
    require(isinstance(rules, list) and len(rules) >= 2, "ingress_invalid")
    matches = [
        i for i, rule in enumerate(rules) if rule.get("hostname", "").lower().rstrip(".") == HOST
    ]
    require(len(matches) == 1 and matches[0] < len(rules) - 1, "route_missing_or_duplicate")
    index = matches[0]
    require(rules[-1] == {"service": "http_status:404"}, "fallback_changed")
    # Unknown path/origin overrides could route differently than the probe.
    require(
        set(rules[index]) == {"hostname", "service"} and rules[index]["hostname"] == HOST,
        "owned_route_overrides",
    )
    require(not config.get("originRequest"), "global_origin_overrides")
    for rule in rules[:index]:
        hostname = rule.get("hostname", "")
        require(
            hostname and not hostname_matches(hostname),
            "route_shadowed",
        )
    return index


class Router:
    def __init__(self, api, settings):
        validate_settings(settings)
        self.api, self.settings = api, settings
        self.base = f"/accounts/{settings['account_id']}/cfd_tunnel/{TUNNEL}"
        self.config_path = self.base + "/configurations"

    def inspect(self):
        s = self.settings
        zone = self.api(f"/zones/{s['zone_id']}")
        require(
            zone["id"] == s["zone_id"]
            and zone["name"] == "yokoma.de"
            and zone["status"] == "active"
            and zone["account"]["id"] == s["account_id"],
            "zone_identity_changed",
        )
        tunnel = self.api(self.base)
        require(
            tunnel["id"] == TUNNEL
            and tunnel["name"] == "mailcow-yokoma"
            and tunnel["config_src"] == "cloudflare"
            and not tunnel.get("deleted_at"),
            "tunnel_identity_changed",
        )
        records = self.api(f"/zones/{s['zone_id']}/dns_records?name={HOST}&per_page=100")
        require(len(records) == 1, "dns_conflict")
        record = records[0]
        require(
            record["id"] == s["dns_record_id"]
            and record["name"] == HOST
            and record["type"] == "CNAME"
            and record["proxied"] is True
            and record["content"] == TUNNEL + ".cfargotunnel.com",
            "dns_conflict",
        )
        snapshot = self.api(self.config_path)
        require(
            snapshot["tunnel_id"] == TUNNEL
            and snapshot["account_id"] == s["account_id"]
            and type(snapshot["version"]) is int,
            "configuration_identity_changed",
        )
        index = route_index(snapshot["config"])
        require(
            snapshot["config"]["ingress"][index]["service"] in (PLACEHOLDER, s["origin"]),
            "owned_service_conflict",
        )
        return snapshot, tunnel["status"]

    def replace(self, snapshot, desired):
        latest, _ = self.inspect()
        require(fingerprint(latest) == fingerprint(snapshot), "configuration_drift")
        updated = deepcopy(latest["config"])
        index = route_index(updated)
        if updated["ingress"][index]["service"] == desired:
            return False
        updated["ingress"][index]["service"] = desired
        # Do not send a purported version precondition: the API doesn't offer one.
        self.api(self.config_path, "PUT", {"config": updated})
        observed, _ = self.inspect()
        require(
            observed["config"] == updated and observed["version"] > latest["version"],
            "write_outcome_uncertain",
        )
        return True

    def rollback(self):
        # Fresh snapshot, never replay a saved entire-tunnel configuration.
        snapshot, _ = self.inspect()
        return self.replace(snapshot, PLACEHOLDER)

    def activate(self, probe, public_check):
        snapshot, health = self.inspect()
        require(health == "healthy", "tunnel_not_healthy")
        probe()
        # Even idempotent activation revalidates readiness and public behavior.
        self.replace(snapshot, self.settings["origin"])
        try:
            public_check(False)
        except Exception:  # noqa: BLE001 - sanitize all external failures at this boundary
            # No compensation for ambiguous PUTs above. Reinspect before any recovery.
            self.rollback()
            public_check(True)
            raise RoutingError("public_check_failed_rolled_back") from None
