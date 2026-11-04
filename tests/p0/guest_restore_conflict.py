"""Verify that our guest-only restore prototypes refuse a later user script edit."""

import argparse
import json
from pathlib import Path
import platform
import socket
import subprocess
import sys

import yaml

from guest_client_context import FLCLASH, flclash_context
from guest_flclash_store import connection as flclash_database, read_guard, matches


def native_guard_conflict(client):
    context = flclash_context(client)
    root, fixtures = context["root"], context["fixtures"]
    receipt = json.loads((fixtures / "flclash-overlay-receipt.json").read_text())
    expected = receipt["changes"]["guards"][0]
    with flclash_database(root) as database:
        if not matches(read_guard(database, expected), expected):
            raise ValueError("fixture guard was already changed")
        database.execute("UPDATE rules SET rule_target='DIRECT' WHERE id=?", (expected["id"],))
    pid_path = context["desktop"] / f"{client}.pid"
    pid = pid_path.read_text()
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("guest_flclash_overlay.py")),
                                 "restore", "--client", client], capture_output=True, text=True, timeout=15)
        assert result.returncode != 0 and "restore conflict" in result.stderr
        with flclash_database(root, True) as database:
            assert database.execute("SELECT rule_target FROM rules WHERE id=?", (expected["id"],)).fetchone()[0] == "DIRECT"
        assert pid_path.read_text() == pid and Path(f"/proc/{pid}/exe").exists()
        report = {"restore_conflict_detected": True, "native_user_edit_preserved": True, "client_not_stopped": True}
        (fixtures / "reports" / f"{client}-restore-conflict-native-row.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        with flclash_database(root) as database:
            current, _ = read_guard(database, expected)
            before = {key: value for key, value in expected.items() if key != "mapping"}
            if current == {**before, "rule_target": "DIRECT"}:
                database.execute("UPDATE rules SET rule_target=? WHERE id=?", (expected["rule_target"], expected["id"]))


def main():
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("This fixture may run only inside the disposable P0 guest")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client", choices=["verge", *FLCLASH])
    parser.add_argument("--profile", action="store_true", help="edit the current Verge profile hook")
    args = parser.parse_args()
    client = args.client
    if client != "verge":
        native_guard_conflict(client)
        return
    base = Path("/home/egress-test")
    script = base / (".local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/Script.js"
                     if client == "verge" else ".local/share/com.follow.clash/scripts/-9002.js")
    if args.profile:
        if client != "verge":
            raise ValueError("profile hook selection is only for the Verge fixture")
        root = script.parent.parent
        metadata = yaml.safe_load((root / "profiles.yaml").read_text())
        selected = next(row for row in metadata["items"] if row.get("uid") == metadata["current"])
        script = root / "profiles" / (selected["option"]["script"] + ".js")
    before = script.read_text()
    changed = before + "\n// synthetic late user edit\n"
    pid_path = base / f"p0-desktop/{client}.pid"
    pid = pid_path.read_text()
    try:
        script.write_text(changed)
        result = subprocess.run([sys.executable, str(Path(__file__).with_name(f"guest_{client}_overlay.py")), "restore"],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode != 0 and "restore conflict" in result.stderr
        assert script.read_text() == changed
        assert pid_path.read_text() == pid and Path(f"/proc/{pid}/exe").exists()
        report = {"restore_conflict_detected": True, "late_user_edit_preserved": True, "client_not_stopped": True}
        suffix = "-profile" if args.profile else ""
        (base / f"p0-fixtures/reports/{client}-restore-conflict{suffix}.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        if script.read_text() == changed:
            script.write_text(before)


if __name__ == "__main__":
    main()
