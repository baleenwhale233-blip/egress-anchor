"""Owned native rule rows and existing script wrappers; never change profile modes."""

from guest_overlay_common import atomic_write, managed_script
from guest_flclash_store import connection, profiles, version, make_guard, read_guard, matches, write_guard


def plan(root, landing_port):
    guards, scripts = [], []
    with connection(root, True) as database:
        sources = profiles(database)
        if not sources or any(row["type"] not in ("url", "file") for row in sources):
            raise ValueError("only native URL/file subscriptions are supported by this experiment")
        identifiers = {row[0] for row in database.execute("SELECT id FROM rules")}
        if version(database) == 21 and database.execute('SELECT 1 FROM rules WHERE "order"=?', ("",)).fetchone():
            raise ValueError("empty native rule order needs separate handling")
        next_id = -980000000
        for profile_id in [None, *[row["id"] for row in sources if row["extend_type"] == "standard"]]:
            while next_id in identifiers:
                next_id -= 1
            guards.append(make_guard(database, next_id, profile_id))
            next_id -= 1
        for row in sources:
            if row["extend_type"] == "standard":
                continue
            if row["extend_type"] != "script" or row["script_id"] is None:
                raise ValueError("native script profile is not ready")
            if database.execute("SELECT id FROM scripts WHERE id=?", (row["script_id"],)).fetchone() is None:
                raise ValueError("native script metadata is missing")
            relative = f'scripts/{row["script_id"]}.js'
            if any(item["path"] == relative for item in scripts):
                continue
            path = root / relative
            if path.is_symlink() or not path.is_file():
                raise ValueError("native script must be an existing regular file")
            before = path.read_text()
            if "EA-P0-" in before:
                raise ValueError("fixture script namespace collision")
            scripts.append({"path": relative, "before": before,
                            "after": managed_script(before, landing_port)})
    return {"guards": guards, "scripts": scripts,
            "profiles": sources}


def preflight(root, changes, restoring=False):
    for row in changes["scripts"]:
        path = root / row["path"]
        if path.is_symlink() or path.resolve().parent != (root / "scripts").resolve():
            raise ValueError("script path left the fixture directory")
        allowed = (row["before"], row["after"]) if restoring else (row["before"],)
        if path.read_text() not in allowed:
            raise ValueError("restore conflict: native user script changed")
    with connection(root, True) as database:
        for row in changes["guards"]:
            current = read_guard(database, row)
            if any(item is not None for item in current) and (not restoring or not matches(current, row)):
                raise ValueError("restore conflict: native guard row changed")
        if not restoring:
            current = profiles(database)
            if current != changes["profiles"]:
                raise ValueError("native subscription set changed while preparing")


def write_plan(root, changes, restoring=False):
    preflight(root, changes, restoring)
    for row in changes["scripts"]:
        atomic_write(root / row["path"], row["before"] if restoring else row["after"])
    with connection(root) as database:
        for row in changes["guards"]:
            write_guard(database, row, restoring)


def check_installed(root, changes):
    for row in changes["scripts"]:
        if (root / row["path"]).read_text() != row["after"]:
            raise ValueError("mounted native script changed; protection needs verification")
    with connection(root, True) as database:
        for row in changes["guards"]:
            if not matches(read_guard(database, row), row):
                raise ValueError("native guard changed; protection needs verification")
        wrapped = {row["path"] for row in changes["scripts"]}
        for row in profiles(database):
            if row["type"] not in ("url", "file"):
                raise ValueError("new custom native profile is unsupported")
            if row["extend_type"] not in ("standard", "script"):
                raise ValueError("native custom overwrite is unsupported")
            if row["extend_type"] == "script" and f'scripts/{row["script_id"]}.js' not in wrapped:
                raise ValueError("new native script profile needs explicit registration")
