"""P0 file-overlay/restore prototype; refuses to run outside the disposable guest."""

import argparse
import json
from pathlib import Path
import platform
import socket

import yaml

from guest_overlay_common import atomic_write, run_client, stop_client
from guest_verge_files import plan, preflight, write_plan
from guest_verge_runtime import update


ROOT = Path("/home/egress-test/.local/share/io.github.clash-verge-rev.clash-verge-rev")
FIXTURES = Path("/home/egress-test/p0-fixtures")
SCRIPT = ROOT / "profiles/Script.js"
RECEIPT = FIXTURES / "verge-overlay-receipt.json"
PROFILE_RECEIPT = FIXTURES / "verge-profile-conflict-receipt.json"


def install():
    if RECEIPT.exists() and json.loads(RECEIPT.read_text()).get("status") != "restored":
        raise ValueError("restore the existing fixture overlay before installing again")
    port = json.loads((FIXTURES / "ports.json").read_text())["landing"]
    changes = plan(ROOT, port)
    receipt = {"version": 2, "client": "verge-p0", "files": changes, "port": port, "status": "prepared"}
    atomic_write(RECEIPT, json.dumps(receipt))
    write_plan(ROOT, changes)
    result = update(ROOT, "apply", port)
    receipt["status"] = "installed"
    atomic_write(RECEIPT, json.dumps(receipt))
    print(json.dumps({"installed_fixture_overlay": True, "preserved_extension_files": len(changes), **result}))


def restore():
    receipt = json.loads(RECEIPT.read_text())
    if receipt.get("client") != "verge-p0":
        raise ValueError("receipt belongs to a different fixture")
    if receipt["status"] == "restored":
        print(json.dumps({"already_restored": True}))
        return
    if receipt.get("version") != 2:
        raise ValueError("restore the legacy fixture receipt with its matching prototype")
    preflight(ROOT, receipt["files"], restoring=True)
    runtime = yaml.safe_load((ROOT / "clash-verge.yaml").read_text())
    own_guard_only = any(row.get("name") == "EA-P0-Deny" for row in runtime.get("proxy-groups", [])) and not any(
        row.get("name") == "EA-P0-Landing" for row in runtime.get("proxies", []))
    rebuild = runtime.get("x-ea-p0-state") in ("unregistered", "script-error", "global-hook-failed") or own_guard_only
    write_plan(ROOT, receipt["files"], restoring=True)
    receipt["status"] = "files_restored"
    atomic_write(RECEIPT, json.dumps(receipt))
    if rebuild:
        stop_client("verge")
        run_client("start", "verge")
        result = {"native_client_restarted": True, "hot_restored": False, "runtime_verification_required": True}
    else:
        result = update(ROOT, "restore", receipt["port"])
    receipt["status"] = "restored"
    atomic_write(RECEIPT, json.dumps(receipt))
    print(json.dumps({"restored_owned_extensions": True, **result}))


def reconcile():
    receipt = json.loads(RECEIPT.read_text())
    if receipt.get("version") != 2 or receipt["status"] != "installed":
        raise ValueError("install the current fixture before reconciling new subscriptions")
    for row in receipt["files"]:
        if (ROOT / row["path"]).read_text() != row["after"]:
            raise ValueError("restore conflict: a mounted extension changed")
    originals = {row["path"]: row["before"] for row in receipt["files"]}
    changes = plan(ROOT, receipt["port"], originals)
    old = {row["path"]: row for row in receipt["files"]}
    if any(row["path"] in old and row["after"] != old[row["path"]]["after"] for row in changes):
        raise ValueError("existing hooks need a separate migration")
    additions = [row for row in changes if row["path"] not in old]
    if not additions:
        print(json.dumps({"already_registered": True}))
        return
    runtime = yaml.safe_load((ROOT / "clash-verge.yaml").read_text())
    restart = runtime.get("x-ea-p0-state") == "unregistered"
    receipt["files"], receipt["status"] = changes, "prepared"
    atomic_write(RECEIPT, json.dumps(receipt))
    write_plan(ROOT, additions)
    # Rebuild through the client when the current profile stopped before user scripts.
    # This preserves original global/merge semantics instead of compiling a partial snapshot.
    if restart:
        stop_client("verge")
        run_client("start", "verge")
    receipt["status"] = "installed"
    atomic_write(RECEIPT, json.dumps(receipt))
    print(json.dumps({"registered_extension_files": len(additions), "native_client_restarted": restart,
                      "runtime_verification_required": True}))


def profile_conflict(enable):
    if enable:
        if PROFILE_RECEIPT.exists():
            raise ValueError("remove the previous synthetic profile conflict first")
        metadata = yaml.safe_load((ROOT / "profiles.yaml").read_text())
        selected = next(row for row in metadata["items"] if row.get("uid") == metadata["current"])
        path = ROOT / "profiles" / (selected["option"]["script"] + ".js")
        before = path.read_text()
        after = 'function main(config) {\n  config.rules = ["DOMAIN,managed.p0.invalid,DIRECT"].concat(config.rules || []);\n  return config;\n}\n'
        atomic_write(PROFILE_RECEIPT, json.dumps({"path": str(path), "before": before, "after": after}))
        atomic_write(path, after)
    else:
        receipt = json.loads(PROFILE_RECEIPT.read_text())
        path = Path(receipt["path"])
        if path.read_text() != receipt["after"]:
            raise ValueError("profile script changed; synthetic conflict was not removed")
        atomic_write(path, receipt["before"])
        PROFILE_RECEIPT.unlink()
    print(json.dumps({"synthetic_profile_conflict": enable, "native_reload_required": True}))


def main():
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("This fixture may run only inside the disposable P0 guest")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["seed-user", "install", "reconcile", "restore", "profile-conflict-on", "profile-conflict-off"])
    args = parser.parse_args()
    if args.action == "seed-user":
        atomic_write(SCRIPT, 'function main(config, profileName) {\n  config.rules = ["DOMAIN,user-added.p0.invalid,DIRECT"].concat(config.rules || []);\n  return config;\n}\n')
        print(json.dumps({"seeded_synthetic_user_rule": True}))
    elif args.action == "install":
        install()
    elif args.action == "reconcile":
        reconcile()
    elif args.action.startswith("profile-conflict-"):
        profile_conflict(args.action.endswith("-on"))
    else:
        restore()


if __name__ == "__main__":
    main()
