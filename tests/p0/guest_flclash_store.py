"""Versioned native rule storage for FlClash 0.8.94/schema 2 and 0.9.2/schema 21."""

import sqlite3
from contextlib import contextmanager


@contextmanager
def connection(root, readonly=False):
    database = sqlite3.connect(f"file:{root}/database.sqlite?mode={'ro' if readonly else 'rw'}", uri=True)
    try:
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys=ON")
        if version(database) not in (2, 21):
            raise ValueError("unsupported FlClash test schema")
        with database:
            yield database
    finally:
        database.close()


def version(database):
    return database.execute("PRAGMA user_version").fetchone()[0]


def profiles(database):
    fields = "id,type,extend_type,script_id" if version(database) == 21 else (
        "id,CASE WHEN url<>'' THEN 'url' ELSE 'file' END AS type,overwrite_type AS extend_type,script_id")
    return [dict(row) for row in database.execute(f"SELECT {fields} FROM profiles ORDER BY id")]


def make_guard(database, identifier, profile_id):
    row = {"id": identifier, "rule_action": "DOMAIN", "content": "managed.p0.invalid",
           "rule_target": "REJECT", "rule_provider": None, "sub_rule": None, "no_resolve": 0, "src": 0}
    if version(database) == 21:
        row.update({"profile_id": profile_id, "order": ""})
    else:
        scene = None if profile_id is None else "added"
        orders = [item[0] for item in database.execute(
            'SELECT "order" FROM profile_rule_mapping WHERE profile_id IS ? AND scene IS ?', (profile_id, scene)) if item[0]]
        rank = max(orders) + "V" if orders else "a0"
        key = "_".join(str(value) for value in (profile_id, identifier, scene) if value is not None)
        row["mapping"] = {"id": key, "profile_id": profile_id, "rule_id": identifier, "scene": scene, "order": rank}
    return row


def read_guard(database, expected):
    rule = database.execute("SELECT * FROM rules WHERE id=?", (expected["id"],)).fetchone()
    link = None
    if "mapping" in expected:
        link = database.execute("SELECT * FROM profile_rule_mapping WHERE id=?", (expected["mapping"]["id"],)).fetchone()
    return (dict(rule) if rule else None, dict(link) if link else None)


def matches(actual, expected):
    rule, link = actual
    return rule == {key: value for key, value in expected.items() if key != "mapping"} and link == expected.get("mapping")


def insert(database, table, row):
    allowed = {"rules": {"id", "profile_id", "rule_action", "content", "rule_target", "rule_provider", "sub_rule", "no_resolve", "src", "order"},
               "profile_rule_mapping": {"id", "profile_id", "rule_id", "scene", "order"}}
    if table not in allowed or not set(row) <= allowed[table]:
        raise ValueError("unsupported native record fields")
    columns = list(row)
    names = ",".join('"' + name + '"' for name in columns)
    database.execute(f"INSERT INTO {table}({names}) VALUES({','.join('?' for _ in columns)})",
                     [row[name] for name in columns])


def write_guard(database, row, restoring=False):
    current = read_guard(database, row)
    if restoring:
        if any(item is not None for item in current) and not matches(current, row):
            raise ValueError("restore conflict: native guard changed during write")
        if "mapping" in row:
            database.execute("DELETE FROM profile_rule_mapping WHERE id=?", (row["mapping"]["id"],))
        database.execute("DELETE FROM rules WHERE id=?", (row["id"],))
    else:
        if any(item is not None for item in current):
            raise ValueError("native guard ID was taken")
        insert(database, "rules", {key: value for key, value in row.items() if key != "mapping"})
        if "mapping" in row:
            insert(database, "profile_rule_mapping", row["mapping"])
