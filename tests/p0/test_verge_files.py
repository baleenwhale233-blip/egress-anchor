from pathlib import Path
import os
from tempfile import TemporaryDirectory
import unittest

import yaml

from guest_verge_files import guarded_merge, plan, preflight, write_plan


class VergeFilePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profiles = self.root / "profiles"
        self.profiles.mkdir()
        metadata = {"current": "A", "items": [{"uid": "A", "type": "remote", "file": "A.yaml",
                    "option": {"script": "user", "merge": "profile-merge", "rules": "rules", "groups": "groups"}}]}
        (self.root / "profiles.yaml").write_text(yaml.safe_dump(metadata))
        files = {"A.yaml": "proxies: [{name: airport}]\nproxy-groups: []\nrules: []\n",
                 "Script.js": "function main(c) { return c; }\n",
                 "Merge.yaml": "# keep this comment\nprofile: {store-selected: true}\n",
                 "user.js": "function main(c) { c.rules.unshift('DOMAIN,x.invalid,DIRECT'); return c; }\n",
                 "profile-merge.yaml": "rules: ['MATCH,DIRECT']\nproxy-groups: []\ncustom: keep\n",
                 "rules.yaml": "prepend: ['DOMAIN,user.invalid,DIRECT']\nappend: []\ndelete: []\n",
                 "groups.yaml": "prepend: []\nappend: []\ndelete: []\n"}
        for name, text in files.items():
            (self.profiles / name).write_text(text)
        self.original = {p.name: p.read_bytes() for p in self.profiles.iterdir()}

    def test_plan_does_not_write_and_restore_preserves_original_bytes(self):
        changes = plan(self.root, 1080)
        self.assertEqual(self.original, {p.name: p.read_bytes() for p in self.profiles.iterdir()})
        write_plan(self.root, changes)
        merge = yaml.safe_load((self.profiles / "profile-merge.yaml").read_text())
        self.assertEqual("keep", merge["custom"])
        self.assertEqual("DOMAIN,managed.p0.invalid,EA-P0-Deny", merge["rules"][0])
        write_plan(self.root, changes, restoring=True)
        self.assertEqual(self.original, {p.name: p.read_bytes() for p in self.profiles.iterdir()})

    def test_later_user_edit_prevents_any_restore_write(self):
        changes = plan(self.root, 1080)
        write_plan(self.root, changes)
        last = self.root / changes[-1]["path"]
        last.write_text(last.read_text() + "\n# later user edit\n")
        before = {p.name: p.read_bytes() for p in self.profiles.iterdir()}
        with self.assertRaisesRegex(ValueError, "restore conflict"):
            write_plan(self.root, changes, restoring=True)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.profiles.iterdir()})

    def test_prepared_partial_write_can_restore_without_touching_source_updates(self):
        changes = plan(self.root, 1080)
        (self.root / changes[0]["path"]).write_text(changes[0]["after"])
        source = self.profiles / "A.yaml"
        source.write_text("proxies: [{name: airport-new}]\n")
        write_plan(self.root, changes, restoring=True)
        self.assertEqual("proxies: [{name: airport-new}]\n", source.read_text())
        self.assertEqual(self.original["Script.js"], (self.profiles / "Script.js").read_bytes())

    def test_reserved_names_and_external_extension_paths_are_rejected(self):
        (self.profiles / "user.js").write_text("// EA-P0-user-owned")
        with self.assertRaisesRegex(ValueError, "namespace"):
            plan(self.root, 1080)
        (self.profiles / "user.js").write_bytes(self.original["user.js"])
        with self.assertRaisesRegex(ValueError, "outside"):
            preflight(self.root, [{"path": "elsewhere.txt", "before": "", "after": ""}])

    def test_merge_without_routing_override_keeps_comments_untouched(self):
        text = self.original["Merge.yaml"].decode()
        self.assertEqual(text, guarded_merge(text))

    def test_different_extension_paths_cannot_alias_the_same_file(self):
        path = self.profiles / "user.js"
        path.unlink()
        os.link(self.profiles / "Script.js", path)
        with self.assertRaisesRegex(ValueError, "alias"):
            plan(self.root, 1080)


if __name__ == "__main__":
    unittest.main()
