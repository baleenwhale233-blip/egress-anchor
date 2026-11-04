"""Stopped-client SQLite prototype for synthetic FlClash profiles in the P0 VM."""

import argparse
import json
from pathlib import Path
import platform
import secrets
import socket
import sqlite3
import time

from guest_overlay_common import atomic_write, digest, managed_script, run_client, stop_client


ROOT = Path("/home/egress-test/.local/share/com.follow.clash")
FIXTURES = Path("/home/egress-test/p0-fixtures")
RECEIPT = FIXTURES / "flclash-overlay-receipt.json"
SCRIPT_ID = -9002
SCRIPT = ROOT / f"scripts/{SCRIPT_ID}.js"
PREFS = ROOT / "shared_preferences.json"


def connection(readonly=False):
    mode = "ro" if readonly else "rw"
    return sqlite3.connect(f"file:{ROOT}/database.sqlite?mode={mode}", uri=True)


def current_profile():
    prefs = json.loads(json.loads(PREFS.read_text())["flutter.config"])
    with connection(True) as database:
        return database.execute("SELECT id,extend_type,script_id FROM profiles WHERE id=?",
                                (prefs["currentProfileId"],)).fetchone()


def seed_user():
    profile = current_profile()
    if profile[1:] != ("standard", None):
        raise ValueError("seed only a fresh standard fixture profile")
    stop_client("flclash")
    with connection() as database:
        database.execute("INSERT INTO rules(id,profile_id,rule_action,content,rule_target) VALUES(?,?,?,?,?)",
                         (-9001, profile[0], "DOMAIN", "user-added.p0.invalid", "DIRECT"))
    raw = json.loads(PREFS.read_text())
    prefs = json.loads(raw["flutter.config"])
    prefs["networkProps"]["systemProxy"] = False
    prefs["patchClashConfig"]["external-controller"] = "127.0.0.1:9090"
    prefs["patchClashConfig"]["secret"] = secrets.token_hex(24)
    raw["flutter.config"] = json.dumps(prefs)
    atomic_write(PREFS, json.dumps(raw))
    run_client("start", "flclash")
    print(json.dumps({"seeded_standard_user_rule": True, "guest_loopback_test_api": True}))


def install(preserve):
    if RECEIPT.exists() and json.loads(RECEIPT.read_text())["status"] == "installed":
        raise ValueError("restore the fixture overlay before installing again")
    profile = current_profile()
    if profile[1:] != ("standard", None):
        raise ValueError("this P0 prototype supports only a synthetic standard profile")
    with connection(True) as database:
        rules = database.execute("SELECT rule_action,content,rule_target FROM rules WHERE profile_id=? OR profile_id IS NULL",
                                 (profile[0],)).fetchall()
        if database.execute("SELECT id FROM scripts WHERE id=?", (SCRIPT_ID,)).fetchone() or SCRIPT.exists():
            raise ValueError("fixture script ID already exists")
    if rules != [("DOMAIN", "user-added.p0.invalid", "DIRECT")]:
        raise ValueError("this experiment translates only the known synthetic standard rule")
    user = 'function main(config) { return config; }'
    if preserve:
        user = 'function main(config) { config.rules = ["DOMAIN,user-added.p0.invalid,DIRECT"].concat(config.rules || []); return config; }'
    port = json.loads((FIXTURES / "ports.json").read_text())["landing"]
    script = managed_script(user, port)
    receipt = {"client": "flclash-p0", "profile_id": profile[0], "before": list(profile[1:]),
               "script_sha256": digest(script), "translated_fixture_rule": preserve, "status": "prepared"}
    atomic_write(RECEIPT, json.dumps(receipt))
    stop_client("flclash")
    SCRIPT.parent.mkdir(exist_ok=True)
    atomic_write(SCRIPT, script)
    with connection() as database:
        database.execute("INSERT INTO scripts(id,label,last_update_time) VALUES(?,?,?)",
                         (SCRIPT_ID, "EA-P0-owned", int(time.time())))
        updated = database.execute("UPDATE profiles SET extend_type='script',script_id=? WHERE id=? AND extend_type='standard' AND script_id IS NULL",
                                   (SCRIPT_ID, profile[0]))
        if updated.rowcount != 1:
            raise ValueError("fixture profile changed; transaction rolled back")
    receipt["status"] = "installed"
    atomic_write(RECEIPT, json.dumps(receipt))
    run_client("start", "flclash")
    print(json.dumps({"installed_profile_script": True, "translated_fixture_rule": preserve}))


def restore():
    receipt = json.loads(RECEIPT.read_text())
    if receipt["client"] != "flclash-p0":
        raise ValueError("wrong fixture receipt")
    if receipt["status"] == "restored":
        print(json.dumps({"already_restored": True}))
        return
    with connection(True) as database:
        current = database.execute("SELECT extend_type,script_id FROM profiles WHERE id=?",
                                   (receipt["profile_id"],)).fetchone()
    if current != ("script", SCRIPT_ID) or digest(SCRIPT.read_text()) != receipt["script_sha256"]:
        raise ValueError("restore conflict: user edits preserved; client not stopped")
    stop_client("flclash")
    if digest(SCRIPT.read_text()) != receipt["script_sha256"]:
        raise ValueError("script changed during stop; nothing was overwritten")
    with connection() as database:
        updated = database.execute("UPDATE profiles SET extend_type=?,script_id=? WHERE id=? AND extend_type='script' AND script_id=?",
                                   (*receipt["before"], receipt["profile_id"], SCRIPT_ID))
        if updated.rowcount != 1:
            raise ValueError("profile changed during stop; transaction rolled back")
        database.execute("DELETE FROM scripts WHERE id=? AND label=?", (SCRIPT_ID, "EA-P0-owned"))
    SCRIPT.unlink()
    receipt["status"] = "restored"
    atomic_write(RECEIPT, json.dumps(receipt))
    run_client("start", "flclash")
    print(json.dumps({"restored_only_owned_profile_fields": True, "native_client_restarted": True}))


def main():
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("This fixture may run only inside the disposable P0 guest")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["seed-user", "install", "install-naive", "restore"])
    action = parser.parse_args().action
    if action == "seed-user":
        seed_user()
    elif action.startswith("install"):
        install(action == "install")
    else:
        restore()


if __name__ == "__main__":
    main()
