from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from egress_anchor.gateway import FetchResult, GatewayError, GatewayService


FIXTURES = Path(__file__).parent / "fixtures"


class GatewayRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name) / "gateway"
        self.source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")

    def service(self):
        return GatewayService(
            self.data_dir,
            fetcher=lambda *_args, **_kwargs: FetchResult(self.source),
        )

    def active_service(self):
        service = self.service()
        source = service.import_source("Primary", "https://primary.example.invalid/sub")
        landing = service.upsert_landing(
            {
                "name": "Primary landing",
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
                    "front_hop": {"mode": "direct"},
                }
            ]
        )
        return service, source, landing

    def assert_origin_only(self, service, expected):
        state = service.public_state()
        stored = service.state_path.read_text(encoding="utf-8")
        self.assertEqual(expected, state["sources"][0]["display_url"])
        self.assertEqual(expected, json.loads(stored)["sources"][0]["display_url"])
        for credential in (
            "fixture-user",
            "fixture-password",
            "fixture-path-token",
            "fixture-query-token",
            "fixture-fragment-token",
        ):
            self.assertNotIn(credential, repr(state))
            self.assertNotIn(credential, stored)

    def test_import_discards_credentials_in_every_url_component(self):
        service = self.service()
        service.import_source(
            "Private source",
            "https://fixture-user:fixture-password@subscription.example.invalid:8443/"
            "sub/fixture-path-token?token=fixture-query-token#fixture-fragment-token",
        )

        self.assert_origin_only(service, "https://subscription.example.invalid:8443")

    def test_loading_existing_v2_state_removes_saved_url_credentials(self):
        service = self.service()
        service.import_source("Existing", "https://subscription.example.invalid/sub")
        stored = json.loads(service.state_path.read_text(encoding="utf-8"))
        stored["sources"][0]["display_url"] = (
            "https://fixture-user:fixture-password@subscription.example.invalid/"
            "sub/fixture-path-token?token=fixture-query-token#fixture-fragment-token"
        )
        token = stored["token"]
        service.state_path.write_text(json.dumps(stored), encoding="utf-8")

        reloaded = self.service()

        self.assert_origin_only(reloaded, "https://subscription.example.invalid")
        self.assertEqual(token, reloaded.public_state()["subscription"]["token"])

    def test_v1_migration_discards_path_credentials(self):
        self.data_dir.mkdir()
        (self.data_dir / "state.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "upstream": {
                        "url": "https://subscription.example.invalid/sub/fixture-path-token"
                        "?token=fixture-query-token#fixture-fragment-token"
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.data_dir / "source.yaml").write_text(self.source, encoding="utf-8")

        service = self.service()

        self.assert_origin_only(service, "https://subscription.example.invalid")

    def test_stale_tab_cannot_overwrite_and_activate_another_source(self):
        service, primary, _landing = self.active_service()
        stale_bindings = deepcopy(service.public_state()["bindings"])
        backup = service.import_source("Backup", "https://backup.example.invalid/sub")
        backup_landing = service.upsert_landing(
            {
                "name": "Backup landing",
                "type": "http",
                "server": "backup-landing.example.invalid",
                "port": 8080,
            }
        )
        service.update_bindings(
            [
                {
                    "group": "AI",
                    "enabled": True,
                    "landing_id": backup_landing["id"],
                    "front_hop": {"mode": "direct"},
                }
            ]
        )
        before = service.public_state()
        stored = service.state_path.read_text(encoding="utf-8")
        compiled = service.read_subscription(before["subscription"]["token"])

        with self.assertRaises(GatewayError) as raised:
            service.update_bindings(
                stale_bindings,
                expected_source_id=primary["id"],
            )

        self.assertEqual("SOURCE_CHANGED", raised.exception.code)
        self.assertEqual(409, raised.exception.status)
        self.assertEqual(before, service.public_state())
        self.assertEqual(stored, service.state_path.read_text(encoding="utf-8"))
        self.assertEqual(compiled, service.read_subscription(before["subscription"]["token"]))
        self.assertEqual(backup["id"], service.public_state()["active_source_id"])

    def test_matching_source_guard_allows_saving_bindings(self):
        service, source, _landing = self.active_service()
        bindings = service.public_state()["bindings"]
        bindings[0]["front_hop"] = {"mode": "source"}

        result = service.update_bindings(bindings, expected_source_id=source["id"])

        self.assertTrue(result["compiled"])
        self.assertEqual(source["id"], service.public_state()["active_source_id"])

    def assert_failed_write_retains_state(self, service, operation):
        before = service.public_state()
        stored = service.state_path.read_text(encoding="utf-8")
        compiled = service.read_subscription(before["subscription"]["token"])

        with patch.object(service, "_save_state_value", side_effect=OSError("fixture disk failure")):
            with self.assertRaises(OSError):
                operation()

        self.assertEqual(before, service.public_state())
        self.assertEqual(stored, service.state_path.read_text(encoding="utf-8"))
        self.assertEqual(compiled, service.read_subscription(before["subscription"]["token"]))

    def test_failed_landing_edit_does_not_publish_a_new_exit(self):
        service, _source, landing = self.active_service()
        self.assert_failed_write_retains_state(
            service,
            lambda: service.upsert_landing(
                {
                    "id": landing["id"],
                    "name": "Changed landing",
                    "type": "socks5",
                    "server": "changed-landing.example.invalid",
                    "port": 1080,
                }
            ),
        )

    def test_failed_landing_delete_keeps_active_subscription(self):
        service, _source, landing = self.active_service()
        self.assert_failed_write_retains_state(
            service, lambda: service.delete_landing(landing["id"])
        )

    def test_failed_selection_keeps_previous_source(self):
        service, primary, _landing = self.active_service()
        backup = service.import_source("Backup", "https://backup.example.invalid/sub")
        service.select_source(primary["id"])
        self.assert_failed_write_retains_state(
            service, lambda: service.select_source(backup["id"])
        )

    def test_failed_import_does_not_publish_a_partial_source_or_error_state(self):
        service, _source, _landing = self.active_service()
        self.assert_failed_write_retains_state(
            service,
            lambda: service.import_source("Backup", "https://backup.example.invalid/sub"),
        )


if __name__ == "__main__":
    unittest.main()
