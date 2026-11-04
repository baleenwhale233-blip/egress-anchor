"""Inject owned-script failures and check actual TCP/UDP denial in the P0 guest."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import socket
import time

import yaml

from guest_client_probe import native_state
from guest_core_api import request
from guest_overlay_common import atomic_write, managed_script
from loopback_fixtures import fixture, ping, tunnel, udp_probe
from guest_client_context import FLCLASH, flclash_context


ROOT = Path("/home/egress-test/.local/share/io.github.clash-verge-rev.clash-verge-rev")
FIXTURES = Path("/home/egress-test/p0-fixtures")
JOURNAL = FIXTURES / "verge-error-injection.json"


def inject(case):
    if JOURNAL.exists():
        raise ValueError("clear the previous synthetic failure first")
    receipt = json.loads((FIXTURES / "verge-overlay-receipt.json").read_text())
    if receipt["status"] != "installed":
        raise ValueError("install the fixture overlay first")
    metadata = yaml.safe_load((ROOT / "profiles.yaml").read_text())
    selected = next(row for row in metadata["items"] if row.get("uid") == metadata["current"])
    paths = [ROOT / "profiles" / (selected["option"]["script"] + ".js")]
    if case.startswith("global-"):
        paths = [ROOT / "profiles/Script.js"]
    if case.startswith("native-"):
        paths.append(ROOT / "profiles/Script.js")
    user = "function main(config) { throw new Error('synthetic user failure'); }"
    if case == "user-syntax":
        user = "function main("
    text = managed_script(user, receipt["port"], stage="global" if case.startswith("global-") else "profile")
    if case in ("native-syntax", "global-syntax"):
        text = "function main("
    elif case == "native-limit":
        text = "function main(config) { while (true) {} return config; }"
    changes = [{"path": str(path), "before": path.read_text(), "after": text} for path in paths]
    atomic_write(JOURNAL, json.dumps({"case": case, "changes": changes}))
    for row in changes:
        atomic_write(Path(row["path"]), row["after"])
    print(json.dumps({"synthetic_failure": case, "native_reload_required": True}))


def clear():
    rows = json.loads(JOURNAL.read_text())["changes"]
    if any(Path(row["path"]).read_text() != row["after"] for row in rows):
        raise ValueError("a script changed; synthetic failure was not cleared")
    for row in rows:
        atomic_write(Path(row["path"]), row["before"])
    JOURNAL.unlink()
    print(json.dumps({"synthetic_failure_cleared": True, "native_reload_required": True}))


def verify(denied, label, client="verge"):
    runtime, selected = native_state(client)
    fixtures = FIXTURES if client == "verge" else flclash_context(client)["fixtures"]
    deadline = time.monotonic() + 15
    while True:
        proxies = request(runtime, client, "GET", "/proxies")["proxies"]
        rules = request(runtime, client, "GET", "/rules")["rules"]
        service = next((row for row in rules if row.get("payload") == "managed.p0.invalid"), {})
        if not denied or (service.get("proxy") in ("EA-P0-Deny", "REJECT") and "EA-P0-Landing" not in proxies):
            break
        if time.monotonic() > deadline:
            raise AssertionError("native regeneration did not reach the expected denied state")
        time.sleep(0.2)
    ports = json.loads((fixtures / "ports.json").read_text())
    if denied:
        assert service["proxy"] in ("EA-P0-Deny", "REJECT")
        try:
            with tunnel(runtime["mixed-port"], "managed.p0.invalid", ports["origin"]) as connection:
                ping(connection)
        except OSError:
            pass
        else:
            raise AssertionError("protected TCP bypassed the failed script")
    with tunnel(runtime["mixed-port"], "unmanaged.p0.invalid", ports["origin"]) as connection:
        ping(connection)
    with fixture("udp") as port:
        assert udp_probe(runtime["mixed-port"], "unmanaged.p0.invalid", port), "UDP positive control failed"
        assert not udp_probe(runtime["mixed-port"], "managed.p0.invalid", port), "protected UDP bypassed the guard"
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "current_profile": selected,
              "protected_tcp_denied": denied, "protected_udp_denied": True,
              "unrelated_tcp_works": True, "unrelated_udp_positive_control": True,
              "managed_objects": sorted(name for name in proxies if name.startswith("EA-P0-"))}
    (fixtures / "reports" / f"{label}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


def main():
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("This fixture may run only inside the disposable P0 guest")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["inject", "clear", "verify-denied", "verify-udp"])
    parser.add_argument("value", nargs="?")
    parser.add_argument("--client", choices=["verge", *FLCLASH], default="verge")
    args = parser.parse_args()
    if args.action == "inject":
        if args.client != "verge":
            raise ValueError("script fault injection is limited to the Verge fixture")
        if args.value not in ("user-throw", "user-syntax", "native-syntax", "native-limit", "global-user-throw", "global-syntax"):
            raise ValueError("use a fixed synthetic failure case")
        inject(args.value)
    elif args.action == "clear":
        clear()
    else:
        if not args.value or not args.value.replace("-", "").isalnum():
            raise ValueError("use an alphanumeric report label")
        verify(args.action == "verify-denied", args.value, args.client)


if __name__ == "__main__":
    main()
