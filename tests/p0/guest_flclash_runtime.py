"""Hot overlay after a native standard-profile guard; original rule generation stays native."""

import json
import ctypes
from pathlib import Path
import socket
import time
from urllib.parse import quote

import yaml

from guest_client_probe import native_state, observe
from guest_core_api import request
from guest_overlay_common import atomic_write
from guest_overlay_compute import transform
from guest_native_validation import validate_candidate
from guest_client_context import flclash_context


def ensure_ready(root, client, changes):
    if client == "flclash-0894" and changes["scripts"]:
        binary = Path(flclash_context(client)["binary"])
        libraries = ["libquickjs_c_bridge_plugin.so", str(binary.parent / "lib/libquickjs_c_bridge_plugin.so")]
        available = False
        for path in libraries:
            try:
                ctypes.CDLL(path)
                available = True
                break
            except OSError:
                pass
        if not available:
            raise ValueError("NATIVE_SCRIPT_ENGINE_UNAVAILABLE: legacy Linux QuickJS bridge is missing; configuration unchanged")
    runtime = yaml.safe_load((root / "config.yaml").read_text()) or {}
    request(runtime, client, "GET", "/version")
    return running(runtime)


def running(runtime):
    port = runtime.get("mixed-port", 0)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def startup_status(root, previous_running, client="flclash"):
    deadline = time.monotonic() + 12
    while True:
        try:
            runtime = yaml.safe_load((root / "config.yaml").read_text())
            request(runtime, client, "GET", "/version")
            break
        except (OSError, ValueError, TypeError):
            if time.monotonic() > deadline:
                raise ValueError("test FlClash did not become ready; receipt retained")
            time.sleep(0.2)
    active = running(runtime)
    return {"previous_running": previous_running, "proxy_running": active,
            "manual_start_required": not active,
            "next_action": None if active else "在 FlClash 点击启动",
            "runtime_verification_required": True}


def apply(root, landing_port, client="flclash"):
    path = root / "config.yaml"
    original = path.read_text()
    runtime, selected = native_state(client)
    config = request(runtime, client, "GET", "/configs")
    if str(config.get("mode", "")).lower() != "rule" or not running(runtime):
        raise ValueError("start the native proxy in rule mode before applying")
    desired = transform(runtime, "apply", landing_port)
    candidate = yaml.safe_dump(desired, sort_keys=False)
    validate_candidate(candidate, root, "p0-flclash-")
    _, current = native_state(client)
    if current != selected or path.read_text() != original:
        raise ValueError("native profile changed while preparing")
    atomic_write(path, candidate)
    request(runtime, client, "PUT", "/configs?force=false", {"path": str(path)})
    _, current = native_state(client)
    if current != selected or path.read_text() != candidate:
        raise ValueError("native profile changed during apply; verification required")
    evidence = observe(True, client=client)
    return {"hot_applied": True, "native_client_restarted": False,
            "verified_service_route": evidence["observed_route"],
            "verified_unmanaged_route": evidence["unmanaged_chain"]}
