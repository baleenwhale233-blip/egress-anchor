from contextlib import redirect_stdout
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import guest_flclash_overlay as overlay
from guest_flclash_runtime import ensure_ready


class FlClashReadinessTests(unittest.TestCase):
    def test_missing_legacy_script_engine_is_rejected_before_controller_use(self):
        with TemporaryDirectory() as directory, \
             patch("guest_flclash_runtime.ctypes.CDLL", side_effect=OSError("missing")), \
             patch("guest_flclash_runtime.request") as request:
            with self.assertRaisesRegex(ValueError, "NATIVE_SCRIPT_ENGINE_UNAVAILABLE"):
                ensure_ready(Path(directory), "flclash-0894", {"scripts": [{}]})
            request.assert_not_called()

    def test_failed_preflight_does_not_write_receipt_or_stop_the_client(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ports.json").write_text('{"landing":1080}')
            receipt = root / "receipt.json"
            with patch.object(overlay, "ROOT", root), patch.object(overlay, "FIXTURES", root), \
                 patch.object(overlay, "RECEIPT", receipt), patch.object(overlay, "plan", return_value={"scripts": []}), \
                 patch.object(overlay, "ensure_ready", side_effect=ValueError("native not ready")), \
                 patch.object(overlay, "stop_client") as stop, redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "not ready"):
                    overlay.install()
            self.assertFalse(receipt.exists())
            stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
