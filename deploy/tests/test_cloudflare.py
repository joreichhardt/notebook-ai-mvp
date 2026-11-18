"""Synthetic control-plane/HTTP tests; never contact Cloudflare or kube1."""

import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

from deploy.cloudflare import routing as r
from deploy.cloudflare.__main__ import main, private_read, writer_lock
from deploy.cloudflare.access import Access, save_state
from deploy.cloudflare.probes import BLOCKED, app_check, connector_probe, public_check, request

SETTINGS = {
    "hostname": r.HOST,
    "tunnel_id": r.TUNNEL,
    "account_id": "a" * 32,
    "zone_id": "b" * 32,
    "dns_record_id": "c" * 32,
    "origin": "http://10.20.30.40:18080",
    "release": "d" * 40,
}


class API:
    def __init__(self):
        self.writes = []
        self.snapshot = {
            "account_id": "a" * 32,
            "tunnel_id": r.TUNNEL,
            "version": 9,
            "config": {
                "warp-routing": {"enabled": True},
                "ingress": [
                    {
                        "hostname": "webmail.yokoma.de",
                        "service": "http://127.0.0.1:80",
                        "originRequest": {"httpHostHeader": "webmail.yokoma.de"},
                    },
                    {"hostname": r.HOST, "service": r.PLACEHOLDER},
                    {"service": "http_status:404"},
                ],
            },
        }
        self.records = [
            {
                "id": "c" * 32,
                "name": r.HOST,
                "type": "CNAME",
                "proxied": True,
                "content": r.TUNNEL + ".cfargotunnel.com",
            }
        ]
        self.health = "healthy"
        self.apps = []
        self.policies = []

    def __call__(self, path, method="GET", body=None):
        if method != "GET":
            self.writes.append((path, method, deepcopy(body)))
        if "/access/apps" in path:
            if method == "POST":
                assert body is not None
                app = {**body, "id": "11111111-1111-4111-8111-111111111111"}
                self.apps.append(app)
                self.policies = body["policies"]
                return deepcopy(app)
            if "/policies" in path:
                return deepcopy(self.policies)
            if path.endswith("?per_page=100"):
                return deepcopy(self.apps)
            return deepcopy(self.apps[0])
        if "/configurations" in path:
            if method == "PUT":
                assert body is not None
                self.snapshot["config"] = deepcopy(body["config"])
                self.snapshot["version"] += 1
            return deepcopy(self.snapshot)
        if "/dns_records?" in path:
            return deepcopy(self.records)
        if "/zones/" in path:
            return {
                "id": "b" * 32,
                "name": "yokoma.de",
                "status": "active",
                "account": {"id": "a" * 32},
            }
        return {
            "id": r.TUNNEL,
            "name": "mailcow-yokoma",
            "config_src": "cloudflare",
            "status": self.health,
        }


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.api = API()
        self.router = r.Router(self.api, SETTINGS)

    def test_activation_is_idempotent_and_preserves_all_foreign_configuration(self):
        before = deepcopy(self.api.snapshot["config"])
        calls = []
        for _ in range(2):
            self.router.activate(lambda: calls.append("probe"), lambda p: calls.append(p))
        self.assertEqual(len(self.api.writes), 1)
        expected = deepcopy(before)
        expected["ingress"][1]["service"] = SETTINGS["origin"]
        self.assertEqual(self.api.snapshot["config"], expected)
        self.assertEqual(calls, ["probe", False, "probe", False])

    def test_unready_origin_never_writes(self):
        with self.assertRaises(r.RoutingError):
            self.router.activate(lambda: r.require(False, "unready"), lambda _: None)
        self.assertEqual(self.api.writes, [])

    def test_unhealthy_tunnel_never_probes_or_writes(self):
        self.api.health = "down"
        with self.assertRaises(r.RoutingError):
            self.router.activate(lambda: self.fail("probe"), lambda _: None)
        self.assertEqual(self.api.writes, [])

    def test_observed_concurrent_update_aborts(self):
        snapshot, _ = self.router.inspect()
        self.api.snapshot["version"] += 1
        self.api.snapshot["config"]["ingress"][0]["service"] = "http://127.0.0.1:81"
        with self.assertRaisesRegex(r.RoutingError, "configuration_drift"):
            self.router.replace(snapshot, SETTINGS["origin"])
        self.assertEqual(self.api.writes, [])

    def test_readiness_time_drift_aborts(self):
        def probe():
            self.api.snapshot["version"] += 1

        with self.assertRaises(r.RoutingError):
            self.router.activate(probe, lambda _: None)
        self.assertEqual(self.api.writes, [])

    def test_public_failure_rolls_back_only_owned_service_preserving_new_foreign_state(self):
        def smoke(placeholder):
            if not placeholder:
                self.api.snapshot["config"]["new-field"] = {"preserve": True}
                self.api.snapshot["version"] += 1
                raise OSError("private upstream data")

        with self.assertRaisesRegex(r.RoutingError, "public_check_failed_rolled_back"):
            self.router.activate(lambda: None, smoke)
        self.assertEqual(self.api.snapshot["config"]["new-field"], {"preserve": True})
        self.assertEqual(self.api.snapshot["config"]["ingress"][1]["service"], r.PLACEHOLDER)
        self.assertEqual(len(self.api.writes), 2)
        self.router.rollback()
        self.assertEqual(len(self.api.writes), 2)

    def test_changed_owned_service_not_overwritten_by_rollback(self):
        self.api.snapshot["config"]["ingress"][1]["service"] = "http://10.0.0.2:8080"
        with self.assertRaisesRegex(r.RoutingError, "owned_service_conflict"):
            self.router.rollback()
        self.assertEqual(self.api.writes, [])

    def test_ambiguous_write_does_not_blindly_compensate(self):
        def ambiguous(path, method="GET", body=None):
            result = self.api(path, method, body)
            if method == "PUT":
                raise TimeoutError("secret")
            return result

        router = r.Router(ambiguous, SETTINGS)
        with self.assertRaises(TimeoutError):
            router.activate(lambda: None, lambda _: self.fail("smoke"))
        self.assertEqual(len(self.api.writes), 1)

    def test_identity_dns_and_ingress_conflicts_fail_closed(self):
        mutations = [
            lambda a: a.records.append(deepcopy(a.records[0])),
            lambda a: a.records[0].update(proxied=False),
            lambda a: a.records[0].update(id="e" * 32),
            lambda a: a.snapshot.update(tunnel_id="wrong"),
            lambda a: a.snapshot["config"]["ingress"].insert(
                0, {"hostname": "*.yokoma.de", "service": "http_status:404"}
            ),
            lambda a: a.snapshot["config"]["ingress"][1].update(path="/api"),
            lambda a: a.snapshot["config"]["ingress"].insert(
                1, deepcopy(a.snapshot["config"]["ingress"][1])
            ),
            lambda a: a.snapshot["config"].update(originRequest={"httpHostHeader": "wrong"}),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                api = API()
                mutate(api)
                with self.assertRaises(r.RoutingError):
                    r.Router(api, SETTINGS).rollback()
                self.assertEqual(api.writes, [])

    def test_dns_case_aliases_and_wildcards_never_shadow_owned_route(self):
        for hostname in ("NOTE.YOKOMA.DE", "note.yokoma.de.", "*.YOKOMA.DE", "note.*"):
            with self.subTest(hostname=hostname):
                api = API()
                api.snapshot["config"]["ingress"].insert(
                    0, {"hostname": hostname, "service": "http_status:200"}
                )
                with self.assertRaises(r.RoutingError):
                    r.Router(api, SETTINGS).rollback()
                self.assertEqual(api.writes, [])

    def test_only_explicit_internal_ip_origins(self):
        for origin in (
            "https://example.com",
            "http://169.254.169.254:8080",
            "http://10.0.0.1:80",
            "http://10.0.0.1:8080/admin",
            "http://8.8.8.8:8080",
            "http://proxy:8080",
            "http://10.0.0.1:8080?x=1",
        ):
            with self.subTest(origin=origin), self.assertRaises((r.RoutingError, ValueError)):
                r.origin_url(origin)
        self.assertEqual(r.origin_url("http://[fd00::1]:8080"), "http://[fd00::1]:8080")


class ProbeTests(unittest.TestCase):
    def fetch(self, origin, path, **kwargs):
        if path == "/api/health/ready":
            return (
                200,
                {"content-type": "application/json", "cache-control": "no-store"},
                json.dumps({"status": "ready", "release": SETTINGS["release"]}).encode(),
            )
        if path == "/":
            return 200, {"content-type": "text/html"}, b"<html></html>"
        if path == "/api/workspaces":
            return 401, {}, b""
        return 404, {}, b""

    def test_actual_readiness_frontend_and_admin_probe_matrix(self):
        calls = []

        def fetch(origin, path, **kw):
            calls.append(path)
            return self.fetch(origin, path, **kw)

        app_check(SETTINGS["origin"], SETTINGS["release"], fetch=fetch)
        self.assertEqual(calls, ["/api/health/ready", "/", "/api/workspaces", *BLOCKED])
        for bad in ("/api/health/ready", "/", "/api/workspaces", *BLOCKED):

            def bad_fetch(origin, path, bad=bad, **kw):
                return (200, {}, b"fallback") if path == bad else self.fetch(origin, path, **kw)

            with self.subTest(bad=bad), self.assertRaises(r.RoutingError):
                app_check(SETTINGS["origin"], SETTINGS["release"], fetch=bad_fetch)

    def test_malformed_readiness_has_fixed_failure(self):
        def fetch(*args, **kwargs):
            return 200, {"content-type": "application/json"}, b"PRIVATE_NOT_JSON"

        with self.assertRaisesRegex(r.RoutingError, "^readiness_failed$"):
            app_check(SETTINGS["origin"], SETTINGS["release"], fetch=fetch)

    def test_stale_release_not_ready(self):
        with self.assertRaises(r.RoutingError):
            app_check(SETTINGS["origin"], "e" * 40, fetch=self.fetch)

    def test_foreign_pid_never_executed(self):
        with patch("os.readlink", return_value="/usr/bin/other"), patch("subprocess.run") as run:
            with self.assertRaises(r.RoutingError):
                connector_probe(SETTINGS["origin"], SETTINGS["release"], 123)
            run.assert_not_called()

    def test_connector_uses_netns_and_scrubbed_environment(self):
        with (
            tempfile.NamedTemporaryFile() as ns,
            patch("os.readlink", return_value="/usr/bin/cloudflared"),
            patch("pathlib.Path.open", return_value=open(ns.name, "rb")),
            patch("subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            connector_probe(SETTINGS["origin"], SETTINGS["release"], 123)
            args, kwargs = run.call_args
            self.assertEqual(args[0][0], "nsenter")
            self.assertTrue(args[0][1].startswith("--net=/proc/self/fd/"))
            self.assertNotIn("BWS_ACCESS_TOKEN", kwargs["env"])
            self.assertEqual(set(kwargs["env"]), {"PATH", "LANG"})
            self.assertEqual(kwargs["timeout"], 60)
            self.assertTrue(kwargs["pass_fds"])


class PublicProbeTests(unittest.TestCase):
    def doh(self):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps(
            {"Status": 0, "Answer": [{"type": 1, "data": "104.16.1.1"}]}
        ).encode()
        return connection

    def test_dns_tls_placeholder_and_access_redirect_paths(self):
        for protected in (False, True):
            with (
                patch("socket.getaddrinfo", return_value=[object()]),
                patch("http.client.HTTPSConnection", return_value=self.doh()),
                patch("deploy.cloudflare.probes.request") as fetch,
            ):
                if protected:
                    fetch.return_value = (
                        302,
                        {
                            "location": "https://example.cloudflareaccess.com/cdn-cgi/access/login/test"
                        },
                        b"",
                    )
                else:
                    fetch.return_value = (503, {}, b"")
                public_check(
                    SETTINGS["release"], True, "example.cloudflareaccess.com" if protected else None
                )
                addresses = {call.kwargs["address"] for call in fetch.call_args_list}
                self.assertEqual(addresses, {None, "104.16.1.1"})
                if protected:
                    self.assertEqual(fetch.call_count, 2 * (2 + len(BLOCKED)))

    def test_ordinary_dns_failure_is_not_masked_by_doh(self):
        with (
            patch("socket.getaddrinfo", side_effect=OSError("failed")),
            patch("http.client.HTTPSConnection") as connect,
        ):
            with self.assertRaises(OSError):
                public_check(SETTINGS["release"], True)
            connect.assert_not_called()

    def test_wrong_access_redirect_or_public_content_denied(self):
        for status, location in (
            (200, ""),
            (302, "https://evil.test/cdn-cgi/access/login/x"),
            (302, "http://example.cloudflareaccess.com/cdn-cgi/access/login/x"),
            (302, "https://example.cloudflareaccess.com/elsewhere"),
        ):
            with (
                patch("socket.getaddrinfo", return_value=[object()]),
                patch("http.client.HTTPSConnection", return_value=self.doh()),
                patch(
                    "deploy.cloudflare.probes.request",
                    return_value=(status, {"location": location}, b""),
                ),
                self.assertRaises(r.RoutingError),
            ):
                public_check(SETTINGS["release"], True, "example.cloudflareaccess.com")

    def test_address_override_keeps_certificate_validation_and_sni(self):
        context = MagicMock()
        with (
            patch("ssl.create_default_context", return_value=context),
            patch("socket.create_connection") as connect,
            patch("http.client.HTTPSConnection") as factory,
        ):
            response = factory.return_value.getresponse.return_value
            response.status = 503
            response.read.return_value = b""
            response.getheaders.return_value = []
            self.assertEqual(request("https://" + r.HOST, "/", address="104.16.1.1")[0], 503)
            context.wrap_socket.assert_called_once_with(
                connect.return_value, server_hostname=r.HOST
            )
            self.assertEqual(factory.return_value.request.call_args.args[0], "GET")
            factory.return_value.close.assert_called_once()

    def test_tls_error_propagates(self):
        with patch("ssl.create_default_context") as context, patch("socket.create_connection"):
            context.return_value.wrap_socket.side_effect = OSError("bad certificate")
            with self.assertRaises(OSError):
                request("https://" + r.HOST, "/", address="104.16.1.1")


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.api = API()
        self.settings = {
            **SETTINGS,
            "access": {
                "email": "owner@example.test",
                "team_domain": "example.cloudflareaccess.com",
            },
        }
        self.access = Access(self.api, self.settings, {})

    def test_create_with_narrow_policy_then_idempotent(self):
        saved = []
        self.access.prepare(lambda state: saved.append(deepcopy(state)))
        self.access.prepare(lambda _: self.fail("save twice"))
        self.assertEqual(len(self.api.writes), 1)
        self.assertEqual(
            self.api.policies[0]["include"], [{"email": {"email": "owner@example.test"}}]
        )
        self.assertNotIn("email", saved[0])
        self.assertEqual(self.api.apps[0]["domain"], r.HOST)

    def test_no_adoption_or_replacement_of_existing_path_app(self):
        self.api.apps = [{"id": "foreign", "domain": r.HOST + "/api"}]
        with self.assertRaises(r.RoutingError):
            self.access.prepare(lambda _: self.fail("save"))
        self.assertEqual(self.api.writes, [])

    def test_overlapping_domain_variants_fail_closed(self):
        for domain in ("NOTE.YOKOMA.DE/api", "note.yokoma.de.", "note.*", "*.yokoma.de"):
            with self.subTest(domain=domain):
                self.api.apps = [{"id": "foreign", "domain": domain}]
                with self.assertRaises(r.RoutingError):
                    self.access.prepare(lambda _: self.fail("save"))
                self.assertEqual(self.api.writes, [])

    def test_ambiguous_access_creation_is_not_retried_or_adopted(self):
        def ambiguous(path, method="GET", body=None):
            result = self.api(path, method, body)
            if method == "POST":
                raise TimeoutError("private upstream data")
            return result

        access = Access(ambiguous, self.settings, {})
        with self.assertRaises(TimeoutError):
            access.prepare(lambda _: self.fail("save"))
        with self.assertRaisesRegex(r.RoutingError, "access_application_conflict"):
            access.prepare(lambda _: self.fail("save"))
        self.assertEqual(len(self.api.writes), 1)

    def test_bypass_or_extra_email_policy_rejected(self):
        self.access.prepare(lambda _: None)
        for change in (
            {"decision": "bypass"},
            {"include": [{"everyone": {}}]},
            {"include": [{"email": {"email": "other@example.test"}}]},
        ):
            original = deepcopy(self.api.policies)
            self.api.policies[0].update(change)
            with self.assertRaises(r.RoutingError):
                self.access.inspect()
            self.api.policies = original
        self.api.policies.append(deepcopy(self.api.policies[0]))
        with self.assertRaises(r.RoutingError):
            self.access.inspect()

    def test_scope_expansion_and_preflight_bypass_rejected(self):
        self.access.prepare(lambda _: None)
        for change in (
            {"options_preflight_bypass": True},
            {"destinations": [{"type": "public", "uri": "*.yokoma.de"}]},
        ):
            original = deepcopy(self.api.apps)
            self.api.apps[0].update(change)
            with self.assertRaises(r.RoutingError):
                self.access.inspect()
            self.api.apps = original


class LocalSafetyTests(unittest.TestCase):
    def test_private_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text("synthetic-value")
            path.chmod(0o600)
            self.assertEqual(private_read(path), "synthetic-value")
            path.chmod(0o644)
            with self.assertRaises(r.RoutingError):
                private_read(path)
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                private_read(link)

    def test_lock_contention_and_retained_inode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock"
            with writer_lock(path):
                inode = path.stat().st_ino
                with self.assertRaises(BlockingIOError), writer_lock(path):
                    self.fail("second writer")
            with writer_lock(path):
                self.assertEqual(path.stat().st_ino, inode)

    def test_state_never_overwrites_and_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state"
            save_state(path, {"app_id": "test"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                save_state(path, {"app_id": "wrong"})
            self.assertEqual(json.loads(path.read_text()), {"app_id": "test"})

    def test_cli_dry_run_no_writes_and_no_upstream_error_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings"
            path.write_text(json.dumps(SETTINGS))
            path.chmod(0o600)
            api = API()
            with (
                patch("deploy.cloudflare.__main__.Cloudflare", return_value=api),
                patch("sys.stdout", new_callable=io.StringIO) as output,
            ):
                self.assertEqual(
                    main(["dry-run", "--settings", str(path), "--token-file", "unused"]), 0
                )
                self.assertNotIn(SETTINGS["origin"], output.getvalue())
            self.assertEqual(api.writes, [])
            with (
                patch(
                    "deploy.cloudflare.__main__.Cloudflare",
                    side_effect=ValueError("SECRET_SENTINEL"),
                ),
                patch("sys.stderr", new_callable=io.StringIO) as output,
            ):
                self.assertEqual(
                    main(["inspect", "--settings", str(path), "--token-file", "unused"]), 1
                )
                self.assertNotIn("SECRET_SENTINEL", output.getvalue())

    def test_cli_activate_reactivate_and_rollback_with_access(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = {
                **SETTINGS,
                "access": {
                    "email": "owner@example.test",
                    "team_domain": "example.cloudflareaccess.com",
                },
            }
            api = API()
            state = {}
            Access(api, settings, state).prepare(lambda _: None)
            api.writes.clear()
            config = Path(directory) / "settings"
            config.write_text(json.dumps(settings))
            config.chmod(0o600)
            state_path = Path(directory) / "state"
            save_state(state_path, state)
            events = []
            with (
                patch("deploy.cloudflare.__main__.Cloudflare", return_value=api),
                patch("deploy.cloudflare.__main__.writer_lock") as lock,
                patch(
                    "deploy.cloudflare.__main__.connector_probe",
                    side_effect=lambda *args: events.append("ready"),
                ),
                patch(
                    "deploy.cloudflare.__main__.verify_with_retry",
                    side_effect=lambda *args: events.append("public"),
                ),
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                for mode in ("activate", "activate", "rollback", "rollback"):
                    self.assertEqual(
                        main(
                            [
                                mode,
                                "--settings",
                                str(config),
                                "--token-file",
                                "unused",
                                "--access-state",
                                str(state_path),
                                "--connector-pid",
                                "123",
                                "--exclusive-writer",
                                "--release-approved",
                                "--expected-fingerprint",
                                r.fingerprint(api.snapshot),
                            ]
                        ),
                        0,
                    )
                self.assertEqual(lock.call_count, 4)
            self.assertEqual(len(api.writes), 2)
            self.assertEqual(events.count("ready"), 2)
            self.assertEqual(api.snapshot["config"]["ingress"][1]["service"], r.PLACEHOLDER)

    def test_cli_invite_only_activates_without_access_but_refuses_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings"
            path.write_text(json.dumps(SETTINGS))
            path.chmod(0o600)
            for overlap in (False, True):
                api = API()

                def request(route, *args, overlap=overlap, api=api, **kwargs):
                    if "/access/apps?" in route:
                        return [{"id": "foreign", "domain": "note.yokoma.de"}] if overlap else []
                    return api(route, *args, **kwargs)

                with (
                    patch("deploy.cloudflare.__main__.Cloudflare", return_value=request),
                    patch("deploy.cloudflare.__main__.writer_lock"),
                    patch("deploy.cloudflare.__main__.connector_probe") as ready,
                    patch("deploy.cloudflare.__main__.verify_with_retry"),
                    patch("sys.stdout", new_callable=io.StringIO),
                    patch("sys.stderr", new_callable=io.StringIO),
                ):
                    result = main(
                        [
                            "activate",
                            "--settings",
                            str(path),
                            "--token-file",
                            "unused",
                            "--invite-only",
                            "--exclusive-writer",
                            "--release-approved",
                            "--connector-pid",
                            "123",
                            "--expected-fingerprint",
                            r.fingerprint(api.snapshot),
                        ]
                    )
                    self.assertEqual(result, int(overlap))
                    self.assertEqual(ready.call_count, int(not overlap))
                    self.assertEqual(len(api.writes), int(not overlap))

    def test_cli_refuses_writes_without_required_operating_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings"
            path.write_text(json.dumps(SETTINGS))
            path.chmod(0o600)
            api = API()
            with (
                patch("deploy.cloudflare.__main__.Cloudflare", return_value=api),
                patch("sys.stderr", new_callable=io.StringIO),
            ):
                for flags in (
                    [],
                    ["--exclusive-writer"],
                    ["--exclusive-writer", "--expected-fingerprint", "bad"],
                ):
                    self.assertEqual(
                        main(
                            ["activate", "--settings", str(path), "--token-file", "unused", *flags]
                        ),
                        1,
                    )
            self.assertEqual(api.writes, [])


if __name__ == "__main__":
    unittest.main()
