"""Follow native guarded rebuilds; new flows stay rejected before each hot activation."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import sys
import time

from guest_client_probe import native_state
from guest_core_api import request
from guest_flclash_guards import check_installed
from guest_flclash_runtime import apply
from guest_client_context import FLCLASH, flclash_context


ROOT = Path("/home/egress-test/.local/share/com.follow.clash")
FIXTURES = Path("/home/egress-test/p0-fixtures")
PID = FIXTURES / "flclash-follow.pid"
CLIENT = "flclash"


def run():
    previous, previous_event = None, None
    while True:
        try:
            receipt = json.loads((FIXTURES / "flclash-overlay-receipt.json").read_text())
            if receipt.get("status") == "restored":
                return
            if receipt.get("version") != 2 or receipt.get("status") != "installed":
                raise ValueError("native guard receipt is not ready")
            info = (ROOT / "config.yaml").stat()
            signature = (info.st_mtime_ns, info.st_size)
            if signature == previous:
                time.sleep(0.2)
                continue
            runtime, selected = native_state(CLIENT)
            check_installed(ROOT, receipt["changes"])
            rules = request(runtime, CLIENT, "GET", "/rules")["rules"]
            service = next((row for row in rules if row.get("payload") == "managed.p0.invalid"), {})
            if service.get("proxy") == "REJECT":
                result = apply(ROOT, receipt["port"], CLIENT)
                event = {"state": "activated_after_native_guard", "profile_id": selected["id"], **result}
            elif service.get("proxy") == "EA-P0-Managed":
                event = {"state": "already_active", "profile_id": selected["id"]}
            else:
                raise ValueError("protected native rule is missing; scope is unsupported")
            info = (ROOT / "config.yaml").stat()
            previous = (info.st_mtime_ns, info.st_size)
        except (OSError, ValueError, TypeError, KeyError, AssertionError) as error:
            event = {"state": "needs_verification", "error": str(error)}
        if event != previous_event:
            row = {"checked_at": datetime.now(timezone.utc).isoformat(), **event}
            with (FIXTURES / "flclash-follow-events.jsonl").open("a") as stream:
                stream.write(json.dumps(row) + "\n")
            previous_event = event
        time.sleep(0.2)


def main():
    global ROOT, FIXTURES, PID, CLIENT
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("This fixture may run only inside the disposable P0 guest")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "run"])
    parser.add_argument("--client", choices=list(FLCLASH), default="flclash")
    args = parser.parse_args()
    action, CLIENT = args.action, args.client
    context = flclash_context(CLIENT)
    ROOT, FIXTURES = context["root"], context["fixtures"]
    PID = FIXTURES / "flclash-follow.pid"
    if action == "run":
        run()
        return
    if PID.exists():
        pid = int(PID.read_text())
        executable = Path(f"/proc/{pid}/exe")
        if executable.exists():
            arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if executable.resolve() != Path(sys.executable).resolve() or str(Path(__file__).resolve()).encode() not in arguments:
                raise ValueError("refusing to control a process not owned by this follower")
            if action == "start":
                print(json.dumps({"reused_test_follower": True, "pid": pid}))
                return
            os.kill(pid, signal.SIGTERM)
            print(json.dumps({"stopped_test_follower": True}))
            return
    if action == "stop":
        print(json.dumps({"already_stopped": True}))
        return
    with (FIXTURES / "flclash-follow.log").open("a") as log:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "run", "--client", CLIENT],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    PID.write_text(str(child.pid))
    print(json.dumps({"started_test_follower": True, "pid": child.pid}))


if __name__ == "__main__":
    main()
