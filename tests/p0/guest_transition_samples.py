"""Sample every successful service flow during an operator-driven native rebuild."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import socket
import time

from guest_client_context import FLCLASH, flclash_context
from guest_client_probe import connection_chain, native_state
from guest_core_api import request
from loopback_fixtures import ping, tunnel


def main():
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("This fixture may run only inside the disposable P0 guest")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=list(FLCLASH), default="flclash")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--label", default="next-flclash-transition")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 60 or not args.label.replace("-", "").isalnum():
        raise ValueError("use a short bounded sampling period and report label")
    context = flclash_context(args.client)
    runtime, selected = native_state(args.client)
    ports = json.loads((context["fixtures"] / "ports.json").read_text())
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "client": context["label"],
              "initial_profile": selected, "through_landing": 0, "blocked": 0,
              "unknown_successes": 0, "bypasses": [], "unrelated_live_connection_preserved": True}
    end = time.monotonic() + args.seconds
    with tunnel(runtime["mixed-port"], "unmanaged.p0.invalid", ports["origin"]) as unrelated:
        ping(unrelated)
        while time.monotonic() < end:
            try:
                with tunnel(runtime["mixed-port"], "managed.p0.invalid", ports["origin"]) as flow:
                    ping(flow)
                    try:
                        rows = request(runtime, args.client, "GET", "/connections")["connections"]
                        chain = connection_chain(rows, "managed.p0.invalid", flow.getsockname()[1])
                    except (AssertionError, OSError, ValueError):
                        report["unknown_successes"] += 1
                    else:
                        if "EA-P0-Landing" in chain:
                            report["through_landing"] += 1
                        else:
                            report["bypasses"].append(chain)
            except OSError:
                report["blocked"] += 1
            try:
                ping(unrelated)
            except OSError:
                report["unrelated_live_connection_preserved"] = False
            time.sleep(0.03)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    path = context["fixtures"] / "reports" / f"{args.label}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    if report["bypasses"] or report["unknown_successes"] or not report["through_landing"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
