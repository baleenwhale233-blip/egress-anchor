"""P0 file-overlay/restore prototype; refuses to run outside the disposable guest."""

import argparse
import json
from pathlib import Path
import platform
import socket

import yaml

from guest_overlay_common import atomic_write
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
    write_plan(ROOT, receipt["files"], restoring=True)
    receipt["status"] = "files_restored"
    atomic_write(RECEIPT, json.dumps(receipt))
    result = update(ROOT, "restore", receipt["port"])
    receipt["status"] = "restored"
    atomic_write(RECEIPT, json.dumps(receipt))
    print(json.dumps({"restored_owned_extensions": True, **result}))


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
    parser.add_argument("action", choices=["seed-user", "install", "restore", "profile-conflict-on", "profile-conflict-off"])
    args = parser.parse_args()
    if args.action == "seed-user":
        atomic_write(SCRIPT, 'function main(config, profileName) {\n  config.rules = ["DOMAIN,user-added.p0.invalid,DIRECT"].concat(config.rules || []);\n  return config;\n}\n')
        print(json.dumps({"seeded_synthetic_user_rule": True}))
    elif args.action == "install":
        install()
    elif args.action.startswith("profile-conflict-"):
        profile_conflict(args.action.endswith("-on"))
    else:
        restore()


if __name__ == "__main__":
    main()
