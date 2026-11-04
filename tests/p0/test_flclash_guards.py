from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from guest_flclash_guards import check_installed, connection, plan, write_plan


class FlClashNativeGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        with sqlite3.connect(self.root / "database.sqlite") as database:
            database.executescript('''PRAGMA user_version=21;
              CREATE TABLE profiles(id INTEGER PRIMARY KEY,type TEXT,extend_type TEXT,script_id INTEGER);
              CREATE TABLE scripts(id INTEGER PRIMARY KEY,label TEXT);
              CREATE TABLE rules(id INTEGER PRIMARY KEY,profile_id INTEGER REFERENCES profiles(id),
                rule_action TEXT,content TEXT,rule_target TEXT,rule_provider TEXT,sub_rule TEXT,
                no_resolve INTEGER DEFAULT 0,src INTEGER DEFAULT 0,"order" TEXT);
              INSERT INTO profiles VALUES(1,'url','standard',NULL),(2,'url','script',20);
              INSERT INTO scripts VALUES(20,'user');
              INSERT INTO rules(id,profile_id,rule_action,content,rule_target) VALUES(101,1,'DOMAIN','user.invalid','DIRECT');
            ''')
        database.close()
        (self.root / "scripts/20.js").write_text("function main(c) { return c; }\n")

    def test_standard_modes_and_user_rules_are_not_rewritten(self):
        changes = plan(self.root, 1080)
        write_plan(self.root, changes)
        with connection(self.root, True) as database:
            modes = [tuple(row) for row in database.execute("SELECT id,extend_type,script_id FROM profiles ORDER BY id")]
            self.assertEqual([(1, "standard", None), (2, "script", 20)], modes)
            self.assertEqual("DIRECT", database.execute("SELECT rule_target FROM rules WHERE id=101").fetchone()[0])
        check_installed(self.root, changes)
        write_plan(self.root, changes, restoring=True)
        with connection(self.root, True) as database:
            self.assertEqual([101], [row[0] for row in database.execute("SELECT id FROM rules")])
        self.assertEqual("function main(c) { return c; }\n", (self.root / "scripts/20.js").read_text())

    def test_new_standard_subscription_inherits_global_guard_without_metadata_write(self):
        changes = plan(self.root, 1080)
        write_plan(self.root, changes)
        with connection(self.root) as database:
            database.execute("INSERT INTO profiles VALUES(3,'url','standard',NULL)")
            rows = list(database.execute('SELECT rule_target FROM rules WHERE profile_id IS NULL ORDER BY "order"'))
            self.assertEqual("REJECT", rows[0][0])
        check_installed(self.root, changes)
        write_plan(self.root, changes, restoring=True)
        with connection(self.root, True) as database:
            self.assertIsNotNone(database.execute("SELECT id FROM profiles WHERE id=3").fetchone())

    def test_guard_edit_is_preserved_and_blocks_all_restore_writes(self):
        changes = plan(self.root, 1080)
        write_plan(self.root, changes)
        before = (self.root / "scripts/20.js").read_text()
        with connection(self.root) as database:
            database.execute("UPDATE rules SET rule_target='DIRECT' WHERE id=?", (changes["guards"][0]["id"],))
        with self.assertRaisesRegex(ValueError, "restore conflict"):
            write_plan(self.root, changes, restoring=True)
        self.assertEqual(before, (self.root / "scripts/20.js").read_text())

    def test_missing_or_new_script_profile_is_not_silently_marked_protected(self):
        changes = plan(self.root, 1080)
        write_plan(self.root, changes)
        with connection(self.root) as database:
            database.execute("INSERT INTO profiles VALUES(3,'url','script',30)")
        with self.assertRaisesRegex(ValueError, "explicit registration"):
            check_installed(self.root, changes)

    def test_later_custom_overwrite_does_not_reuse_standard_guard_claim(self):
        changes = plan(self.root, 1080)
        write_plan(self.root, changes)
        with connection(self.root) as database:
            database.execute("UPDATE profiles SET extend_type='custom' WHERE id=1")
        with self.assertRaisesRegex(ValueError, "custom overwrite"):
            check_installed(self.root, changes)


if __name__ == "__main__":
    unittest.main()
