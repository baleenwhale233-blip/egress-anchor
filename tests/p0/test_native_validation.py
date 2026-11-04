from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from guest_native_validation import validate_candidate


class NativeValidationResourceTests(unittest.TestCase):
    def test_only_known_geodata_is_aliased_without_copying_private_state(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("GEOSITE.dat", "GEOIP.dat", "cache.db", "shared_preferences.json"):
                (root / name).write_text("fixture")

            def checked(arguments, **kwargs):
                candidate = Path(arguments[-1])
                temporary = candidate.parent
                self.assertEqual("rules: []\n", candidate.read_text())
                self.assertEqual((root / "GEOSITE.dat").resolve(), (temporary / "GeoSite.dat").resolve())
                self.assertTrue((temporary / "GeoSite.dat").is_symlink())
                self.assertFalse((temporary / "cache.db").exists())
                self.assertFalse((temporary / "shared_preferences.json").exists())
                return SimpleNamespace(returncode=0)

            with patch("guest_native_validation.subprocess.run", side_effect=checked):
                validate_candidate("rules: []\n", root)
            self.assertEqual("fixture", (root / "GEOSITE.dat").read_text())

    def test_native_rejection_is_not_replaced_by_unvalidated_output(self):
        with TemporaryDirectory() as directory, \
             patch("guest_native_validation.subprocess.run", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(ValueError, "rejected"):
                validate_candidate("rules: []\n", Path(directory))


if __name__ == "__main__":
    unittest.main()
