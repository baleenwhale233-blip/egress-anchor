from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import guest_verge_overlay as overlay


class VergeRecoveryTests(unittest.TestCase):
    def test_failed_runtime_verification_retains_a_recoverable_receipt(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "profiles").mkdir()
            script = root / "profiles/Script.js"
            script.write_text("original\n")
            (root / "clash-verge.yaml").write_text("x-ea-p0-state: active\nproxies: [{name: EA-P0-Landing}]\n")
            (root / "ports.json").write_text('{"landing":1080}')
            receipt = root / "receipt.json"
            changes = [{"path": "profiles/Script.js", "before": "original\n", "after": "owned\n"}]
            with patch.object(overlay, "ROOT", root), patch.object(overlay, "FIXTURES", root), \
                 patch.object(overlay, "RECEIPT", receipt), patch.object(overlay, "plan", return_value=changes), \
                 patch.object(overlay, "update", side_effect=ValueError("runtime route mismatch")), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "route mismatch"):
                    overlay.install()
            self.assertEqual("prepared", json.loads(receipt.read_text())["status"])
            self.assertEqual("owned\n", script.read_text())
            with patch.object(overlay, "ROOT", root), patch.object(overlay, "RECEIPT", receipt), \
                 patch.object(overlay, "update", return_value={"hot_restored": True}), \
                 redirect_stdout(io.StringIO()):
                overlay.restore()
            self.assertEqual("restored", json.loads(receipt.read_text())["status"])
            self.assertEqual("original\n", script.read_text())

    def test_repeated_restore_does_not_contact_or_restart_the_client(self):
        with TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            receipt.write_text('{"client":"verge-p0","version":2,"status":"restored"}')
            with patch.object(overlay, "RECEIPT", receipt), patch.object(overlay, "update") as update, \
                 redirect_stdout(io.StringIO()) as output:
                overlay.restore()
            update.assert_not_called()
            self.assertEqual({"already_restored": True}, json.loads(output.getvalue()))

    def test_partial_script_output_is_rebuilt_instead_of_hot_restored(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "profiles").mkdir()
            script = root / "profiles/Script.js"
            script.write_text("owned\n")
            (root / "clash-verge.yaml").write_text("x-ea-p0-state: script-error\nproxies: []\n")
            receipt = root / "receipt.json"
            receipt.write_text(json.dumps({"client": "verge-p0", "version": 2, "status": "installed", "port": 1080,
                                           "files": [{"path": "profiles/Script.js", "before": "original\n", "after": "owned\n"}]}))
            with patch.object(overlay, "ROOT", root), patch.object(overlay, "RECEIPT", receipt), \
                 patch.object(overlay, "update") as update, patch.object(overlay, "stop_client") as stop, \
                 patch.object(overlay, "run_client") as start, redirect_stdout(io.StringIO()) as output:
                overlay.restore()
            update.assert_not_called()
            stop.assert_called_once_with("verge")
            start.assert_called_once_with("start", "verge")
            self.assertEqual("original\n", script.read_text())
            self.assertTrue(json.loads(output.getvalue())["runtime_verification_required"])


if __name__ == "__main__":
    unittest.main()
