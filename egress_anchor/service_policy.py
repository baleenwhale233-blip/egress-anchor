"""Pure service-policy input validation, independent of client and source groups.

``domains`` is the resolved compiler input. Template and user domain storage
remain separate responsibilities; exclusions are retained even when a template
does not currently include their parent domain. Plain domains match themselves
and their subdomains, like Mihomo DOMAIN-SUFFIX rules.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from typing import Any, Mapping, Optional, Sequence


_SERVICE_FIELDS = {"id", "name", "domains", "exclude_domains", "exit", "strict"}
_EXIT_KINDS = {"inherit", "direct", "airport", "isp"}
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\Z")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class ServicePolicyError(ValueError):
    """A field-addressable failure that never echoes unvalidated input."""

    def __init__(self, code: str, message: str, field: str, *, service_ids=()):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.field = field
        self.service_ids = tuple(service_ids)


def _suffixes(domain: str) -> tuple[str, ...]:
    labels = domain.split(".")
    return tuple(".".join(labels[index:]) for index in range(len(labels) - 1))


def _normalize_domain(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("A plain domain name is required.")
    domain = value.strip().lower()
    if domain.endswith("."):
        domain = domain[:-1]
    try:
        domain = domain.encode("idna").decode("ascii")
        domain.encode("ascii").decode("idna")
    except UnicodeError:
        raise ValueError("A plain domain name is required.") from None
    labels = domain.split(".")
    if (len(labels) < 2 or len(domain) > 253
            or not all(_LABEL.fullmatch(label) for label in labels)):
        raise ValueError("A plain domain name is required.")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return domain
    raise ValueError("Use a domain name rather than an IP address.")


@dataclass(frozen=True)
class ServiceExit:
    kind: str
    reference: Optional[str] = None


@dataclass(frozen=True)
class ServicePolicy:
    id: str
    name: str
    domains: tuple[str, ...]
    exclude_domains: tuple[str, ...]
    exit: ServiceExit
    strict: bool = False

    def matches_domain(self, hostname: str) -> bool:
        try:
            suffixes = _suffixes(_normalize_domain(hostname))
        except ValueError:
            return False
        return (any(domain in self.domains for domain in suffixes)
                and not any(domain in self.exclude_domains for domain in suffixes))


def _invalid(field: str) -> ServicePolicyError:
    return ServicePolicyError(
        "SERVICE_POLICY_INVALID", "The service policy contains an invalid field.", field,
    )


def _text(value: Any, field: str, limit: int, *, strip: bool = True) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise _invalid(field)
    if not value.isprintable():
        raise _invalid(field)
    return value.strip() if strip else value


def _domains(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise _invalid(field)
    result = set()
    for index, entry in enumerate(value):
        try:
            result.add(_normalize_domain(entry))
        except ValueError:
            raise ServicePolicyError(
                "SERVICE_DOMAIN_INVALID", "Enter a plain domain without a URL, port or wildcard.",
                f"{field}[{index}]",
            ) from None
    return tuple(sorted(result))


def _exit(value: Any, field: str) -> ServiceExit:
    if not isinstance(value, Mapping) or set(value) - {"kind", "reference"}:
        raise _invalid(field)
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in _EXIT_KINDS:
        raise _invalid(f"{field}.kind")
    reference = value.get("reference")
    if kind in {"airport", "isp"}:
        reference = _text(reference, f"{field}.reference", 256, strip=False)
    elif reference is not None:
        raise _invalid(f"{field}.reference")
    return ServiceExit(kind, reference)


def _check_conflicts(policies: tuple[ServicePolicy, ...]) -> None:
    # Parent suffixes come first, so only ancestors and exact matches need lookup.
    # Exclusions remove this service's claim; they do not force traffic DIRECT.
    claims: dict[str, list[ServicePolicy]] = {}
    entries = sorted(
        ((domain.count("."), domain, policy.id, policy)
         for policy in policies if policy.exit.kind != "inherit"
         for domain in policy.domains),
        key=lambda entry: entry[:3],
    )
    for _, domain, _, policy in entries:
        if not policy.matches_domain(domain):
            continue
        for suffix in _suffixes(domain):
            for owner in claims.get(suffix, ()):
                if owner.id != policy.id and owner.matches_domain(domain):
                    raise ServicePolicyError(
                        "SERVICE_DOMAIN_CONFLICT", "Two services claim overlapping domains.",
                        "services", service_ids=sorted((owner.id, policy.id)),
                    )
        claims.setdefault(domain, []).append(policy)


def parse_service_policies(value: Sequence[Mapping[str, Any]]) -> tuple[ServicePolicy, ...]:
    """Normalize policies and reject ambiguous routing before any client writes.

    An inherited service emits no independent routing claim. Airport references
    name an existing node/group; ISP references are resource IDs. Availability,
    chain construction and client support are checked by the compiler/adapter.
    """
    if not isinstance(value, (list, tuple)):
        raise _invalid("services")
    policies = []
    seen = set()
    for index, raw in enumerate(value):
        path = f"services[{index}]"
        if not isinstance(raw, Mapping) or set(raw) - _SERVICE_FIELDS:
            raise _invalid(path)
        service_id = raw.get("id")
        if not isinstance(service_id, str) or not _ID.fullmatch(service_id):
            raise _invalid(f"{path}.id")
        if service_id in seen:
            raise ServicePolicyError(
                "SERVICE_ID_DUPLICATE", "Service IDs must be unique.", f"{path}.id",
                service_ids=(service_id,),
            )
        seen.add(service_id)
        name = _text(raw.get("name"), f"{path}.name", 80)
        exit_choice = _exit(raw.get("exit"), f"{path}.exit")
        domains = _domains(raw.get("domains", []), f"{path}.domains")
        exclusions = _domains(raw.get("exclude_domains", []), f"{path}.exclude_domains")
        if exit_choice.kind != "inherit" and not domains:
            raise ServicePolicyError(
                "SERVICE_DOMAINS_REQUIRED", "An independent service needs at least one domain.",
                f"{path}.domains", service_ids=(service_id,),
            )
        strict = raw.get("strict", False)
        if not isinstance(strict, bool):
            raise _invalid(f"{path}.strict")
        if strict and exit_choice.kind == "inherit":
            raise ServicePolicyError(
                "SERVICE_STRICT_REQUIRES_OVERRIDE", "Strict mode needs an explicit service exit.",
                f"{path}.strict", service_ids=(service_id,),
            )
        policies.append(ServicePolicy(service_id, name, domains, exclusions, exit_choice, strict))
    result = tuple(policies)
    _check_conflicts(result)
    return result
