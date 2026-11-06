"""Separate saved configuration, application, runtime and scoped evidence."""

from copy import deepcopy

CHECKS = (("exit", "实际出口"), ("chain", "代理链连接"), ("reachability", "服务可达性"),
          ("dns", "DNS 路径"), ("udp_webrtc", "UDP / WebRTC"))
EVIDENCE_STATES = {"verified", "unverified", "unsupported", "failed", "checking", "stale"}


def verification_view(evidence, *, revision, source_id, generation):
    result = {"state": "unverified", "scope": "service", "checked_at": None,
        "service_revision": None, "source_id": None, "generation": None,
        "reason": "服务路径检测尚未接入。", "items": [
            {"id": key, "label": label, "state": "unverified", "reason": "尚未检测"}
            for key, label in CHECKS]}
    if (not isinstance(evidence, dict) or not isinstance(evidence.get("state"), str)
            or evidence["state"] not in EVIDENCE_STATES):
        return result
    for key in ("state", "checked_at", "service_revision", "source_id", "generation", "reason"):
        if key in evidence:
            result[key] = deepcopy(evidence[key])
    items = evidence.get("items", [])
    if isinstance(items, list):
        by_id = {row.get("id"): row for row in items if isinstance(row, dict)
                 and isinstance(row.get("id"), str) and isinstance(row.get("state"), str)
                 and row["state"] in EVIDENCE_STATES}
        for row in result["items"]:
            if row["id"] in by_id:
                item = by_id[row["id"]]
                row.update(state=item["state"], reason=item.get("reason", ""))
    # Never reuse a result for a different saved intent or generated source.
    if (evidence.get("service_revision"), evidence.get("source_id"), evidence.get("generation")) != (
            revision, source_id, generation):
        result.update(state="stale", reason="配置已变化，请重新检测。")
        result["items"] = [dict(row, state="stale") for row in result["items"]]
    return result


def service_status(record, state, application):
    return {
        "configuration": {"state": "saved", "revision": record["revision"]},
        "application": {"state": application, "applied_revision": None, "applied_at": None},
        "runtime": {"state": "unconfirmed", "exit": None, "protection": None, "checked_at": None},
        "verification": verification_view(None, revision=record["revision"],
            source_id=state.get("selected_source_id"), generation=state.get("active_generation")),
    }
