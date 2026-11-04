"""Observe native URL-test failover without changing the required landing."""

from contextlib import ExitStack
from copy import deepcopy
import threading
import time
from urllib.parse import quote

from loopback_fixtures import fixture, ping, tunnel


def wait_until(predicate, message):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError(message)


def check_automatic_front(core, source, ports, checks, events, other_connection):
    primary_down, secondary_down = threading.Event(), threading.Event()
    with ExitStack() as stack:
        health = stack.enter_context(fixture("health"))
        primary = stack.enter_context(fixture("http", "auto-primary", events, outage=primary_down))
        secondary = stack.enter_context(fixture("http", "auto-secondary", events,
                                              outage=secondary_down, connect_delay=0.12))
        source = deepcopy(source)
        source["proxies"].extend([
            {"name": "auto-primary", "type": "http", "server": "127.0.0.1", "port": primary},
            {"name": "auto-secondary", "type": "http", "server": "127.0.0.1", "port": secondary},
        ])
        landing = {"type": "http", "server": "127.0.0.1", "port": ports["landing-b"]}
        core.reload(source, landing, front_hop={
            "mode": "automatic", "proxies": ["auto-primary", "auto-secondary"],
            "url": f"http://127.0.0.1:{health}/p0-health", "interval": 1,
        })
        managed = next(row for row in core.report["managed_groups"] if row["group"] == "AI")
        front_path = "/proxies/" + quote(managed["front"], safe="")
        with tunnel(core.mixed_port, "claude.p0.invalid", ports["origin"]) as connection:
            ping(connection)
        wait_until(
            lambda: core.api("GET", front_path)["now"] == "auto-primary"
            and all(any(e["proxy"] == name and e["port"] == health for e in events)
                    for name in ("auto-primary", "auto-secondary")),
            "native URL-test did not select the healthy faster primary",
        )
        checks.append({"name": "automatic_front_selects_primary", "passed": True})

        primary_down.set()
        failed_requests = 0

        def service_has_failed_over():
            nonlocal failed_requests
            # Lazy health checks require actual use. Do not force a selection or group test.
            try:
                with tunnel(core.mixed_port, "claude.p0.invalid", ports["origin"]) as connection:
                    ping(connection)
            except OSError:
                failed_requests += 1
            return core.api("GET", front_path)["now"] == "auto-secondary"

        wait_until(service_has_failed_over, "native URL-test did not recover during active service requests")
        start = len(events)
        with tunnel(core.mixed_port, "claude.p0.invalid", ports["origin"]) as connection:
            ping(connection)
            observed = core.api("GET", "/connections")["connections"]
            assert any(row["metadata"].get("host") == "claude.p0.invalid"
                       and managed["landing"] in row["chains"] for row in observed)
        assert any(e["proxy"] == "auto-secondary" and e["port"] == ports["landing-b"]
                   for e in events[start:])
        assert any(e["proxy"] == "landing-b" and e["port"] == ports["origin"] for e in events[start:])
        ping(other_connection)
        checks.append({"name": "automatic_front_failover_keeps_landing", "passed": True,
                       "requests_failed_before_switch": failed_requests})

        secondary_down.set()
        start = len(events)
        try:
            with tunnel(core.mixed_port, "claude.p0.invalid", ports["origin"]) as connection:
                ping(connection)
        except OSError:
            pass
        else:
            raise AssertionError("all-front failure bypassed the required landing")
        assert not any(e["port"] == ports["origin"] for e in events[start:])
        ping(other_connection)
        checks.append({"name": "all_fronts_down_do_not_bypass", "passed": True})
