"""Pure Mihomo subscription compiler.

The public seam is ``compile_subscription``. Delivery, caching, and client
control stay outside this module.

Fail-closed topology:

    managed group -> generated landing -> generated front -> source candidates

The managed group has one member. Runtime selection may change inside the front
group, but no valid managed-group selection can skip the landing.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Mapping, Optional

import yaml


COMPILED_MARKER = "# EgressAnchor compiled schema=1"
POLICY_MARKER_PREFIX = "# EgressAnchor policy="
BODY_MARKER_PREFIX = "# EgressAnchor body="
YAML_MAPPING_TAG = "tag:yaml.org,2002:map"
YAML_MERGE_TAG = "tag:yaml.org,2002:merge"
AUTOMATIC_CANDIDATE_FIELDS = (
    "proxies",
    "use",
    "include-all",
    "include-all-proxies",
    "include-all-providers",
    "filter",
    "exclude-filter",
    "exclude-type",
)
FINGERPRINT_FRONT_FIELDS = (
    "mode",
    "proxy",
    *AUTOMATIC_CANDIDATE_FIELDS,
    "url",
    "interval",
)
DEFAULT_HEALTHCHECK_URL = "https://www.gstatic.com/generate_204"
META_OUTBOUNDS = {
    "DIRECT",
    "REJECT",
    "REJECT-DROP",
    "PASS",
    "PASS-RULE",
    "COMPATIBLE",
    "GLOBAL",
}
MAX_SOURCE_BYTES = 8 * 1024 * 1024
POLICY_FIELDS = {"version", "managed_groups"}
BINDING_FIELDS = {"landing", "front_hop"}
LANDING_FIELDS = {"type", "server", "port", "username", "password", "udp"}
FRONT_FIELDS = {
    "source": {"mode"},
    "fixed": {"mode", "proxy"},
    "automatic": {"mode", *AUTOMATIC_CANDIDATE_FIELDS, "url", "interval"},
    "direct": {"mode"},
}


class _DuplicateKeyError(Exception):
    def __init__(self, mark: Any) -> None:
        self.mark = mark


class _StrictSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _StrictSafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> Mapping[Any, Any]:
    seen = set()
    for key_node, _ in node.value:
        if key_node.tag == YAML_MERGE_TAG:
            continue
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in seen
            seen.add(key)
        except TypeError:
            duplicate = False
        if duplicate:
            raise _DuplicateKeyError(key_node.start_mark)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictSafeLoader.add_constructor(YAML_MAPPING_TAG, _construct_unique_mapping)


@dataclass(frozen=True)
class CompileResult:
    compiled_yaml: str = field(repr=False)
    report: Mapping[str, Any]


class CompileError(ValueError):
    """A caller-safe compiler failure.

    The message is deliberately supplied by the compiler instead of forwarding
    parser or source text that may contain credentials.
    """

    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        line: Optional[int] = None,
        column: Optional[int] = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.line = line
        self.column = column


def _generated_names(group_name: str) -> tuple[str, str]:
    digest = hashlib.sha256(group_name.encode("utf-8")).hexdigest()[:16]
    return f"EA·{digest}·landing", f"EA·{digest}·front"


def _policy_fingerprint(policy: Mapping[str, Any]) -> str:
    public_shape = []
    for group_name in sorted(policy["managed_groups"]):
        binding = policy["managed_groups"][group_name]
        front_hop = binding.get("front_hop", {})
        mode = front_hop.get("mode", "source")
        public_front = {"mode": mode}
        if mode == "automatic":
            public_front["url"] = front_hop.get("url", DEFAULT_HEALTHCHECK_URL)
            public_front["interval"] = front_hop.get("interval", 300)
        for field in FINGERPRINT_FRONT_FIELDS:
            if field not in {"mode", "url", "interval"} and field in front_hop:
                public_front[field] = front_hop[field]
        public_shape.append({"group": group_name, "front_hop": public_front})
    encoded = json.dumps(
        public_shape,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _compiled_header(policy: Mapping[str, Any]) -> str:
    return f"{COMPILED_MARKER}\n{POLICY_MARKER_PREFIX}{_policy_fingerprint(policy)}"


def _body_fingerprint(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _compiled_document(policy: Mapping[str, Any], body: str) -> str:
    return (
        f"{_compiled_header(policy)}\n"
        f"{BODY_MARKER_PREFIX}{_body_fingerprint(body)}\n"
        f"{body}"
    )


def _marked_document_matches(source_yaml: str, policy: Mapping[str, Any]) -> bool:
    parts = source_yaml.split("\n", 3)
    if len(parts) != 4:
        return False
    schema_marker, policy_marker, body_marker, body = parts
    if f"{schema_marker}\n{policy_marker}" != _compiled_header(policy):
        return False
    return body_marker == f"{BODY_MARKER_PREFIX}{_body_fingerprint(body)}"


def _generated_exclusion_pattern(landing_names: set[str]) -> str:
    # Mihomo uses Go's RE2 syntax; use a capturing group because RE2 does not
    # accept Python/PCRE-style non-capturing ``(?:...)`` groups.
    return "^(" + "|".join(re.escape(name) for name in sorted(landing_names)) + ")$"


def _build_landing_proxy(
    binding: Mapping[str, Any],
    landing_name: str,
    front_name: Optional[str],
) -> dict[str, Any]:
    landing = deepcopy(binding["landing"])
    landing["server"] = landing["server"].strip()
    landing["name"] = landing_name
    if front_name is not None:
        landing["dialer-proxy"] = front_name
    return landing


def _compiled_report(policy: Mapping[str, Any], *, already_compiled: bool) -> Mapping[str, Any]:
    applied = []
    for group_name in sorted(policy["managed_groups"]):
        binding = policy["managed_groups"][group_name]
        landing_name, front_name = _generated_names(group_name)
        front_mode = binding.get("front_hop", {}).get("mode", "source")
        applied.append(
            {
                "group": group_name,
                "landing": landing_name,
                "front": None if front_mode == "direct" else front_name,
                "front_mode": front_mode,
            }
        )
    return _build_report(applied, already_compiled=already_compiled)


def _build_report(
    applied: list[dict[str, Any]],
    *,
    already_compiled: bool,
) -> Mapping[str, Any]:
    return {
        "managed_groups": applied,
        "counts": {
            "managed_groups": len(applied),
            "generated_proxies": len(applied),
            "generated_groups": sum(1 for item in applied if item["front"] is not None),
        },
        "warnings": [],
        "already_compiled": already_compiled,
    }


def _matches_compiled_topology(config: Mapping[str, Any], policy: Mapping[str, Any]) -> bool:
    proxies = config.get("proxies")
    groups = config.get("proxy-groups")

    generated_landings = {
        _generated_names(group_name)[0] for group_name in policy["managed_groups"]
    }
    for group_name in sorted(policy["managed_groups"]):
        binding = policy["managed_groups"][group_name]
        landing_name, front_name = _generated_names(group_name)
        front_hop = binding.get("front_hop", {"mode": "source"})
        front_mode = front_hop.get("mode", "source")
        target_matches = [group for group in groups if group.get("name") == group_name]
        landing_matches = [proxy for proxy in proxies if proxy.get("name") == landing_name]
        front_matches = [group for group in groups if group.get("name") == front_name]
        expected_fronts = 0 if front_mode == "direct" else 1
        if (
            len(target_matches) != 1
            or len(landing_matches) != 1
            or len(front_matches) != expected_fronts
        ):
            return False
        target = target_matches[0]
        if target.get("type") != "select" or target.get("proxies") != [landing_name]:
            return False

        expected_landing = _build_landing_proxy(
            binding,
            landing_name,
            None if front_mode == "direct" else front_name,
        )
        if landing_matches[0] != expected_landing:
            return False

        if front_mode == "direct":
            continue
        front = front_matches[0]
        if front.get("hidden") is not True or front.get("empty-fallback") != "REJECT":
            return False
        if front.get("include-all") or front.get("include-all-proxies"):
            exclusion = front.get("exclude-filter")
            if not isinstance(exclusion, str):
                return False
            generated_pattern = _generated_exclusion_pattern(generated_landings)
            if exclusion != generated_pattern and not exclusion.endswith(
                f"|{generated_pattern}"
            ):
                return False
        if front_hop.get("mode") == "fixed":
            if front.get("type") != "select" or front.get("proxies") != [front_hop["proxy"]]:
                return False
            if "use" in front:
                return False
        if front_hop.get("mode") == "automatic":
            if front.get("type") != "url-test":
                return False
            if front.get("url") != front_hop.get("url", DEFAULT_HEALTHCHECK_URL):
                return False
            if front.get("interval") != front_hop.get("interval", 300):
                return False
    return True


def _load_yaml(source_yaml: str) -> Any:
    try:
        return yaml.load(source_yaml, Loader=_StrictSafeLoader)
    except RecursionError:
        raise CompileError(
            "YAML_INVALID",
            "The source YAML nesting is too deep.",
        ) from None
    except _DuplicateKeyError as exc:
        raise CompileError(
            "YAML_DUPLICATE_KEY",
            "The source contains a duplicate YAML mapping key.",
            line=exc.mark.line + 1,
            column=exc.mark.column + 1,
        ) from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise CompileError(
            "YAML_INVALID",
            "The source is not valid safe YAML.",
            line=mark.line + 1 if mark is not None else None,
            column=mark.column + 1 if mark is not None else None,
        ) from None


def _validate_unique_runtime_names(config: Mapping[str, Any]) -> None:
    names = set()
    items = list(config.get("proxies") or []) + list(config.get("proxy-groups") or [])
    for item in items:
        name = item["name"]
        if name in names:
            raise CompileError(
                "DUPLICATE_RUNTIME_NAME",
                "Proxy and group names must be unique.",
            )
        names.add(name)


def _source_schema_invalid() -> CompileError:
    return CompileError(
        "SOURCE_SCHEMA_INVALID",
        "The source does not match the required Mihomo configuration shape.",
    )


def _validate_source_schema(config: dict[str, Any]) -> None:
    proxies = config.get("proxies")
    if proxies is None:
        config["proxies"] = []
        proxies = []
    groups = config.get("proxy-groups")
    providers = config.get("proxy-providers")
    if not isinstance(proxies, list) or not isinstance(groups, list):
        raise _source_schema_invalid()
    if providers is not None:
        if not isinstance(providers, Mapping):
            raise _source_schema_invalid()
        for provider_name, provider in providers.items():
            if not isinstance(provider_name, str) or not provider_name:
                raise _source_schema_invalid()
            if not isinstance(provider, Mapping):
                raise _source_schema_invalid()

    for item in proxies:
        if not isinstance(item, Mapping):
            raise _source_schema_invalid()
        if not isinstance(item.get("name"), str) or not item["name"]:
            raise _source_schema_invalid()
        if not isinstance(item.get("type"), str) or not item["type"]:
            raise _source_schema_invalid()
        if "dialer-proxy" in item and not isinstance(item["dialer-proxy"], str):
            raise _source_schema_invalid()

    for group in groups:
        if not isinstance(group, Mapping):
            raise _source_schema_invalid()
        if not isinstance(group.get("name"), str) or not group["name"]:
            raise _source_schema_invalid()
        if not isinstance(group.get("type"), str) or not group["type"]:
            raise _source_schema_invalid()
        for field in ("proxies", "use"):
            if field in group and (
                not isinstance(group[field], list)
                or not all(isinstance(reference, str) and reference for reference in group[field])
            ):
                raise _source_schema_invalid()
        for field in (
            "include-all",
            "include-all-proxies",
            "include-all-providers",
            "hidden",
            "disable-udp",
        ):
            if field in group and not isinstance(group[field], bool):
                raise _source_schema_invalid()
        for field in ("filter", "exclude-filter", "exclude-type", "url"):
            if field in group and not isinstance(group[field], str):
                raise _source_schema_invalid()


def _validate_source_references(config: Mapping[str, Any]) -> None:
    proxies = config.get("proxies") or []
    groups = config.get("proxy-groups") or []
    providers = config.get("proxy-providers") or {}
    runtime_names = {item["name"] for item in list(proxies) + list(groups)}
    allowed = runtime_names | META_OUTBOUNDS
    provider_names = set(providers)
    for group in groups:
        if any(reference not in allowed for reference in group.get("proxies", [])):
            raise _source_schema_invalid()
        if any(provider not in provider_names for provider in group.get("use", [])):
            raise _source_schema_invalid()
    for proxy in proxies:
        dialer = proxy.get("dialer-proxy")
        if dialer is not None and dialer not in allowed:
            raise _source_schema_invalid()


def _validate_acyclic(config: Mapping[str, Any]) -> None:
    proxies = config.get("proxies") or []
    groups = config.get("proxy-groups") or []
    named_items = {
        item["name"]: item
        for item in list(proxies) + list(groups)
    }
    group_names = {item["name"] for item in groups}
    edges = {name: set() for name in named_items}

    for name, item in named_items.items():
        references = []
        if name in group_names:
            references.extend(item.get("proxies", []))
        if isinstance(item.get("dialer-proxy"), str):
            references.append(item["dialer-proxy"])
        edges[name].update(reference for reference in references if reference in named_items)

    indegree = {name: 0 for name in edges}
    for references in edges.values():
        for reference in references:
            indegree[reference] += 1
    ready = deque(name for name, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        name = ready.popleft()
        visited += 1
        for reference in edges[name]:
            indegree[reference] -= 1
            if indegree[reference] == 0:
                ready.append(reference)
    if visited != len(edges):
        raise CompileError(
            "TOPOLOGY_CYCLE",
            "The explicit proxy and group topology contains a cycle.",
        )


def _validate_source_root(config: Any) -> dict[str, Any]:
    if isinstance(config, list):
        raise CompileError(
            "FULL_CONFIG_REQUIRED",
            "V0.1 requires a complete Mihomo configuration.",
        )
    if not isinstance(config, dict):
        raise CompileError(
            "SOURCE_NOT_MAPPING",
            "The source root must be a YAML mapping.",
        )
    if not isinstance(config.get("proxy-groups"), list):
        raise CompileError(
            "FULL_CONFIG_REQUIRED",
            "V0.1 requires a complete Mihomo configuration.",
        )
    return config


def _validate_source_text(source_yaml: Any) -> str:
    if not isinstance(source_yaml, str):
        raise CompileError(
            "SOURCE_NOT_MAPPING",
            "The source must be UTF-8 YAML text.",
        )
    if not source_yaml.strip():
        raise CompileError("SOURCE_EMPTY", "The source is empty.")
    try:
        source_size = len(source_yaml.encode("utf-8"))
    except UnicodeEncodeError:
        raise CompileError("YAML_INVALID", "The source is not valid UTF-8 text.") from None
    if source_size > MAX_SOURCE_BYTES:
        raise CompileError("SOURCE_TOO_LARGE", "The source exceeds the V0.1 size limit.")
    return source_yaml


def _policy_invalid() -> CompileError:
    return CompileError("POLICY_INVALID", "The compiler policy is invalid.")


def _validate_policy(policy: Any) -> Mapping[str, Any]:
    if (
        not isinstance(policy, Mapping)
        or isinstance(policy.get("version"), bool)
        or policy.get("version") != 1
    ):
        raise _policy_invalid()
    if set(policy) - POLICY_FIELDS:
        raise _policy_invalid()
    managed_groups = policy.get("managed_groups")
    if not isinstance(managed_groups, Mapping) or not managed_groups:
        raise _policy_invalid()

    for group_name, binding in managed_groups.items():
        if not isinstance(group_name, str) or not group_name or not isinstance(binding, Mapping):
            raise _policy_invalid()
        if set(binding) - BINDING_FIELDS:
            raise _policy_invalid()
        landing = binding.get("landing")
        if not isinstance(landing, Mapping):
            raise _policy_invalid()
        if set(landing) - LANDING_FIELDS:
            raise _policy_invalid()
        landing_type = landing.get("type")
        if not isinstance(landing_type, str) or landing_type not in {"http", "socks5"}:
            raise _policy_invalid()
        if not isinstance(landing.get("server"), str) or not landing["server"].strip():
            raise _policy_invalid()
        port = landing.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise _policy_invalid()
        for field in ("username", "password"):
            if field in landing and not isinstance(landing[field], str):
                raise _policy_invalid()
        if "udp" in landing and not isinstance(landing["udp"], bool):
            raise _policy_invalid()

        front_hop = binding.get("front_hop", {"mode": "source"})
        if not isinstance(front_hop, Mapping):
            raise _policy_invalid()
        mode = front_hop.get("mode", "source")
        if (
            not isinstance(mode, str)
            or mode not in FRONT_FIELDS
            or set(front_hop) - FRONT_FIELDS[mode]
        ):
            raise _policy_invalid()
        if mode == "fixed":
            if not isinstance(front_hop.get("proxy"), str) or not front_hop["proxy"]:
                raise _policy_invalid()
        if mode == "automatic":
            if "url" in front_hop and (
                not isinstance(front_hop["url"], str) or not front_hop["url"]
            ):
                raise _policy_invalid()
            interval = front_hop.get("interval", 300)
            if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
                raise _policy_invalid()
            for field in ("proxies", "use"):
                if field in front_hop and (
                    not isinstance(front_hop[field], list)
                    or not all(isinstance(item, str) and item for item in front_hop[field])
                ):
                    raise _policy_invalid()
            for field in (
                "include-all",
                "include-all-proxies",
                "include-all-providers",
            ):
                if field in front_hop and not isinstance(front_hop[field], bool):
                    raise _policy_invalid()
            for field in ("filter", "exclude-filter", "exclude-type"):
                if field in front_hop and not isinstance(front_hop[field], str):
                    raise _policy_invalid()
    return policy


def _build_front_group(
    target: dict[str, Any],
    binding: Mapping[str, Any],
    front_name: str,
    source_names: set[str],
    provider_names: set[str],
) -> Optional[dict[str, Any]]:
    front_hop = binding.get("front_hop", {"mode": "source"})
    mode = front_hop.get("mode", "source")
    if mode == "direct":
        return None
    if mode == "fixed":
        reference = front_hop["proxy"]
        if reference not in source_names:
            raise CompileError(
                "FRONT_REFERENCE_MISSING",
                "A front-hop reference is missing from the source configuration.",
            )
        return {
            "name": front_name,
            "type": "select",
            "proxies": [reference],
            "hidden": True,
            "empty-fallback": "REJECT",
        }

    if mode == "automatic":
        front = {"name": front_name, "type": "url-test"}
        for field in AUTOMATIC_CANDIDATE_FIELDS:
            if field in front_hop:
                front[field] = deepcopy(front_hop[field])
            elif field in target:
                front[field] = deepcopy(target[field])
        front["url"] = front_hop.get("url", DEFAULT_HEALTHCHECK_URL)
        front["interval"] = front_hop.get("interval", 300)
        front["hidden"] = True
        front["empty-fallback"] = "REJECT"
        if any(
            reference not in source_names and reference not in META_OUTBOUNDS
            for reference in front.get("proxies", [])
        ):
            raise CompileError(
                "FRONT_REFERENCE_MISSING",
                "A front-hop reference is missing from the source configuration.",
            )
        if any(provider not in provider_names for provider in front.get("use", [])):
            raise CompileError(
                "FRONT_REFERENCE_MISSING",
                "A front-hop provider is missing from the source configuration.",
            )
        return front

    front = deepcopy(target)
    front["name"] = front_name
    front["hidden"] = True
    front["empty-fallback"] = "REJECT"
    return front


def _exclude_generated_landings(front: dict[str, Any], landing_names: set[str]) -> None:
    if not (front.get("include-all") or front.get("include-all-proxies")):
        return
    generated_pattern = _generated_exclusion_pattern(landing_names)
    existing = front.get("exclude-filter")
    if existing:
        front["exclude-filter"] = f"{existing}|{generated_pattern}"
    else:
        front["exclude-filter"] = generated_pattern


def _validated_source(source_yaml: str) -> tuple[str, dict[str, Any]]:
    source_yaml = _validate_source_text(source_yaml)
    config = _validate_source_root(_load_yaml(source_yaml))
    _validate_source_schema(config)
    _validate_unique_runtime_names(config)
    _validate_source_references(config)
    _validate_acyclic(config)
    return source_yaml, config


def inspect_subscription(source_yaml: str) -> Mapping[str, Any]:
    """Return a credential-free catalog for a complete Mihomo subscription."""
    _, config = _validated_source(source_yaml)
    groups = []
    for group in config["proxy-groups"]:
        groups.append(
            {
                "name": group["name"],
                "type": group["type"],
                "proxies": list(group.get("proxies", [])),
                "use": list(group.get("use", [])),
                "hidden": bool(group.get("hidden", False)),
                "icon": group.get("icon"),
            }
        )
    proxies = [
        {"name": proxy["name"], "type": proxy["type"]}
        for proxy in config.get("proxies", [])
    ]
    providers = sorted((config.get("proxy-providers") or {}).keys())
    return {"groups": groups, "proxies": proxies, "providers": providers}


def compile_subscription(source_yaml: str, policy: Mapping[str, Any]) -> CompileResult:
    """Compile a full Mihomo configuration into a fail-closed configuration."""
    policy = _validate_policy(policy)
    source_yaml, config = _validated_source(source_yaml)
    if source_yaml.startswith(f"{COMPILED_MARKER}\n"):
        if not _marked_document_matches(source_yaml, policy):
            raise CompileError(
                "ALREADY_COMPILED_MISMATCH",
                "The marked configuration does not match the supplied policy.",
            )
        if _matches_compiled_topology(config, policy):
            return CompileResult(source_yaml, _compiled_report(policy, already_compiled=True))
        raise CompileError(
            "ALREADY_COMPILED_MISMATCH",
            "The marked configuration does not match the supplied policy.",
        )
    groups = config["proxy-groups"]
    proxies = config.setdefault("proxies", [])
    group_by_name = {group["name"]: group for group in groups}
    source_names = {item.get("name") for item in proxies + groups}
    providers = config.get("proxy-providers") or {}
    provider_names = set(providers) if isinstance(providers, Mapping) else set()
    managed_front_names = {
        group_name: _generated_names(group_name)[1]
        for group_name, binding in policy["managed_groups"].items()
        if binding.get("front_hop", {}).get("mode", "source") != "direct"
    }
    direct_managed_groups = set(policy["managed_groups"]) - set(managed_front_names)
    generated_landing_names = {
        _generated_names(group_name)[0] for group_name in policy["managed_groups"]
    }
    applied = []

    for group_name in sorted(policy["managed_groups"]):
        binding = policy["managed_groups"][group_name]
        if group_name not in group_by_name:
            raise CompileError(
                "MANAGED_GROUP_MISSING",
                "A managed group is missing from the source configuration.",
            )
        landing_name, front_name = _generated_names(group_name)
        front_mode = binding.get("front_hop", {}).get("mode", "source")
        if landing_name in source_names or (
            front_mode != "direct" and front_name in source_names
        ):
            raise CompileError(
                "GENERATED_NAME_COLLISION",
                "A generated name is already owned by the source configuration.",
            )
        target = group_by_name[group_name]

        front = _build_front_group(
            target,
            binding,
            front_name,
            source_names,
            provider_names,
        )
        if front is not None and isinstance(front.get("proxies"), list):
            if any(reference in direct_managed_groups for reference in front["proxies"]):
                raise _policy_invalid()
            front["proxies"] = [
                managed_front_names.get(reference, reference)
                for reference in front["proxies"]
            ]
        if front is not None:
            _exclude_generated_landings(front, generated_landing_names)

        landing = _build_landing_proxy(
            binding,
            landing_name,
            None if front is None else front_name,
        )

        wrapper = {"name": group_name, "type": "select", "proxies": [landing_name]}
        for key in ("icon", "hidden", "disable-udp"):
            if key in target:
                wrapper[key] = target[key]
        target.clear()
        target.update(wrapper)

        proxies.append(landing)
        if front is not None:
            groups.append(front)
        applied.append(
            {
                "group": group_name,
                "landing": landing_name,
                "front": None if front is None else front_name,
                "front_mode": front_mode,
            }
        )

    _validate_source_schema(config)
    _validate_unique_runtime_names(config)
    _validate_source_references(config)
    _validate_acyclic(config)
    body = yaml.safe_dump(
        config,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )
    return CompileResult(
        _compiled_document(policy, body),
        _build_report(applied, already_compiled=False),
    )
