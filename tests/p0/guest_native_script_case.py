"""Create one synthetic existing-user script while its test client is stopped."""

import argparse
import json
from pathlib import Path
import platform
import socket
import time

from guest_client_context import FLCLASH, flclash_context
from guest_flclash_store import connection, profiles, version
from guest_overlay_common import atomic_write, digest
from guest_flclash_guards import write_plan


SOURCE = """function main(config) {
  config.rules = ["DOMAIN,managed.p0.invalid,DIRECT", "DOMAIN,user-added.p0.invalid,DIRECT"].concat(config.rules || []);
  return config;
}
"""


def main():
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("Use only the disposable P0 guest")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=list(FLCLASH), required=True)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    client = args.client
    context = flclash_context(client)
    pid = (context["desktop"] / f"{client}.pid").read_text()
    if Path(f"/proc/{pid}/exe").exists():
        raise ValueError("stop the test client before seeding this user case")
    root = context["root"]
    if args.cleanup:
        journal = json.loads((context["fixtures"] / "original-user-script-case.json").read_text())
        receipt_path = context["fixtures"] / "flclash-overlay-receipt.json"
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if receipt.get("status") != "restored":
                write_plan(root, receipt["changes"], restoring=True)
                receipt["status"], receipt["recovery_runtime_verified"] = "restored", False
                atomic_write(receipt_path, json.dumps(receipt))
        script = root / "scripts/-9701.js"
        if digest(script.read_text()) != journal["source_sha256"]:
            raise ValueError("synthetic original script changed; cleanup refused")
        with connection(root) as database:
            selected = next(row for row in profiles(database) if row["id"] == journal["profile"]["id"])
            if selected["extend_type"] != "script" or selected["script_id"] != -9701:
                raise ValueError("synthetic profile changed; cleanup refused")
            column = "extend_type" if version(database) == 21 else "overwrite_type"
            database.execute(f"UPDATE profiles SET {column}=?,script_id=? WHERE id=?",
                             (journal["profile"]["extend_type"], journal["profile"]["script_id"], selected["id"]))
            database.execute("DELETE FROM scripts WHERE id=-9701 AND label=?", ("Original synthetic user script",))
        script.unlink()
        print(json.dumps({"synthetic_script_case_cleaned": True, "native_runtime_not_yet_verified": True}))
        return
    prefs = json.loads(json.loads((root / "shared_preferences.json").read_text())["flutter.config"])
    script = root / "scripts/-9701.js"
    if script.exists():
        raise ValueError("the synthetic user script already exists")
    with connection(root) as database:
        selected = next(row for row in profiles(database) if row["id"] == prefs["currentProfileId"])
        if selected["extend_type"] != "standard" or selected["script_id"] is not None:
            raise ValueError("seed only a standard synthetic subscription")
        column = "extend_type" if version(database) == 21 else "overwrite_type"
        database.execute("INSERT INTO scripts(id,label,last_update_time) VALUES(?,?,?)",
                         (-9701, "Original synthetic user script", int(time.time())))
        database.execute(f"UPDATE profiles SET {column}='script',script_id=-9701 WHERE id=?", (selected["id"],))
    script.parent.mkdir(exist_ok=True)
    atomic_write(script, SOURCE)
    atomic_write(context["fixtures"] / "original-user-script-case.json",
                 json.dumps({"profile": selected, "script_id": -9701, "source_sha256": digest(SOURCE)}))
    print(json.dumps({"seeded_original_user_script": True, "profile_id": selected["id"], "source_sha256": digest(SOURCE)}))


if __name__ == "__main__":
    main()
