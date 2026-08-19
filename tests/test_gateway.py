from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import unittest

import yaml

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

        self.assertFalse(state["upstream"]["configured"])
        self.assertFalse(state["subscription"]["available"])
        self.assertGreaterEqual(len(state["subscription"]["token"]), 32)
        self.assertEqual([], state["landings"])
        self.assertEqual([], state["catalog"]["groups"])
        self.assertEqual(0o700, self.data_dir.stat().st_mode & 0o777)
        self.assertEqual(0o600, (self.data_dir / "state.json").stat().st_mode & 0o777)

    def test_refresh_discovers_groups_without_requiring_a_binding(self):
        service, fetcher = self.service(
            FetchResult(
                self.source,
                etag='"fixture-v1"',
                last_modified="Wed, 19 Aug 2026 00:00:00 GMT",
            )
        )
        service.configure_upstream(
            "https://subscription.example.invalid/full.yaml?token=fixture-secret"
        )

        result = service.refresh()
        state = service.public_state()

        self.assertTrue(result["ok"])
        self.assertEqual(["AI", "Default"], [g["name"] for g in state["catalog"]["groups"]])
        self.assertFalse(state["subscription"]["available"])
        self.assertEqual(
            "https://subscription.example.invalid/full.yaml?…",
            state["upstream"]["display_url"],
        )
        self.assertNotIn("fixture-secret", repr(state))
        self.assertEqual(None, fetcher.calls[0]["etag"])
        self.assertEqual(0o600, (self.data_dir / "source.yaml").stat().st_mode & 0o777)

    def test_binding_generates_tokenized_fail_closed_subscription(self):
        service, _ = self.service(FetchResult(self.source))
        service.configure_upstream("https://subscription.example.invalid/full.yaml")
        service.refresh()
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

    def test_failed_refresh_keeps_last_known_good_compiled_output(self):
        service, fetcher = self.service(
            FetchResult(self.source, etag='"fixture-v1"'),
            GatewayError("UPSTREAM_FETCH_FAILED", "Upstream unavailable.", status=502),
        )
        service.configure_upstream("https://subscription.example.invalid/full.yaml")
        service.refresh()
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
            service.refresh()

        after = service.read_subscription(token)
        state = service.public_state()
        self.assertEqual("UPSTREAM_FETCH_FAILED", raised.exception.code)
        self.assertEqual(before, after)
        self.assertTrue(state["subscription"]["available"])
        self.assertEqual("UPSTREAM_FETCH_FAILED", state["last_error"]["code"])
        self.assertEqual('"fixture-v1"', fetcher.calls[1]["etag"])

    def test_invalid_yaml_refresh_keeps_last_known_good_compiled_output(self):
        service, _ = self.service(
            FetchResult(self.source),
            FetchResult("proxies: [unterminated\n"),
        )
        service.configure_upstream("https://subscription.example.invalid/full.yaml")
        service.refresh()
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
            service.refresh()

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
                service.configure_upstream(
                    "https://subscription.example.invalid/full.yaml"
                )
                with self.assertRaises(GatewayError) as raised:
                    service.refresh()
                self.assertEqual(expected_code, raised.exception.code)

    def test_disabling_all_bindings_stops_serving_the_old_generation(self):
        service, _ = self.service(FetchResult(self.source))
        service.configure_upstream("https://subscription.example.invalid/full.yaml")
        service.refresh()
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

    def test_editing_an_active_landing_recompiles_the_subscription(self):
        service, _ = self.service(FetchResult(self.source))
        service.configure_upstream("https://subscription.example.invalid/full.yaml")
        service.refresh()
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
        service.configure_upstream("https://subscription.example.invalid/full.yaml")
        service.refresh()
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

        self.assertFalse(service.public_state()["subscription"]["available"])
        with self.assertRaises(GatewayError) as raised:
            service.read_subscription(token)
        self.assertEqual("NO_COMPILED_SUBSCRIPTION", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
