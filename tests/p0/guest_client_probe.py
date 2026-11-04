"""Observe only the named guest's native client and loopback test traffic."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import socket
import urllib.request

import yaml

from loopback_fixtures import ping, tunnel
from guest_core_api import request
from guest_client_context import FLCLASH, flclash_context
from guest_flclash_store import connection as flclash_database, profiles as flclash_profiles


FIXTURES = Path("/home/egress-test/p0-fixtures")


def connection_chain(rows, host, source_port):
    matches = [row for row in rows or []
               if row["metadata"].get("host") == host
               and row["metadata"].get("network") == "tcp"
               and str(row["metadata"].get("sourcePort")) == str(source_port)]
    if len(matches) != 1:
        raise AssertionError("could not identify the exact synthetic TCP connection")
    return matches[0]["chains"]


def native_state(client):
    base = Path("/home/egress-test/.local/share")
    if client == "verge":
        root = base / "io.github.clash-verge-rev.clash-verge-rev"
        runtime = yaml.safe_load((root / "clash-verge.yaml").read_text())
        metadata = yaml.safe_load((root / "profiles.yaml").read_text())
        selected = next(row for row in metadata["items"] if row.get("uid") == metadata["current"])
        return runtime, {"id": selected["uid"], "name": selected.get("name")}
    root = flclash_context(client)["root"]
    runtime = yaml.safe_load((root / "config.yaml").read_text())
    prefs = json.loads(json.loads((root / "shared_preferences.json").read_text())["flutter.config"])
    if prefs.get("networkProps", {}).get("systemProxy"):
        raise AssertionError("keep the guest's system proxy disabled")
    with flclash_database(root, True) as database:
        row = next(row for row in flclash_profiles(database) if row["id"] == prefs["currentProfileId"])
        label = database.execute("SELECT label FROM profiles WHERE id=?", (row["id"],)).fetchone()[0]
    return runtime, {"id": row["id"], "name": label, "extend_type": row["extend_type"], "script_id": row["script_id"]}


def observe(managed, expected_source=None, expected_route=None, client="verge", user_rule=False):
    expected_route = expected_route or ("managed" if managed else "source")
    runtime, selected = native_state(client)
    fixtures = FIXTURES if client == "verge" else flclash_context(client)["fixtures"]
    ports = json.loads((fixtures / "ports.json").read_text())

    def api(path):
        return request(runtime, client, "GET", path)

    proxies = api("/proxies")["proxies"]
    rules = api("/rules")["rules"]
    has_user_rule = any(row.get("payload") == "user-added.p0.invalid" and row.get("proxy") == "DIRECT"
                        for row in rules)
    if user_rule and not has_user_rule:
        raise AssertionError("the synthetic user's existing rule was lost")
    source_nodes = sorted(name for name in proxies if name.startswith(("airport-", "ordinary-")))
    if expected_source and source_nodes != [f"airport-{expected_source}", f"ordinary-{expected_source}"]:
        raise AssertionError(f"source version mismatch: {source_nodes}")
    own_names = sorted(name for name in proxies if name.startswith("EA-P0-"))
    if managed != bool(own_names):
        raise AssertionError(f"managed state mismatch: {own_names}")
    if runtime.get("tun", {}).get("enable"):
        raise AssertionError("the fixture must keep TUN disabled")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def events():
        with opener.open("http://127.0.0.1:18880/events", timeout=3) as response:
            return json.loads(response.read())
    start = len(events())
    with tunnel(runtime["mixed-port"], "managed.p0.invalid", ports["origin"]) as connection:
        ping(connection)
        observations = api("/connections")["connections"]
        chains = connection_chain(observations, "managed.p0.invalid", connection.getsockname()[1])
        route = "managed" if "EA-P0-Landing" in chains else ("direct" if "DIRECT" in chains else "source")
        if route != expected_route:
            raise AssertionError(f"unexpected actual chain: {chains}")
    traffic = events()[start:]
    if expected_route == "managed":
        assert any(row["proxy"] in ("front", "ordinary") and row["port"] == ports["landing"] for row in traffic)
        assert any(row["proxy"] == "landing" and row["port"] == ports["origin"] for row in traffic)
    else:
        assert not any(row["proxy"] == "landing" for row in traffic)
        if expected_route == "direct":
            assert not traffic
    start = len(events())
    with tunnel(runtime["mixed-port"], "unmanaged.p0.invalid", ports["origin"]) as connection:
        ping(connection)
        observations = api("/connections")["connections"]
        unrelated_chain = connection_chain(observations, "unmanaged.p0.invalid", connection.getsockname()[1])
        assert "OriginalDefault" in unrelated_chain
    unrelated_traffic = events()[start:]
    assert any(row["proxy"] == "ordinary" and row["port"] == ports["origin"] for row in unrelated_traffic)
    assert not any(row["proxy"] == "landing" for row in unrelated_traffic)
    return {
        "platform": "Ubuntu 24.04 ARM64 VM, offline, TUN disabled",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "client": "Clash Verge Rev 2.5.10" if client == "verge" else flclash_context(client)["label"],
        "core": api("/version"), "current_profile": selected, "user_rule_present": has_user_rule,
        "source_nodes": source_nodes, "fixture_proxy_events": traffic,
        "managed_objects": own_names, "actual_chain": chains, "observed_route": route,
        "unmanaged_chain": unrelated_chain, "unmanaged_proxy_events": unrelated_traffic,
        "rules": [{key: row.get(key) for key in ("type", "payload", "proxy")}
                  for row in rules if "p0.invalid" in str(row.get("payload", ""))],
    }


def main():
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("This fixture may run only inside the disposable P0 guest")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label")
    parser.add_argument("--client", choices=["verge", *FLCLASH], default="verge")
    parser.add_argument("--managed", action="store_true")
    parser.add_argument("--source", help="expected fixture suffix, e.g. a-v2")
    parser.add_argument("--route", choices=["managed", "source", "direct"], help="negative control may override the installed overlay")
    parser.add_argument("--user-rule", action="store_true", help="require the original synthetic user's rule")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9_-]+", args.label):
        raise ValueError("use a fixture report label")
    directory = (FIXTURES if args.client == "verge" else flclash_context(args.client)["fixtures"]) / "reports"
    directory.mkdir(exist_ok=True)
    try:
        report = observe(args.managed, args.source, args.route, args.client, args.user_rule)
    except Exception as error:
        report = {"checked_at": datetime.now(timezone.utc).isoformat(),
                  "error": f"{type(error).__name__}: {error}"}
        (directory / f"{args.label}.json").write_text(json.dumps(report, indent=2) + "\n")
        raise
    (directory / f"{args.label}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
