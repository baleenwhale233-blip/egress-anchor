"""Opt-in P0 traffic checks. Never imports installed client profiles or changes TUN."""

import argparse
from copy import deepcopy
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kernel_session import KernelSession
from gpt_strict_checks import check_gpt_strict
from loopback_fixtures import fixture, free_port, ping, tunnel, udp_probe


def source(front, ordinary):
    return {
        "hosts": {f"{name}.p0.invalid": "127.0.0.1" for name in ("claude", "chatgpt", "direct")},
        "proxies": [
            {"name": "front", "type": "http", "server": "127.0.0.1", "port": front},
            {"name": "ordinary", "type": "http", "server": "127.0.0.1", "port": ordinary},
        ],
        "proxy-groups": [
            {"name": "AI", "type": "select", "proxies": ["front"]},
            {"name": "Other", "type": "select", "proxies": ["ordinary"]},
        ],
        "rules": ["DOMAIN,claude.p0.invalid,AI", "DOMAIN,chatgpt.p0.invalid,Other",
                  "DOMAIN,direct.p0.invalid,DIRECT", "MATCH,DIRECT"],
    }


def run(binary, checks):
    events = []
    with ExitStack() as stack:
        ports = {name: stack.enter_context(fixture(kind, name, events)) for name, kind in [
            ("origin", "echo"), ("udp", "udp"), ("front", "http"),
            ("ordinary", "http"), ("landing-a", "http"), ("landing-b", "http"), ("socks", "socks5"),
        ]}
        core = KernelSession(binary)
        stack.callback(core.close)
        config = source(ports["front"], ports["ordinary"])
        landing = {"type": "http", "server": "127.0.0.1", "port": ports["landing-a"]}
        version = core.start(config, landing)
        checks.append({"name": "generated_config_accepted", "passed": True})
        for mode in ["source", "fixed", "automatic", "direct"]:
            front_hop = {"mode": mode, **({"proxy": "front"} if mode == "fixed" else {})}
            if mode == "automatic":
                front_hop["url"] = "http://127.0.0.1:1/p0-health"
            core.configuration(config, landing, front_hop=front_hop)
        checks.append({"name": "four_front_modes_config_check", "passed": True})
        claude = stack.enter_context(tunnel(core.mixed_port, "claude.p0.invalid", ports["origin"]))
        other = stack.enter_context(tunnel(core.mixed_port, "chatgpt.p0.invalid", ports["origin"]))
        ping(claude)
        ping(other)
        assert any(e["proxy"] == "front" and e["port"] == ports["landing-a"] for e in events)
        assert any(e["proxy"] == "landing-a" and e["port"] == ports["origin"] for e in events)
        checks.append({"name": "http_landing_uses_front_and_landing", "passed": True})

        connections = core.api("GET", "/connections")["connections"]
        claude_ids = [c["id"] for c in connections if c["metadata"].get("host") == "claude.p0.invalid"]
        other_ids = [c["id"] for c in connections if c["metadata"].get("host") == "chatgpt.p0.invalid"]
        assert claude_ids and other_ids, "connection metadata did not retain fixture domains"
        landing = {**landing, "port": ports["landing-b"]}
        core.reload(config, landing)
        ping(other)
        ping(claude)
        checks.append({"name": "reload_preserves_existing_service_connections", "passed": True})
        for connection_id in claude_ids:
            core.api("DELETE", f"/connections/{connection_id}")
        ping(other)
        assert claude.recv(1) == b"", "the old managed connection was not closed"
        with tunnel(core.mixed_port, "claude.p0.invalid", ports["origin"]) as replacement:
            ping(replacement)
        assert any(e["proxy"] == "landing-b" and e["port"] == ports["origin"] for e in events)
        remaining_ids = {c["id"] for c in core.api("GET", "/connections")["connections"]}
        assert set(other_ids) <= remaining_ids
        checks.append({"name": "selective_close_and_landing_switch", "passed": True})

        core.reload(config, {"type": "socks5", "server": "127.0.0.1", "port": ports["socks"], "udp": False})
        with tunnel(core.mixed_port, "claude.p0.invalid", ports["origin"]) as connection:
            ping(connection)
        assert any(e["proxy"] == "socks" and e["port"] == ports["origin"] for e in events)
        checks.append({"name": "socks_landing_uses_chain", "passed": True})

        core.reload(config, {**landing, "port": free_port()})
        start = len(events)
        failed = False
        try:
            with tunnel(core.mixed_port, "claude.p0.invalid", ports["origin"]) as connection:
                ping(connection)
        except OSError:
            failed = True
        assert failed, "TCP succeeded with an unavailable required landing"
        assert not any(e["port"] == ports["origin"] for e in events[start:])
        ping(other)
        checks.append({"name": "tcp_landing_failure_does_not_bypass", "passed": True})

        empty_source = deepcopy(config)
        empty_source["proxy-providers"] = {"empty": {"type": "inline", "payload": []}}
        empty_source["proxy-groups"][0] = {"name": "AI", "type": "select", "use": ["empty"]}
        try:
            core.configuration(empty_source, landing, front_hop={"mode": "source"})
        except AssertionError as error:
            assert "file doesn't have any proxy" in str(error)
        else:
            raise AssertionError("expected the pinned core to reject an empty inline provider")
        checks.append({"name": "empty_provider_config_is_rejected", "passed": True})
        filtered_source = deepcopy(empty_source)
        filtered_source["proxy-providers"]["empty"]["payload"] = [config["proxies"][0]]
        filtered_source["proxy-groups"][0]["filter"] = "^no-fixture-candidate$"
        core.reload(filtered_source, landing, front_hop={"mode": "source"})
        failed = False
        try:
            with tunnel(core.mixed_port, "claude.p0.invalid", ports["origin"]) as connection:
                ping(connection)
        except OSError:
            failed = True
        assert failed, "empty provider bypassed the required front"
        ping(other)
        checks.append({"name": "filtered_empty_group_fails_closed", "passed": True})

        core.reload(config, landing)
        assert udp_probe(core.mixed_port, "direct.p0.invalid", ports["udp"]), "UDP positive control failed"
        checks.append({"name": "udp_direct_positive_control", "passed": True})
        udp_baseline = udp_probe(core.mixed_port, "claude.p0.invalid", ports["udp"])
        core.reload(config, landing, udp_guards=("claude.p0.invalid",))
        udp_guarded = udp_probe(core.mixed_port, "claude.p0.invalid", ports["udp"])
        assert not udp_guarded, "explicit service UDP rejection did not block the probe"
        checks.append({"name": "udp_without_explicit_guard", "passed": not udp_baseline,
                       "observation": "direct UDP reply observed" if udp_baseline else "no UDP reply"})
        checks.append({"name": "explicit_service_udp_guard", "passed": not udp_guarded})
        unrelated_udp = udp_probe(core.mixed_port, "direct.p0.invalid", ports["udp"])
        assert unrelated_udp, "service UDP guard also blocked unrelated UDP"
        checks.append({"name": "unrelated_udp_preserved_with_guard", "passed": unrelated_udp})
        check_gpt_strict(core, config, ports, checks)
        return {"scope": "standalone Mihomo, loopback fixtures, TUN disabled; not desktop-client acceptance",
                "version": version, "checks": checks, "proxy_events": events,
                "unguarded_udp_contained": not udp_baseline}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    checks = []
    try:
        report = run(args.binary, checks)
    except Exception as error:
        report = {"checks": checks, "error": str(error), "scope": "isolated P0 fixture run"}
    report["checked_at"] = datetime.now(timezone.utc).isoformat()
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(path), "checks": report["checks"], "error": report.get("error")}, ensure_ascii=False))
    return 0 if "error" not in report and all(case["passed"] for case in report["checks"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
