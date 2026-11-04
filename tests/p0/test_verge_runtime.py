from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from guest_verge_runtime import update


class VergeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "clash-verge.yaml"
        self.original = "mixed-port: 7897\nproxies: []\nrules: ['MATCH,DIRECT']\n"
        self.path.write_text(self.original)
        (self.root / "profiles.yaml").write_text("current: A\n")
        self.desired = yaml.safe_load(self.original)
        self.desired["proxies"] = [{"name": "EA-P0-Landing"}]

    def invoke(self, api, checked=None):
        with patch("guest_verge_runtime.transform", return_value=self.desired), \
             patch("guest_native_validation.subprocess.run", side_effect=checked or (lambda *a, **k: SimpleNamespace(returncode=0))), \
             patch("guest_verge_runtime.request", side_effect=api), \
             patch("guest_verge_runtime.verify_routes", return_value={"verified_service_route": "managed"}):
            return update(self.root, "apply", 1080)

    def test_null_connection_list_is_valid_and_does_not_close_everything(self):
        calls = []

        def api(runtime, client, method, path, body=None):
            calls.append((method, path))
            return {"connections": None} if path == "/connections" else {"proxies": {"EA-P0-Landing": {}}}

        result = self.invoke(api)
        self.assertTrue(result["hot_applied"])
        self.assertEqual(0, result["closed_protected_connections"])
        self.assertFalse(any(method == "DELETE" for method, _ in calls))

    def test_only_protected_connection_ids_are_closed(self):
        calls = []

        def api(runtime, client, method, path, body=None):
            calls.append((method, path))
            if path == "/connections":
                return {"connections": [{"id": "protected", "metadata": {"host": "managed.p0.invalid"}},
                                        {"id": "keep", "metadata": {"host": "unmanaged.p0.invalid"}}]}
            return {"proxies": {"EA-P0-Landing": {}}}

        self.invoke(api)
        self.assertEqual([("DELETE", "/connections/protected")], [call for call in calls if call[0] == "DELETE"])

    def test_validation_failure_leaves_original_runtime_untouched(self):
        with self.assertRaisesRegex(ValueError, "rejected"):
            self.invoke(lambda *a: self.fail("controller must not be called"),
                        lambda *a, **k: SimpleNamespace(returncode=1))
        self.assertEqual(self.original, self.path.read_text())

    def test_source_change_while_preparing_is_not_overwritten(self):
        def checked(*args, **kwargs):
            self.path.write_text("native-new-runtime\n")
            return SimpleNamespace(returncode=0)

        with self.assertRaisesRegex(ValueError, "changed while preparing"):
            self.invoke(lambda *a: self.fail("controller must not be called"), checked)
        self.assertEqual("native-new-runtime\n", self.path.read_text())


if __name__ == "__main__":
    unittest.main()
