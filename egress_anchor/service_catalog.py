"""Service templates and user configuration; no storage or client access.

Templates intentionally cover first-party domains only. Shared third-party
login, payment and telemetry hosts are not claimed on behalf of a service.
"""

from copy import deepcopy
import secrets

from .service_policy import ServicePolicyError, parse_service_policies
from .service_protection import (ProtectionError, ProtectionPolicy, parse_protection,
                                 protection_capabilities, protection_details)
from .service_status import service_status


TEMPLATE_VERSION = "2026-11-06"
TEMPLATES = {
    "claude": {
        "name": "Claude", "description": "Claude 网页与 Claude Code",
        "domains": ["claude.ai", "anthropic.com", "claude.com", "claudeusercontent.com"],
        "suggested_exit": "inherit",
    },
    "chatgpt": {
        "name": "ChatGPT", "description": "ChatGPT 与 Codex",
        "domains": ["chatgpt.com", "openai.com", "oaistatic.com", "oaiusercontent.com", "oaistatsig.com"],
        "suggested_exit": "inherit",
    },
    "deepseek": {
        "name": "DeepSeek", "description": "DeepSeek 网页与 API",
        "domains": ["deepseek.com"], "suggested_exit": "direct",
    },
}
EDIT_FIELDS = {"id", "name", "template_id", "extra_domains", "exclude_domains", "exit", "strict", "protection"}


def default_services():
    return [dict(id=key, name=value["name"], template_id=key, extra_domains=[],
                 exclude_domains=[], exit={"kind": "inherit"}, strict=False,
                 protection=ProtectionPolicy().as_dict(), revision=0, updated_at=None) for key, value in TEMPLATES.items()]


def resolve_services(records):
    """Merge the current template without persisting template domains in records."""
    resolved = []
    for record in records:
        template = TEMPLATES.get(record.get("template_id"), {})
        resolved.append({
            "id": record["id"], "name": record["name"],
            "domains": template.get("domains", []) + record.get("extra_domains", []),
            "exclude_domains": record.get("exclude_domains", []),
            "exit": record["exit"], "strict": record.get("strict", False),
            **({"protection": record["protection"]} if "protection" in record else {}),
        })
    return parse_service_policies(resolved)


def change_service(records, payload, *, now, delete=False):
    """Return a validated candidate; stale or invalid edits never mutate records."""
    raw = payload.get("service")
    if not isinstance(raw, dict) or set(raw) - EDIT_FIELDS:
        raise ServicePolicyError("SERVICE_POLICY_INVALID", "服务设置格式不正确。", "service")
    service_id = raw.get("id")
    current = next((s for s in records if s["id"] == service_id), None)
    if service_id is not None and current is None:
        raise ServicePolicyError("SERVICE_NOT_FOUND", "服务已被删除，请刷新列表。", "service.id")
    revision = payload.get("revision")
    if type(revision) is not int or revision != (current["revision"] if current else 0):
        raise ServicePolicyError("SERVICE_CHANGED", "服务已在其他窗口修改，请重新打开设置。", "revision")
    result = deepcopy(records)
    if delete:
        if current is None or current.get("template_id"):
            raise ServicePolicyError("SERVICE_BUILTIN", "内置服务请改为沿用订阅。", "service.id")
        return [s for s in result if s["id"] != service_id]
    if current is None and len(result) >= 64:
        raise ServicePolicyError("SERVICE_LIMIT", "最多添加 64 个服务。", "services")
    template_id = raw.get("template_id")
    if template_id is not None and (not isinstance(template_id, str) or template_id not in TEMPLATES):
        raise ServicePolicyError("SERVICE_POLICY_INVALID", "请选择有效的服务模板。", "service.template_id")
    if template_id != (current.get("template_id") if current else None):
        raise ServicePolicyError("SERVICE_POLICY_INVALID", "已有服务的模板不能更换。", "service.template_id")
    for field in ("extra_domains", "exclude_domains"):
        values = raw.get(field, [])
        if not isinstance(values, list) or len(values) > 256:
            raise ServicePolicyError("SERVICE_POLICY_INVALID", "每个列表最多填写 256 个域名。", f"service.{field}")
    if raw.get("strict"):
        raise ServicePolicyError("SERVICE_STRICT_UNAVAILABLE", "完整严格模式尚未接入，暂不能开启。", "service.strict")
    try:
        protection = parse_protection(raw.get("protection", (current or {}).get(
            "protection", ProtectionPolicy().as_dict())))
    except ProtectionError as error:
        raise ServicePolicyError("SERVICE_PROTECTION_INVALID", str(error), f"service.{error.field}") from None
    if protection.preset != "off":
        raise ServicePolicyError("SERVICE_PROTECTION_UNAVAILABLE", "完整保护尚未接入，暂不能开启。", "service.protection.preset")
    if "exit" not in raw:
        raise ServicePolicyError("SERVICE_POLICY_INVALID", "请选择出口方式。", "service.exit")
    record = {key: deepcopy(value) for key, value in raw.items()}
    record.update(id=service_id or f"custom-{secrets.token_hex(6)}", template_id=template_id,
                  extra_domains=raw.get("extra_domains", []), exclude_domains=raw.get("exclude_domains", []),
                  strict=raw.get("strict", False), protection=protection.as_dict(), revision=revision + 1, updated_at=now)
    # Validate additional domains separately, so a template cannot hide malformed input.
    additional = parse_service_policies([dict(id=record["id"], name=record.get("name"),
        domains=record["extra_domains"], exclude_domains=record["exclude_domains"],
        exit={"kind": "inherit"})])[0]
    record.update(name=additional.name, extra_domains=list(additional.domains),
                  exclude_domains=list(additional.exclude_domains))
    if current:
        result[result.index(current)] = record
    else:
        result.append(record)
    resolve_services(result)
    return result


def service_view(state):
    records = deepcopy(state.get("services", default_services()))
    generated = (state.get("active_generation") is not None
                 and state.get("active_source_id") == state.get("selected_source_id")
                 and state.get("compiled_services_revision") == state.get("services_revision", 0))
    for record in records:
        template = TEMPLATES.get(record.get("template_id"), {})
        record["template_domains"] = list(template.get("domains", []))
        record["description"] = template.get("description", "自定义服务")
        untouched = record["exit"]["kind"] == "inherit" and record["revision"] == 0
        record["application"] = "inherited" if untouched else ("pending_client" if generated else "saved")
        record["verification"] = "unverified"
        protection = parse_protection(record.get("protection", ProtectionPolicy().as_dict()))
        record["protection"] = protection.as_dict()
        record["protection_details"] = protection_details(protection, record["exit"]["kind"])
        record["status"] = service_status(record, state, record["application"])
    return {
        "services": records, "templates": deepcopy(TEMPLATES), "template_version": TEMPLATE_VERSION,
        "services_need_generation": not generated,
        "capabilities": {"strict": False, "service_probe": False, "automatic_apply": False,
                         "protection": protection_capabilities()},
    }
