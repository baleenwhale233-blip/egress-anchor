from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from guest_flclash_guards import check_installed, plan, write_plan
from guest_flclash_store import connection, profiles


class LegacyNativeStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        with sqlite3.connect(self.root / "database.sqlite") as database:
            database.executescript('''PRAGMA user_version=2;
              CREATE TABLE profiles(id INTEGER PRIMARY KEY,url TEXT,overwrite_type TEXT,script_id INTEGER);
              CREATE TABLE scripts(id INTEGER PRIMARY KEY,label TEXT);
              CREATE TABLE rules(id INTEGER PRIMARY KEY,rule_action TEXT,content TEXT,rule_target TEXT,
                rule_provider TEXT,sub_rule TEXT,no_resolve INTEGER DEFAULT 0,src INTEGER DEFAULT 0);
              CREATE TABLE profile_rule_mapping(id TEXT PRIMARY KEY,profile_id INTEGER REFERENCES profiles(id),
                rule_id INTEGER REFERENCES rules(id) ON DELETE CASCADE,scene TEXT,"order" TEXT);
              INSERT INTO profiles VALUES(1,'https://source.invalid','standard',NULL),(2,'https://other.invalid','script',20);
              INSERT INTO scripts VALUES(20,'user');
              INSERT INTO rules(id,rule_action,content,rule_target) VALUES(101,'DOMAIN','user.invalid','DIRECT');
              INSERT INTO profile_rule_mapping VALUES('101',NULL,101,NULL,'a0');
            ''')
        database.close()
        (self.root / "scripts/20.js").write_text("function main(c) { return c; }\n")

    def test_legacy_descending_global_order_and_mapping_are_respected(self):
        changes = plan(self.root, 1080)
        write_plan(self.root, changes)
        with connection(self.root, True) as database:
            rows = list(database.execute('SELECT rules.rule_target FROM rules JOIN profile_rule_mapping m ON m.rule_id=rules.id WHERE m.profile_id IS NULL ORDER BY m."order" DESC'))
            self.assertEqual(["REJECT", "DIRECT"], [row[0] for row in rows])
            self.assertEqual("standard", profiles(database)[0]["extend_type"])
        check_installed(self.root, changes)

    def test_restore_keeps_original_links_new_subscriptions_and_script_bytes(self):
        changes = plan(self.root, 1080)
        write_plan(self.root, changes)
        with connection(self.root) as database:
            database.execute("INSERT INTO profiles VALUES(3,'https://new.invalid','standard',NULL)")
        write_plan(self.root, changes, restoring=True)
        with connection(self.root, True) as database:
            self.assertEqual(["101"], [row[0] for row in database.execute("SELECT id FROM profile_rule_mapping")])
            self.assertEqual([101], [row[0] for row in database.execute("SELECT id FROM rules")])
            self.assertEqual(3, len(profiles(database)))
        self.assertEqual("function main(c) { return c; }\n", (self.root / "scripts/20.js").read_text())

    def test_later_link_edit_blocks_restore_before_any_write(self):
        changes = plan(self.root, 1080)
        write_plan(self.root, changes)
        before = (self.root / "scripts/20.js").read_text()
        link = changes["guards"][0]["mapping"]["id"]
        with connection(self.root) as database:
            database.execute('UPDATE profile_rule_mapping SET "order"=? WHERE id=?', ("z9", link))
        with self.assertRaisesRegex(ValueError, "restore conflict"):
            write_plan(self.root, changes, restoring=True)
        self.assertEqual(before, (self.root / "scripts/20.js").read_text())


if __name__ == "__main__":
    unittest.main()
