"""Notebook-only Access application, with one exact email allow policy.

No shared service tokens, bypass policies, identity-provider edits or deletion.
"""

import json
import os
import re
import tempfile
from pathlib import Path
from uuid import UUID

from .routing import HOST, hostname_matches, require

NAME = "Notebook owner access"


def validate_access(settings):
    access = settings["access"]
    require(
        re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", access["email"]) is not None,
        "access_email_invalid",
    )
    require(
        re.fullmatch(r"[a-z0-9-]+\.cloudflareaccess\.com", access["team_domain"]) is not None,
        "access_team_invalid",
    )


def overlaps(domain):
    host = domain.split("/")[0].lower()
    return hostname_matches(host)


def access_inventory(api, account_id, *, owned=None):
    apps = api(f"/accounts/{account_id}/access/apps?per_page=100")
    for app in apps:
        domains = [app.get("domain", ""), *app.get("self_hosted_domains", [])]
        domains += [
            d.get("uri", "") for d in app.get("destinations", []) if d.get("type") == "public"
        ]
        if any(overlaps(domain) for domain in domains):
            require(owned is not None and app["id"] == owned, "access_application_conflict")
    return apps


class Access:
    def __init__(self, api, settings, state):
        validate_access(settings)
        self.api, self.settings, self.state = api, settings, state
        self.base = f"/accounts/{settings['account_id']}/access/apps"

    def inventory(self):
        return access_inventory(
            self.api, self.settings["account_id"], owned=self.state.get("app_id")
        )

    def inspect(self):
        require(
            self.state.get("account_id") == self.settings["account_id"]
            and self.state.get("hostname") == HOST,
            "access_state_identity",
        )
        app_id = str(UUID(self.state["app_id"]))
        self.inventory()
        app = self.api(self.base + "/" + app_id)
        require(
            app["id"] == app_id
            and app["domain"] == HOST
            and app["type"] == "self_hosted"
            and app["name"] == NAME
            and app.get("http_only_cookie_attribute") is True
            and not app.get("options_preflight_bypass")
            and not app.get("allow_authenticate_via_warp"),
            "access_application_changed",
        )
        require(
            all(domain == HOST for domain in app.get("self_hosted_domains", []))
            and all(
                d.get("type") == "public" and d.get("uri") == HOST
                for d in app.get("destinations", [])
            ),
            "access_scope_changed",
        )
        policies = self.api(self.base + "/" + app_id + "/policies?per_page=100")
        require(len(policies) == 1, "access_policy_conflict")
        policy = policies[0]
        require(
            policy["decision"] == "allow"
            and policy["include"] == [{"email": {"email": self.settings["access"]["email"]}}]
            and not policy.get("exclude")
            and not policy.get("require"),
            "access_policy_changed",
        )

    def prepare(self, save):
        self.inventory()
        if self.state:
            self.inspect()
            return False
        # Policy is included in creation: never create a public bypass interval.
        app = self.api(
            self.base,
            "POST",
            {
                "name": NAME,
                "domain": HOST,
                "type": "self_hosted",
                "session_duration": "1h",
                "http_only_cookie_attribute": True,
                "options_preflight_bypass": False,
                "allow_authenticate_via_warp": False,
                "policies": [
                    {
                        "name": NAME,
                        "decision": "allow",
                        "precedence": 1,
                        "include": [{"email": {"email": self.settings["access"]["email"]}}],
                    }
                ],
            },
        )
        self.state.update(
            app_id=str(UUID(app["id"])), account_id=self.settings["account_id"], hostname=HOST
        )
        save(self.state)
        self.inspect()
        return True


def save_state(path, state):
    parent = Path(path).parent
    info = parent.lstat()
    import stat

    require(
        stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
        "state_directory_permissions",
    )
    fd, temporary = tempfile.mkstemp(prefix=".access-", dir=parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        # Creation only; no overwrite of concurrently introduced state.
        os.link(temporary, path)
    finally:
        os.unlink(temporary)
