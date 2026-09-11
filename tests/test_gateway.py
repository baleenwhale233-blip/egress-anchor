from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import unittest

import yaml

from egress_anchor.compiler import inspect_subscription
from egress_anchor.gateway import FetchResult, GatewayError, GatewayService


FIXTURES = Path(__file__).parent / "fixtures"


class QueueFetcher:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, *, etag=None, last_modified=None):
        self.calls.append(
            {"url": url, "etag": etag, "last_modified": last_modified}
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class GatewayServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name) / "dev-data"
        self.source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")

    def service(self, *responses):
        fetcher = QueueFetcher(*responses)
        return GatewayService(self.data_dir, fetcher=fetcher), fetcher

    def test_initial_state_is_private_and_has_no_subscription_output(self):
        service, _ = self.service()

        state = service.public_state()

        self.assertEqual([], state["sources"])
        self.assertEqual(None, state["selected_source_id"])
        self.assertEqual(None, state["active_source_id"])
        self.assertFalse(state["subscription"]["available"])
        self.assertGreaterEqual(len(state["subscription"]["token"]), 32)
        self.assertEqual([], state["landings"])
        self.assertEqual([], state["catalog"]["groups"])
        self.assertEqual(0o700, self.data_dir.stat().st_mode & 0o777)
        self.assertEqual(0o600, (self.data_dir / "state.json").stat().st_mode & 0o777)

    def test_v1_state_migrates_to_a_url_free_source_snapshot(self):
        self.data_dir.mkdir(parents=True)
        legacy_url = (
            "https://legacy.example.invalid/import?token=fixture-legacy-secret"
        )
        legacy_state = {
            "version": 1,
            "token": "fixture-local-token-012345678901234567890123456789",
            "upstream": {
                "url": legacy_url,
                "display_url": "https://legacy.example.invalid/import?…",
                "etag": None,
                "last_modified": None,
            },
            "landings": [],
            "bindings": [],
            "catalog": inspect_subscription(self.source),
            "active_generation": None,
            "active_report": None,
            "source_updated_at": 1787068800,
            "last_success_at": None,
            "last_refresh_at": 1787068800,
            "last_error": None,
        }
        (self.data_dir / "state.json").write_text(
            json.dumps(legacy_state), encoding="utf-8"
        )
        (self.data_dir / "source.yaml").write_text(
            self.source, encoding="utf-8"
        )

        service = GatewayService(self.data_dir, fetcher=QueueFetcher())
        state = service.public_state()
        stored_state = (self.data_dir / "state.json").read_text(encoding="utf-8")

        self.assertEqual(1, len(state["sources"]))
        self.assertEqual(state["sources"][0]["id"], state["selected_source_id"])
        self.assertEqual(None, state["active_source_id"])
        self.assertNotIn("fixture-legacy-secret", stored_state)
        self.assertEqual(2, json.loads(stored_state)["version"])
        snapshot = (
            self.data_dir
            / "sources"
            / state["sources"][0]["id"]
            / "source.yaml"
        )
        self.assertEqual(self.source, snapshot.read_text(encoding="utf-8"))

    def test_import_source_persists_a_snapshot_without_persisting_the_url(self):
        service, fetcher = self.service(FetchResult(self.source))
        import_url = (
            "https://subscription.example.invalid/full.yaml?token=fixture-secret"
        )

        imported = service.import_source("Fixture source", import_url)
        state = service.public_state()
        stored_state = (self.data_dir / "state.json").read_text(encoding="utf-8")
        snapshot = (
            self.data_dir / "sources" / imported["id"] / "source.yaml"
        )

        self.assertEqual("Fixture source", imported["name"])
        self.assertEqual(imported["id"], state["selected_source_id"])
        self.assertEqual(None, state["active_source_id"])
        self.assertEqual([imported], state["sources"])
        self.assertEqual(
            ["AI", "Default"], [group["name"] for group in state["catalog"]["groups"]]
        )
        self.assertFalse(state["subscription"]["available"])
        self.assertNotIn(import_url, repr(state))
        self.assertNotIn("fixture-secret", stored_state)
        self.assertEqual(self.source, snapshot.read_text(encoding="utf-8"))
        self.assertEqual(0o600, snapshot.stat().st_mode & 0o777)
        self.assertEqual(import_url, fetcher.calls[0]["url"])

    def test_duplicate_source_name_is_rejected_before_fetching(self):
        service, fetcher = self.service(FetchResult(self.source))
        service.import_source(
            "Primary", "https://primary.example.invalid/import?token=fixture-a"
        )

        with self.assertRaises(GatewayError) as raised:
            service.import_source(
                "Primary", "https://other.example.invalid/import?token=fixture-b"
            )

        self.assertEqual("SOURCE_NAME_CONFLICT", raised.exception.code)
        self.assertEqual(1, len(fetcher.calls))
        self.assertEqual(1, len(service.public_state()["sources"]))

    def test_source_limit_is_rejected_before_fetching(self):
        service, fetcher = self.service(
            *(FetchResult(self.source) for _ in range(20))
        )
        for index in range(20):
            service.import_source(
                f"Source {index + 1}",
                f"https://source-{index + 1}.example.invalid/import?token=fixture",
            )

        with self.assertRaises(GatewayError) as raised:
            service.import_source(
                "Source 21", "https://source-21.example.invalid/import?token=fixture"
            )

        self.assertEqual("SOURCE_LIMIT_REACHED", raised.exception.code)
        self.assertEqual(20, len(fetcher.calls))
        self.assertEqual(20, len(service.public_state()["sources"]))

    def test_selecting_a_saved_source_restores_its_own_catalog(self):
        second_source = (FIXTURES / "update_after.yaml").read_text(encoding="utf-8")
        service, _ = self.service(
            FetchResult(self.source),
            FetchResult(second_source),
        )
        first = service.import_source(
            "Primary", "https://primary.example.invalid/import?token=fixture-a"
        )
        second = service.import_source(
            "Backup", "https://backup.example.invalid/import?token=fixture-b"
        )

        selected = service.select_source(first["id"])
        state = service.public_state()

        self.assertEqual(first["id"], selected["id"])
        self.assertEqual(first["id"], state["selected_source_id"])
        self.assertEqual(None, state["active_source_id"])
        self.assertEqual(2, len(state["sources"]))
        self.assertEqual(
            ["AI", "Default"], [group["name"] for group in state["catalog"]["groups"]]
        )
        self.assertFalse(
            next(item for item in state["sources"] if item["id"] == second["id"])[
                "selected"
            ]
        )

    def test_renaming_a_source_only_changes_its_display_name(self):
        service, _ = self.service(FetchResult(self.source))
        imported = service.import_source(
            "Original name", "https://primary.example.invalid/import?token=fixture"
        )
        landing = service.upsert_landing(
            {
                "name": "Fixture landing",
                "type": "http",
                "server": "landing.example.invalid",
                "port": 8080,
            }
        )
        service.update_bindings(
            [
                {
                    "group": "AI",
                    "enabled": True,
                    "landing_id": landing["id"],
                    "front_hop": {"mode": "direct"},
                }
            ]
        )
        before = service.public_state()
        compiled_before = service.read_subscription(before["subscription"]["token"])

        renamed = service.rename_source(imported["id"], "Renamed source")
        after = service.public_state()

        self.assertEqual("Renamed source", renamed["name"])
        self.assertEqual(imported["id"], after["selected_source_id"])
        self.assertEqual(imported["id"], after["active_source_id"])
        self.assertEqual(
            before["subscription"]["generation"],
            after["subscription"]["generation"],
        )
        self.assertEqual(
            compiled_before,
            service.read_subscription(after["subscription"]["token"]),
        )

    def test_saving_bindings_compiles_and_activates_the_selected_snapshot(self):
        service, _ = self.service(FetchResult(self.source))
        imported = service.import_source(
            "Primary", "https://primary.example.invalid/import?token=fixture"
        )
        landing = service.upsert_landing(
            {
                "name": "Fixture landing",
                "type": "socks5",
                "server": "landing.example.invalid",
                "port": 1080,
            }
        )

        result = service.update_bindings(
            [
                {
                    "group": "AI",
                    "enabled": True,
                    "landing_id": landing["id"],
                    "front_hop": {"mode": "source"},
                }
            ]
        )
        state = service.public_state()
        compiled = service.read_subscription(state["subscription"]["token"])

        self.assertTrue(result["compiled"])
        self.assertEqual(imported["id"], state["active_source_id"])
        self.assertTrue(state["subscription"]["available"])
        self.assertEqual(1, state["sources"][0]["managed_group_count"])
        self.assertIn("landing.example.invalid", compiled)

    def test_activate_source_switches_between_saved_snapshots(self):
        second_source = (FIXTURES / "update_after.yaml").read_text(encoding="utf-8")
        service, _ = self.service(
            FetchResult(self.source),
            FetchResult(second_source),
        )
        first = service.import_source(
            "Primary", "https://primary.example.invalid/import?token=fixture-a"
        )
        landing = service.upsert_landing(
            {
                "name": "Fixture landing",
                "type": "http",
                "server": "landing.example.invalid",
                "port": 8080,
            }
        )
        binding = [
            {
                "group": "AI",
                "enabled": True,
                "landing_id": landing["id"],
                "front_hop": {"mode": "source"},
            }
        ]
        service.update_bindings(binding)
        second = service.import_source(
            "Backup", "https://backup.example.invalid/import?token=fixture-b"
        )
        self.assertEqual(
            first["id"], service.public_state()["active_source_id"]
        )
        service.update_bindings(binding)
        self.assertEqual(
            second["id"], service.public_state()["active_source_id"]
        )

        result = service.activate_source(first["id"])
        state = service.public_state()
        compiled = service.read_subscription(state["subscription"]["token"])

        self.assertTrue(result["compiled"])
        self.assertEqual(first["id"], state["selected_source_id"])
        self.assertEqual(first["id"], state["active_source_id"])
        self.assertIn("HK-01", compiled)
        self.assertNotIn("Added-01", compiled)

    def test_failed_source_activation_keeps_the_active_snapshot(self):
        second_source = (FIXTURES / "update_after.yaml").read_text(encoding="utf-8")
        service, _ = self.service(
            FetchResult(self.source),
            FetchResult(second_source),
        )
        first = service.import_source(
            "Primary", "https://primary.example.invalid/import?token=fixture-a"
        )
        landing = service.upsert_landing(
            {
                "name": "Fixture landing",
                "type": "socks5",
                "server": "landing.example.invalid",
                "port": 1080,
            }
        )
        service.update_bindings(
            [
                {
                    "group": "AI",
                    "enabled": True,
                    "landing_id": landing["id"],
                    "front_hop": {"mode": "source"},
                }
            ]
        )
        token = service.public_state()["subscription"]["token"]
        before = service.read_subscription(token)
        second = service.import_source(
            "Backup", "https://backup.example.invalid/import?token=fixture-b"
        )

        with self.assertRaises(GatewayError) as raised:
            service.activate_source(second["id"])

        state = service.public_state()
        self.assertEqual("SOURCE_NOT_CONFIGURED", raised.exception.code)
        self.assertEqual(first["id"], state["active_source_id"])
        self.assertTrue(state["subscription"]["available"])
        self.assertEqual(before, service.read_subscription(token))

    def test_empty_inactive_source_bindings_do_not_stop_the_active_snapshot(self):
        second_source = (FIXTURES / "update_after.yaml").read_text(encoding="utf-8")
        service, _ = self.service(
            FetchResult(self.source),
            FetchResult(second_source),
        )
        first = service.import_source(
            "Primary", "https://primary.example.invalid/import?token=fixture-a"
        )
        landing = service.upsert_landing(
            {
                "name": "Fixture landing",
                "type": "http",
                "server": "landing.example.invalid",
                "port": 8080,
            }
        )
        service.update_bindings(
            [
                {
                    "group": "AI",
                    "enabled": True,
                    "landing_id": landing["id"],
                    "front_hop": {"mode": "direct"},
                }
            ]
        )
        token = service.public_state()["subscription"]["token"]
        before = service.read_subscription(token)
        service.import_source(
            "Backup", "https://backup.example.invalid/import?token=fixture-b"
        )

        result = service.update_bindings([])
        state = service.public_state()

        self.assertFalse(result["compiled"])
        self.assertEqual(first["id"], state["active_source_id"])
        self.assertTrue(state["subscription"]["available"])
        self.assertEqual(before, service.read_subscription(token))

    def test_deleting_an_inactive_source_selects_the_remaining_snapshot(self):
        second_source = (FIXTURES / "update_after.yaml").read_text(encoding="utf-8")
        service, _ = self.service(
            FetchResult(self.source),
            FetchResult(second_source),
        )
        first = service.import_source(
            "Primary", "https://primary.example.invalid/import?token=fixture-a"
        )
        second = service.import_source(
            "Backup", "https://backup.example.invalid/import?token=fixture-b"
        )
        second_snapshot = (
            self.data_dir / "sources" / second["id"] / "source.yaml"
        )

        service.delete_source(second["id"])
        state = service.public_state()

        self.assertEqual([first["id"]], [source["id"] for source in state["sources"]])
        self.assertEqual(first["id"], state["selected_source_id"])
        self.assertFalse(second_snapshot.exists())

    def test_active_source_cannot_be_deleted(self):
        service, _ = self.service(FetchResult(self.source))
        imported = service.import_source(
            "Primary", "https://primary.example.invalid/import?token=fixture"
        )
        landing = service.upsert_landing(
            {
                "name": "Fixture landing",
                "type": "http",
                "server": "landing.example.invalid",
                "port": 8080,
            }
        )
        service.update_bindings(
            [
                {
                    "group": "AI",
                    "enabled": True,
                    "landing_id": landing["id"],
                    "front_hop": {"mode": "direct"},
                }
            ]
        )

        with self.assertRaises(GatewayError) as raised:
            service.delete_source(imported["id"])

        self.assertEqual("SOURCE_ACTIVE", raised.exception.code)
        self.assertEqual(
            imported["id"], service.public_state()["active_source_id"]
        )

    def test_binding_generates_tokenized_fail_closed_subscription(self):
        service, _ = self.service(FetchResult(self.source))
        service.import_source(
            "Fixture source", "https://subscription.example.invalid/import"
        )
        landing = service.upsert_landing(
            {
                "name": "Fixture landing",
                "type": "socks5",
                "server": "landing.example.invalid",
                "port": 1080,
                "username": "fixture-user",
                "password": "fixture-password",
                "udp": True,
            }
        )

        result = service.update_bindings(
            [
                {
                    "group": "AI",
                    "enabled": True,
                    "landing_id": landing["id"],
                    "front_hop": {"mode": "source"},
                }
            ]
        )
        state = service.public_state()
        compiled = service.read_subscription(state["subscription"]["token"])
        config = yaml.safe_load(compiled)
        groups = {group["name"]: group for group in config["proxy-groups"]}
        proxies = {proxy["name"]: proxy for proxy in config["proxies"]}

        self.assertTrue(result["ok"])
        self.assertTrue(state["subscription"]["available"])
        self.assertEqual(1, state["subscription"]["report"]["counts"]["managed_groups"])
        self.assertNotIn("fixture-password", repr(state))
        self.assertEqual(1, len(groups["AI"]["proxies"]))
        self.assertEqual("landing.example.invalid", proxies[groups["AI"]["proxies"][0]]["server"])

    def test_failed_import_keeps_last_known_good_compiled_output(self):
        service, fetcher = self.service(
            FetchResult(self.source),
            GatewayError("UPSTREAM_FETCH_FAILED", "Upstream unavailable.", status=502),
        )
        active_source = service.import_source(
            "Primary", "https://subscription.example.invalid/import?token=fixture-a"
        )
        landing = service.upsert_landing(
            {
                "name": "Fixture landing",
                "type": "http",
                "server": "landing.example.invalid",
                "port": 8080,
                "password": "fixture-password",
            }
        )
        service.update_bindings(
            [
                {
                    "group": "AI",
                    "enabled": True,
                    "landing_id": landing["id"],
                    "front_hop": {"mode": "automatic"},
                }
            ]
        )
        token = service.public_state()["subscription"]["token"]
        before = service.read_subscription(token)

        with self.assertRaises(GatewayError) as raised:
            service.import_source(
                "Backup", "https://backup.example.invalid/import?token=fixture-b"
            )

        after = service.read_subscription(token)
        state = service.public_state()
        self.assertEqual("UPSTREAM_FETCH_FAILED", raised.exception.code)
        self.assertEqual(before, after)
        self.assertTrue(state["subscription"]["available"])
        self.assertEqual(active_source["id"], state["active_source_id"])
        self.assertEqual(1, len(state["sources"]))
        self.assertEqual("UPSTREAM_FETCH_FAILED", state["last_error"]["code"])
        self.assertEqual(None, fetcher.calls[1]["etag"])

    def test_invalid_import_keeps_last_known_good_compiled_output(self):
        service, _ = self.service(
            FetchResult(self.source),
            FetchResult("proxies: [unterminated\n"),
        )
        service.import_source(
            "Primary", "https://subscription.example.invalid/import?token=fixture-a"
        )
        landing = service.upsert_landing(
            {
                "name": "Fixture landing",
                "type": "socks5",
                "server": "landing.example.invalid",
                "port": 1080,
            }
        )
        service.update_bindings(
            [
                {
                    "group": "AI",
                    "enabled": True,
                    "landing_id": landing["id"],
                    "front_hop": {"mode": "source"},
                }
            ]
        )
        token = service.public_state()["subscription"]["token"]
        before = service.read_subscription(token)

        with self.assertRaises(GatewayError) as raised:
            service.import_source(
                "Broken", "https://broken.example.invalid/import?token=fixture-b"
            )

        self.assertEqual("YAML_INVALID", raised.exception.code)
        self.assertEqual(before, service.read_subscription(token))
        self.assertTrue(service.public_state()["subscription"]["available"])

    def test_scalar_subscription_formats_return_specific_safe_errors(self):
        encoded = "c3M6Ly9maXh0dXJlLWEKdm1lc3M6Ly9maXh0dXJlLWI="
        cases = {
            "UPSTREAM_FORMAT_BASE64": FetchResult(encoded, content_type="text/plain"),
            "UPSTREAM_FORMAT_URI_LIST": FetchResult(
                "ss://fixture-a\nvmess://fixture-b", content_type="text/plain"
            ),
            "UPSTREAM_FORMAT_HTML": FetchResult(
                "<html><body>login required</body></html>",
                content_type="text/html",
            ),
            "UPSTREAM_FORMAT_NODE_ONLY": FetchResult(
                "- {name: Fixture, type: socks5, server: 192.0.2.200, port: 1080}\n",
                content_type="application/yaml",
            ),
            "UPSTREAM_FORMAT_UNSUPPORTED": FetchResult(
                "temporary provider error", content_type="text/plain"
            ),
        }

        for expected_code, response in cases.items():
            with self.subTest(expected_code=expected_code):
                service = GatewayService(
                    Path(self.temp.name) / expected_code,
                    fetcher=QueueFetcher(response),
                )
                with self.assertRaises(GatewayError) as raised:
                    service.import_source(
                        "Fixture source",
                        "https://subscription.example.invalid/import",
                    )
                self.assertEqual(expected_code, raised.exception.code)

    def test_disabling_all_bindings_stops_serving_the_old_generation(self):
        service, _ = self.service(FetchResult(self.source))
        service.import_source(
            "Fixture source", "https://subscription.example.invalid/import"
        )
        landing = service.upsert_landing(
            {
                "name": "Fixture landing",
                "type": "http",
                "server": "landing.example.invalid",
                "port": 8080,
            }
        )
        service.update_bindings(
            [
                {
                    "group": "AI",
                    "enabled": True,
                    "landing_id": landing["id"],
                    "front_hop": {"mode": "direct"},
                }
            ]
        )
        token = service.public_state()["subscription"]["token"]

        service.update_bindings(
            [
                {
                    "group": "AI",
                    "enabled": False,
                    "landing_id": landing["id"],
                    "front_hop": {"mode": "direct"},
                }
            ]
        )

        self.assertFalse(service.public_state()["subscription"]["available"])
        with self.assertRaises(GatewayError) as raised:
            service.read_subscription(token)
        self.assertEqual("NO_COMPILED_SUBSCRIPTION", raised.exception.code)

    def test_wrong_token_and_missing_output_fail_without_leaking_token(self):
        service, _ = self.service()
        token = service.public_state()["subscription"]["token"]

        with self.assertRaises(GatewayError) as missing:
            service.read_subscription(token)
        with self.assertRaises(GatewayError) as denied:
            service.read_subscription("wrong-token")

        self.assertEqual("NO_COMPILED_SUBSCRIPTION", missing.exception.code)
        self.assertEqual("SUBSCRIPTION_NOT_FOUND", denied.exception.code)
        self.assertNotIn(token, str(denied.exception))

    def test_tampered_generation_pointer_cannot_escape_data_directory(self):
        service, _ = self.service()
        token = service.public_state()["subscription"]["token"]
        stored = json.loads((self.data_dir / "state.json").read_text(encoding="utf-8"))
        stored["active_generation"] = "../../outside"
        (self.data_dir / "state.json").write_text(
            json.dumps(stored), encoding="utf-8"
        )
        reloaded = GatewayService(self.data_dir, fetcher=QueueFetcher())

        self.assertFalse(reloaded.public_state()["subscription"]["available"])
        with self.assertRaises(GatewayError) as raised:
            reloaded.read_subscription(token)
        self.assertEqual("COMPILED_CACHE_MISSING", raised.exception.code)

    def test_state_file_contains_credentials_but_public_state_does_not(self):
        service, _ = self.service()
        landing = service.upsert_landing(
            {
                "name": "Fixture landing",
                "type": "socks5",
                "server": "landing.example.invalid",
                "port": 1080,
                "username": "fixture-user",
                "password": "fixture-password",
            }
        )

        public = service.public_state()
        stored = json.loads((self.data_dir / "state.json").read_text(encoding="utf-8"))

        public_landing = next(
            item for item in public["landings"] if item["id"] == landing["id"]
        )
        self.assertTrue(public_landing["has_password"])
        self.assertNotIn("fixture-password", repr(public))
        self.assertEqual("fixture-password", stored["landings"][0]["password"])

    def test_landing_can_be_added_from_a_socks5_url_without_storing_the_raw_url(self):
        service, _ = self.service()
        landing_url = (
            "socks5://fixture%20user:fixture%3Apassword@192.0.2.44:1081"
        )

        landing = service.upsert_landing(
            {
                "name": "URL landing",
                "url": landing_url,
                "udp": False,
            }
        )
        stored_text = (self.data_dir / "state.json").read_text(encoding="utf-8")
        stored = json.loads(stored_text)["landings"][0]

        self.assertEqual("socks5", landing["type"])
        self.assertEqual("192.0.2.44", landing["server"])
        self.assertEqual(1081, landing["port"])
        self.assertEqual("fixture user", landing["username"])
        self.assertTrue(landing["has_password"])
        self.assertFalse(landing["udp"])
        self.assertEqual("fixture:password", stored["password"])
        self.assertNotIn("socks5://", stored_text)
        self.assertNotIn(landing_url, stored_text)

    def test_landing_can_parse_host_port_username_password(self):
        service, _ = self.service()
        proxy_string = (
            "proxy.example.invalid:8080:fixture-user:fixture-password"
        )

        landing = service.upsert_landing(
            {
                "name": "Colon landing",
                "type": "http",
                "url": proxy_string,
            }
        )
        stored_text = (self.data_dir / "state.json").read_text(encoding="utf-8")

        self.assertEqual("http", landing["type"])
        self.assertEqual("proxy.example.invalid", landing["server"])
        self.assertEqual(8080, landing["port"])
        self.assertEqual("fixture-user", landing["username"])
        self.assertTrue(landing["has_password"])
        self.assertNotIn(proxy_string, stored_text)

    def test_landing_can_parse_username_password_at_host_port(self):
        service, _ = self.service()

        landing = service.upsert_landing(
            {
                "name": "Authority landing",
                "type": "socks5",
                "url": (
                    "fixture%20user:fixture%3Apassword@"
                    "proxy.example.invalid:1080"
                ),
            }
        )

        self.assertEqual("socks5", landing["type"])
        self.assertEqual("proxy.example.invalid", landing["server"])
        self.assertEqual(1080, landing["port"])
        self.assertEqual("fixture user", landing["username"])
        self.assertTrue(landing["has_password"])
        stored = json.loads(
            (self.data_dir / "state.json").read_text(encoding="utf-8")
        )["landings"][0]
        self.assertEqual("fixture:password", stored["password"])

    def test_landing_can_parse_username_password_host_port(self):
        service, _ = self.service()

        landing = service.upsert_landing(
            {
                "name": "Credentials-first landing",
                "type": "http",
                "url": (
                    "fixture-user:fixture-password:"
                    "proxy.example.invalid:8080"
                ),
            }
        )

        self.assertEqual("http", landing["type"])
        self.assertEqual("proxy.example.invalid", landing["server"])
        self.assertEqual(8080, landing["port"])
        self.assertEqual("fixture-user", landing["username"])
        self.assertTrue(landing["has_password"])

    def test_landing_can_parse_host_port_without_authentication(self):
        service, _ = self.service()

        landing = service.upsert_landing(
            {
                "name": "IP allowlist landing",
                "type": "socks5",
                "url": "proxy.example.invalid:1080",
            }
        )

        self.assertEqual("socks5", landing["type"])
        self.assertEqual("proxy.example.invalid", landing["server"])
        self.assertEqual(1080, landing["port"])
        self.assertEqual("", landing["username"])
        self.assertFalse(landing["has_password"])

    def test_ambiguous_four_part_proxy_string_is_rejected(self):
        service, _ = self.service()

        with self.assertRaises(GatewayError) as raised:
            service.upsert_landing(
                {
                    "name": "Ambiguous landing",
                    "type": "http",
                    "url": "proxy.example.invalid:8080:fixture-user:1234",
                }
            )

        self.assertEqual("LANDING_URL_INVALID", raised.exception.code)
        self.assertEqual([], service.public_state()["landings"])

    def test_landing_url_rejects_unsupported_or_incomplete_urls(self):
        cases = (
            "ftp://fixture-user:fixture-password@192.0.2.44:1081",
            "socks5://fixture-user:fixture-password@192.0.2.44",
            "socks5://fixture-user:fixture-password@192.0.2.44:1081/path",
            "http://fixture-user:fixture-password@192.0.2.44:8080?mode=proxy",
        )

        for index, landing_url in enumerate(cases):
            with self.subTest(landing_url=landing_url):
                service = GatewayService(Path(self.temp.name) / f"invalid-url-{index}")
                with self.assertRaises(GatewayError) as raised:
                    service.upsert_landing(
                        {"name": "Invalid URL landing", "url": landing_url}
                    )
                self.assertEqual("LANDING_URL_INVALID", raised.exception.code)
                self.assertNotIn("fixture-password", str(raised.exception))
                self.assertEqual([], service.public_state()["landings"])

    def test_editing_an_active_landing_recompiles_the_subscription(self):
        service, _ = self.service(FetchResult(self.source))
        service.import_source(
            "Fixture source", "https://subscription.example.invalid/import"
        )
        landing = service.upsert_landing(
            {
                "name": "Fixture landing",
                "type": "socks5",
                "server": "old-landing.example.invalid",
                "port": 1080,
            }
        )
        service.update_bindings(
            [
                {
                    "group": "AI",
                    "enabled": True,
                    "landing_id": landing["id"],
                    "front_hop": {"mode": "source"},
                }
            ]
        )
        token = service.public_state()["subscription"]["token"]

        service.upsert_landing(
            {
                "id": landing["id"],
                "name": "Fixture landing",
                "type": "socks5",
                "server": "new-landing.example.invalid",
                "port": 1080,
            }
        )

        compiled = service.read_subscription(token)
        self.assertIn("new-landing.example.invalid", compiled)
        self.assertNotIn("old-landing.example.invalid", compiled)

    def test_deleting_an_active_landing_disables_stale_subscription_output(self):
        service, _ = self.service(FetchResult(self.source))
        service.import_source(
            "Fixture source", "https://subscription.example.invalid/import"
        )
        landing = service.upsert_landing(
            {
                "name": "Fixture landing",
                "type": "http",
                "server": "landing.example.invalid",
                "port": 8080,
            }
        )
        service.update_bindings(
            [
                {
                    "group": "AI",
                    "enabled": True,
                    "landing_id": landing["id"],
                    "front_hop": {"mode": "direct"},
                }
            ]
        )
        token = service.public_state()["subscription"]["token"]

        service.delete_landing(landing["id"])

        state = service.public_state()
        self.assertFalse(state["subscription"]["available"])
        self.assertEqual(None, state["active_source_id"])
        self.assertEqual(0, state["sources"][0]["managed_group_count"])
        with self.assertRaises(GatewayError) as raised:
            service.read_subscription(token)
        self.assertEqual("NO_COMPILED_SUBSCRIPTION", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
