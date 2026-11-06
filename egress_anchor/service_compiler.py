"""Compose service rules with the existing fail-closed subscription compiler."""

from copy import deepcopy
import yaml

from .compiler import CompileError, CompileResult, compile_subscription, inspect_subscription
from .service_catalog import resolve_services
from .service_policy import ServicePolicyError


def airport_choices(catalog, bindings=()):
    """Only offer source routes whose explicit leaves cannot select DIRECT."""
    proxies = {p["name"] for p in catalog.get("proxies", []) if p.get("type") not in {"direct", "reject", "dns"}}
    groups = {g["name"]: g for g in catalog.get("groups", [])}
    managed = {b["group"] for b in bindings if b.get("enabled")}

    def safe(name, visiting=()):
        if name in managed or name in visiting:
            return False
        if name in proxies:
            return True
        group = groups.get(name)
        # Dynamic/provider groups may acquire generated landings or fall back
        # to DIRECT when empty. Do not claim they are airport-only routes.
        if not group or group.get("dynamic_candidates") or group.get("use"):
            return False
        refs = group.get("proxies", [])
        return bool(refs) and all(safe(ref, (*visiting, name)) for ref in refs)

    return sorted(name for name in proxies | set(groups) if safe(name))


def validate_service_references(records, landings, catalog, bindings=()):
    landing_ids = {landing["id"] for landing in landings}
    airports = set(airport_choices(catalog, bindings))
    for policy in resolve_services(records):
        choice = policy.exit
        if choice.kind == "isp" and choice.reference not in landing_ids:
            raise ServicePolicyError("SERVICE_LANDING_MISSING", "落地代理已不存在，请重新选择。", "service.exit.reference", service_ids=(policy.id,))
        if choice.kind == "airport" and choice.reference not in airports:
            raise ServicePolicyError("SERVICE_AIRPORT_MISSING", "请选择当前订阅中不含直连的节点或策略组。", "service.exit.reference", service_ids=(policy.id,))


def _domain_expression(domain, exclusions):
    if any(domain == item or domain.endswith("." + item) for item in exclusions):
        return None
    nested = [item for item in exclusions if item.endswith("." + domain)]
    match = f"DOMAIN-SUFFIX,{domain}"
    if nested:
        operands = [f"({match})", *(f"(NOT,((DOMAIN-SUFFIX,{item})))" for item in nested)]
        match = f"AND,({','.join(operands)})"
    return match


def compile_service_subscription(source_yaml, records, landings, legacy_policy=None):
    """Generate a complete config; callers decide whether/where to deliver it.

    ISP service paths are TCP-only at this stage. Reject their UDP explicitly,
    before source rules, rather than letting an unsupported outbound fall through.
    This is not the product's full DNS/WebRTC/client strict-mode contract.
    """
    catalog = inspect_subscription(source_yaml)
    legacy = deepcopy(legacy_policy or {"version": 1, "managed_groups": {}})
    bindings = [dict(group=name, enabled=True) for name in legacy["managed_groups"]]
    validate_service_references(records, landings, catalog, bindings)
    policies = resolve_services(records)
    if any(policy.strict for policy in policies):
        raise ServicePolicyError("SERVICE_STRICT_UNAVAILABLE", "完整严格模式尚未接入。", "service.strict")
    # inspect_subscription validated duplicate keys, tags, schema and references.
    config = yaml.safe_load(source_yaml)
    names = {p["name"] for p in catalog["proxies"] + catalog["groups"]}
    landing_map = {landing["id"]: landing for landing in landings}
    prefix, report = [], []
    for policy in policies:
        kind = policy.exit.kind
        if kind == "inherit":
            continue
        target = "DIRECT" if kind == "direct" else policy.exit.reference
        if kind == "isp":
            target = f"EA-Service-{policy.id}"
            if target in names:
                raise CompileError("GENERATED_NAME_COLLISION", "A generated service name is owned by the source.")
            nodes = [p["name"] for p in catalog["proxies"] if p.get("type") not in {"direct", "reject", "dns"}]
            if not nodes and not catalog["providers"]:
                raise ServicePolicyError("SERVICE_FRONT_MISSING", "当前订阅没有可用的前置节点。", "service.exit", service_ids=(policy.id,))
            config["proxy-groups"].append(dict(name=target, type="select", proxies=nodes, use=catalog["providers"], hidden=True))
            landing = landing_map[policy.exit.reference]
            legacy["managed_groups"][target] = {
                "landing": {key: deepcopy(landing[key]) for key in ("type", "server", "port", "username", "password", "udp") if key in landing},
                "front_hop": {"mode": "automatic"},
            }
        # Proxy/group names are rule arguments. Refuse delimiters instead of
        # changing the referenced name or creating an unintended rule target.
        if any(char in target for char in ",\r\n"):
            raise ServicePolicyError("SERVICE_TARGET_INVALID", "此节点名称包含规则分隔符，请选择其他节点。", "service.exit.reference")
        for domain in policy.domains:
            expression = _domain_expression(domain, policy.exclude_domains)
            if expression is None:
                continue
            if kind == "isp":
                prefix.append(f"AND,((NETWORK,UDP),({expression})),REJECT")
            prefix.append(f"{expression},{target}")
        report.append({"id": policy.id, "kind": kind, "target": target, "udp": "rejected" if kind == "isp" else "source"})
    config["rules"] = prefix + config.get("rules", [])
    rendered = yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=120)
    if legacy["managed_groups"]:
        result = compile_subscription(rendered, legacy)
        output, details = result.compiled_yaml, dict(result.report)
    else:
        inspect_subscription(rendered)
        output, details = rendered, {}
    details["services"] = report
    details["service_rule_count"] = len(prefix)
    return CompileResult(output, details)
