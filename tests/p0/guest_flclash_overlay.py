"""Stopped-client SQLite prototype for synthetic FlClash profiles in the P0 VM."""

import argparse
import json
from pathlib import Path
import platform
import secrets
import socket

import yaml

from guest_overlay_common import atomic_write, run_client, stop_client
from guest_flclash_guards import plan, preflight, write_plan, check_installed
from guest_flclash_runtime import apply, running, startup_status, ensure_ready
from guest_flclash_store import connection as native_database, profiles, make_guard, write_guard
from guest_client_context import FLCLASH, flclash_context


ROOT = Path("/home/egress-test/.local/share/com.follow.clash")
FIXTURES = Path("/home/egress-test/p0-fixtures")
RECEIPT = FIXTURES / "flclash-overlay-receipt.json"
PREFS = ROOT / "shared_preferences.json"
CLIENT = "flclash"


def current_profile():
    prefs = json.loads(json.loads(PREFS.read_text())["flutter.config"])
    with native_database(ROOT, True) as database:
        row = next(row for row in profiles(database) if row["id"] == prefs["currentProfileId"])
    return row["id"], row["extend_type"], row["script_id"]


def seed_user():
    profile = current_profile()
    if profile[1:] != ("standard", None):
        raise ValueError("seed only a fresh standard fixture profile")
    stop_client(CLIENT)
    with native_database(ROOT) as database:
        row = make_guard(database, -9001, profile[0])
        row.update({"content": "user-added.p0.invalid", "rule_target": "DIRECT"})
        if "order" in row:
            row["order"] = "a0"
        write_guard(database, row)
    raw = json.loads(PREFS.read_text())
    prefs = json.loads(raw["flutter.config"])
    prefs.setdefault("networkProps", {})["systemProxy"] = False
    prefs.setdefault("patchClashConfig", {})["external-controller"] = "127.0.0.1:9090"
    prefs["patchClashConfig"]["secret"] = secrets.token_hex(24)
    raw["flutter.config"] = json.dumps(prefs)
    atomic_write(PREFS, json.dumps(raw))
    run_client("start", CLIENT)
    print(json.dumps({"seeded_standard_user_rule": True, "guest_loopback_test_api": True}))


def install():
    if RECEIPT.exists() and json.loads(RECEIPT.read_text())["status"] != "restored":
        raise ValueError("restore the fixture overlay before installing again")
    port = json.loads((FIXTURES / "ports.json").read_text())["landing"]
    changes = plan(ROOT, port)
    previous = ensure_ready(ROOT, CLIENT, changes)
    receipt = {"client": "flclash-p0", "version": 2, "changes": changes,
               "port": port, "previous_running": previous, "status": "prepared"}
    atomic_write(RECEIPT, json.dumps(receipt))
    stop_client(CLIENT)
    write_plan(ROOT, changes)
    receipt["status"] = "installed"
    atomic_write(RECEIPT, json.dumps(receipt))
    run_client("start", CLIENT)
    print(json.dumps({"installed_native_guards": len(changes["guards"]),
                      "wrapped_existing_scripts": len(changes["scripts"]),
                      "profile_modes_preserved": True, **startup_status(ROOT, previous, CLIENT)}))


def restore():
    receipt = json.loads(RECEIPT.read_text())
    if receipt["client"] != "flclash-p0":
        raise ValueError("wrong fixture receipt")
    if receipt["status"] == "restored":
        print(json.dumps({"already_restored": True}))
        return
    if receipt.get("version") != 2:
        raise ValueError("restore the legacy receipt with its matching prototype")
    preflight(ROOT, receipt["changes"], restoring=True)
    previous = running(yaml.safe_load((ROOT / "config.yaml").read_text()) or {})
    stop_client(CLIENT)
    write_plan(ROOT, receipt["changes"], restoring=True)
    receipt["status"] = "files_restored"
    atomic_write(RECEIPT, json.dumps(receipt))
    run_client("start", CLIENT)
    status = startup_status(ROOT, previous, CLIENT)
    receipt["status"] = "restored"
    atomic_write(RECEIPT, json.dumps(receipt))
    print(json.dumps({"configuration_restored": True, "native_client_restarted": True, **status}))


def apply_current():
    receipt = json.loads(RECEIPT.read_text())
    if receipt.get("version") != 2 or receipt["status"] != "installed":
        raise ValueError("native guards must be installed first")
    check_installed(ROOT, receipt["changes"])
    print(json.dumps(apply(ROOT, receipt["port"], CLIENT)))


def main():
    global CLIENT, ROOT, FIXTURES, RECEIPT, PREFS
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("This fixture may run only inside the disposable P0 guest")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["seed-user", "install", "apply", "restore"])
    parser.add_argument("--client", choices=list(FLCLASH), default="flclash")
    args = parser.parse_args()
    action, CLIENT = args.action, args.client
    context = flclash_context(CLIENT)
    ROOT, FIXTURES = context["root"], context["fixtures"]
    RECEIPT, PREFS = FIXTURES / "flclash-overlay-receipt.json", ROOT / "shared_preferences.json"
    if action == "seed-user":
        seed_user()
    elif action == "install":
        install()
    elif action == "apply":
        apply_current()
    else:
        restore()


if __name__ == "__main__":
    main()
