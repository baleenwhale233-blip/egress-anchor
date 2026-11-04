"""GPT/Codex fixture routing; no real OpenAI requests, accounts or client files."""

from copy import deepcopy

from loopback_fixtures import ping, tunnel, udp_probe


def check_gpt_strict(core, source, ports, checks):
    source = deepcopy(source)
    hosts = ("chatgpt.p0.invalid", "codex.p0.invalid")
    source["hosts"].update({host: "127.0.0.1" for host in hosts})
    source["proxy-groups"].append({"name": "GPT", "type": "select", "proxies": ["front"]})
    source["rules"] = [
        *[f"DOMAIN,{host},GPT" for host in hosts],
        *[rule for rule in source["rules"] if not rule.startswith("DOMAIN,chatgpt.p0.invalid,")],
    ]
    landing = {"type": "http", "server": "127.0.0.1", "port": ports["landing-b"]}
    core.reload(
        source,
        {**landing, "port": ports["landing-a"]},
        udp_guards=hosts,
        extra_managed={"GPT": {"landing": landing, "front_hop": {"mode": "fixed", "proxy": "front"}}},
    )
    groups = {row["group"]: row for row in core.report["managed_groups"]}
    for host in hosts:
        with tunnel(core.mixed_port, host, ports["origin"]) as connection:
            ping(connection)
            observations = core.api("GET", "/connections")["connections"]
            assert any(
                row["metadata"].get("host") == host and groups["GPT"]["landing"] in row["chains"]
                for row in observations
            ), f"{host} did not use the GPT landing"
    checks.append({"name": "gpt_and_codex_tcp_use_same_managed_landing", "passed": True})

    for host in hosts:
        assert not udp_probe(core.mixed_port, host, ports["udp"]), f"{host} UDP was not rejected"
    checks.append({"name": "gpt_and_codex_udp_rejected", "passed": True})

    assert udp_probe(core.mixed_port, "direct.p0.invalid", ports["udp"])
    assert udp_probe(core.mixed_port, "claude.p0.invalid", ports["udp"])
    with tunnel(core.mixed_port, "claude.p0.invalid", ports["origin"]) as connection:
        ping(connection)
        observations = core.api("GET", "/connections")["connections"]
        assert any(
            row["metadata"].get("host") == "claude.p0.invalid" and groups["AI"]["landing"] in row["chains"]
            for row in observations
        ), "GPT strict fixture changed the Claude landing"
    checks.append({"name": "gpt_strict_preserves_claude_policy_and_unrelated_udp", "passed": True})
