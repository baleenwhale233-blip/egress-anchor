"""Apply or remove the shared overlay through the owned guest's native Mihomo API."""

from urllib.parse import quote

import yaml

from guest_core_api import request
from guest_overlay_common import atomic_write, digest
from guest_overlay_compute import transform
from guest_native_validation import validate_candidate


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
    validate_candidate(candidate, root, "p0-verge-")
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
