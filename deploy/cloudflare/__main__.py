"""Deployment hook: inspect, dry-run, probe, activate, verify or rollback.

Run on the coordinated host. This does not provision secrets/start containers.
"""

import argparse
import fcntl
import http.client
import json
import os
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from .access import Access, access_inventory, save_state
from .probes import connector_probe, public_check
from .routing import PLACEHOLDER, Router, RoutingError, fingerprint, require, route_index

LOCK = Path("/run/lock/notebook-cloudflare-tunnel.lock")


def private_read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        require(
            stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            "private_file_permissions",
        )
        value = stream.read(65537)
    require(0 < len(value) <= 65536, "private_file_invalid")
    return value


@contextmanager
def writer_lock(path=LOCK):
    # A single fixed pathname for ALL writers of the shared tunnel on this host.
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and not info.st_mode & 0o077
            and info.st_nlink == 1,
            "lock_permissions",
        )
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)  # Never unlink: that would let a second inode bypass the lock.


class Cloudflare:
    def __init__(self, token_path):
        self.token = private_read(token_path).strip()
        require(self.token and not any(c.isspace() for c in self.token), "token_invalid")

    def __call__(self, path, method="GET", body=None):
        connection = http.client.HTTPSConnection("api.cloudflare.com", timeout=15)
        try:
            connection.request(
                method,
                "/client/v4" + path,
                body=None if body is None else json.dumps(body),
                headers={
                    "Authorization": "Bearer " + self.token,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            require(200 <= response.status < 300, "cloudflare_request_failed")
            raw = response.read(2097153)
            require(len(raw) <= 2097152, "cloudflare_response_limit")
            result = json.loads(raw)
            require(result.get("success") is True, "cloudflare_request_failed")
            require(
                result.get("result_info", {}).get("total_pages", 1) <= 1,
                "cloudflare_pagination_unexpected",
            )
            return result["result"]
        finally:
            connection.close()


def verify_with_retry(release, placeholder, access_domain=None):
    for attempt in range(6):
        try:
            public_check(release, placeholder, access_domain)
            return
        except Exception:  # noqa: BLE001 - sanitize all external failures at this boundary
            if attempt == 5:
                raise RoutingError("public_verification_failed") from None
            time.sleep(2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=(
            "inspect",
            "dry-run",
            "probe",
            "activate",
            "verify",
            "rollback",
            "access-prepare",
            "access-inspect",
        ),
    )
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--access-state", type=Path)
    parser.add_argument("--connector-pid", type=int)
    parser.add_argument("--expected-fingerprint")
    parser.add_argument(
        "--exclusive-writer",
        action="store_true",
        help="Operator has excluded all other tunnel writers for this operation",
    )
    parser.add_argument(
        "--release-approved",
        action="store_true",
        help="Main has approved this exact release for public activation",
    )
    parser.add_argument(
        "--invite-only",
        action="store_true",
        help="Publish the app login without Access; an invitation is still required in Notebook",
    )
    args = parser.parse_args(argv)
    try:
        settings = json.loads(private_read(args.settings))
        from .routing import validate_settings

        validate_settings(settings)
        if args.mode == "probe":
            connector_probe(settings["origin"], settings["release"], args.connector_pid)
            print("connector_probe_passed")
            return 0
        require(args.token_file is not None, "token_file_required")
        api = Cloudflare(args.token_file)
        router = Router(api, settings)
        access = None
        access_domain = None
        if "access" in settings:
            require(args.access_state is not None, "access_state_required")
            state = (
                json.loads(private_read(args.access_state)) if args.access_state.exists() else {}
            )
            access = Access(api, settings, state)
            access_domain = settings["access"]["team_domain"]
        if args.mode in ("access-prepare", "access-inspect"):
            if access is None:
                raise RoutingError("access_settings_required")
            if args.mode == "access-prepare":
                require(args.exclusive_writer, "exclusive_writer_required")
                with writer_lock():
                    snapshot, _ = router.inspect()
                    require(
                        snapshot["config"]["ingress"][route_index(snapshot["config"])]["service"]
                        == PLACEHOLDER,
                        "access_prepare_requires_placeholder",
                    )
                    access.prepare(lambda state: save_state(args.access_state, state))
            else:
                access.inspect()
            verify_with_retry(settings["release"], True, access_domain)
            print("access_protection_verified; authenticated_owner_flow_not_tested")
            return 0
        if access is not None and args.mode != "rollback":
            access.inspect()
        if args.mode in ("inspect", "dry-run", "verify"):
            snapshot, health = router.inspect()
            if args.mode == "verify":
                placeholder = (
                    snapshot["config"]["ingress"][route_index(snapshot["config"])]["service"]
                    == PLACEHOLDER
                )
                verify_with_retry(settings["release"], placeholder, access_domain)
            print(
                json.dumps(
                    {
                        "mode": args.mode,
                        "version": snapshot["version"],
                        "fingerprint": fingerprint(snapshot),
                        "tunnel_health": health,
                        "state": "placeholder"
                        if snapshot["config"]["ingress"][route_index(snapshot["config"])]["service"]
                        == PLACEHOLDER
                        else "origin",
                    }
                )
            )
            return 0
        require(args.exclusive_writer, "exclusive_writer_required")
        require(args.expected_fingerprint is not None, "reviewed_fingerprint_required")
        if args.mode == "activate":
            require(args.release_approved, "release_approval_required")
            require(access is not None or args.invite_only, "access_protection_required")
            require(not (access is not None and args.invite_only), "conflicting_publication_modes")
        with writer_lock():
            snapshot, _ = router.inspect()
            require(fingerprint(snapshot) == args.expected_fingerprint, "reviewed_snapshot_changed")
            if args.mode == "activate":
                if access is None:
                    access_inventory(api, settings["account_id"])
                else:
                    access.inspect()
                verify_with_retry(settings["release"], True, access_domain)
                router.activate(
                    lambda: connector_probe(
                        settings["origin"], settings["release"], args.connector_pid
                    ),
                    lambda placeholder: verify_with_retry(
                        settings["release"], placeholder, access_domain
                    ),
                )
            else:
                router.rollback()
                verify_with_retry(settings["release"], True, access_domain)
        print("routing_operation_verified")
        return 0
    except RoutingError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - sanitize all external failures at this boundary
        # API/OS/TLS errors may include secrets or full responses: never render them.
        print(
            "routing_operation_failed; inspect current state before retry or recovery",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
