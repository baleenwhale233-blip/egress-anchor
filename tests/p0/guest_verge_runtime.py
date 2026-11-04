"""Apply or remove the shared overlay through the owned guest's native Mihomo API."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import quote

import yaml

from guest_core_api import request
from guest_overlay_common import atomic_write, digest, managed_script


def transform(config, action, port):
    script = managed_script("function main(config) { return config; }", port)
    expression = f"main({json.dumps(config, allow_nan=False)}, 'P0')" if action == "apply" else f"eaP0Clean({json.dumps(config, allow_nan=False)})"
    command = [sys.executable, str(Path(__file__).with_name("guest_script_eval.py"))]
    result = subprocess.run(command, input=script + "\nJSON.stringify(" + expression + ")",
                            text=True, capture_output=True, timeout=4)
    if result.returncode:
        raise ValueError("shared fixture generator did not complete")
    return json.loads(result.stdout)


def verify_routes(action, desired):
    from guest_client_probe import observe

    expected = "managed"
    if action == "restore":
        targets = [rule.rsplit(",", 1)[-1] for rule in desired.get("rules", [])
                   if rule.startswith(("DOMAIN,managed.p0.invalid,", "MATCH,"))]
        if not targets or targets[0] not in ("DIRECT", "OriginalAI"):
            raise ValueError("restored fixture needs an explicit supported route check")
        expected = "direct" if targets[0] == "DIRECT" else "source"
    evidence = observe(action == "apply", expected_route=expected, client="verge")
    return {"verified_service_route": evidence["observed_route"],
            "verified_unmanaged_route": evidence["unmanaged_chain"]}


def update(root, action, landing_port):
    path = root / "clash-verge.yaml"
    original = path.read_text()
    metadata = (root / "profiles.yaml").read_text()
    runtime = yaml.safe_load(original)
    desired = transform(runtime, action, landing_port)
    candidate = yaml.safe_dump(desired, sort_keys=False)
    # Validate in a separate data directory; never launch a second listening core.
    with tempfile.TemporaryDirectory(prefix="p0-verge-") as directory:
        temporary = Path(directory) / "candidate.yaml"
        temporary.write_text(candidate)
        checked = subprocess.run(["/usr/bin/verge-mihomo", "-t", "-d", directory, "-f", str(temporary)],
                                 capture_output=True, timeout=8)
        if checked.returncode:
            raise ValueError("native core rejected the candidate fixture configuration")
    if path.read_text() != original or (root / "profiles.yaml").read_text() != metadata:
        raise ValueError("native profile changed while preparing; retry from current state")
    flows = request(runtime, "verge", "GET", "/connections").get("connections") or []
    selected = [row["id"] for row in flows if row["metadata"].get("host") == "managed.p0.invalid"]
    atomic_write(path, candidate)
    request(runtime, "verge", "PUT", "/configs?force=false", {"path": str(path)})
    if (root / "profiles.yaml").read_text() != metadata or digest(path.read_text()) != digest(candidate):
        raise ValueError("native profile changed during apply; runtime needs verification")
    proxies = request(desired, "verge", "GET", "/proxies")["proxies"]
    if ("EA-P0-Landing" in proxies) != (action == "apply"):
        raise ValueError("runtime objects did not match the requested overlay")
    for connection_id in selected:
        request(desired, "verge", "DELETE", "/connections/" + quote(connection_id, safe=""))
    evidence = verify_routes(action, desired)
    return {"hot_applied": action == "apply", "hot_restored": action == "restore",
            "closed_protected_connections": len(selected), "native_client_restarted": False, **evidence}
