"""Protection intent and capability descriptions; never imply runtime enforcement."""

from dataclasses import dataclass
from typing import Mapping


class ProtectionError(ValueError):
    def __init__(self, field, message="保护设置格式不正确。"):
        super().__init__(message)
        self.field = field


ITEMS = {
    "udp": {"label": "UDP", "scope": "service", "recommended": "block",
            "options": {"inherit": "沿用订阅", "block": "拒绝 UDP"}},
    "dns": {"label": "DNS", "scope": "client", "recommended": "via_exit",
            "options": {"inherit": "沿用客户端", "via_exit": "通过指定出口解析"}},
    "ipv6": {"label": "IPv6", "scope": "client", "recommended": "block",
             "options": {"inherit": "沿用客户端", "block": "阻止 IPv6 绕行"}},
}


@dataclass(frozen=True)
class ProtectionPolicy:
    preset: str = "off"
    overrides: tuple[tuple[str, str], ...] = ()

    def as_dict(self):
        return {"preset": self.preset, "overrides": dict(self.overrides)}


def parse_protection(value) -> ProtectionPolicy:
    if not isinstance(value, Mapping) or set(value) - {"preset", "overrides"}:
        raise ProtectionError("protection")
    preset, overrides = value.get("preset"), value.get("overrides", {})
    if not isinstance(preset, str) or preset not in {"off", "recommended", "custom"}:
        raise ProtectionError("protection.preset")
    if not isinstance(overrides, Mapping) or set(overrides) - set(ITEMS):
        raise ProtectionError("protection.overrides")
    if preset != "custom" and overrides:
        raise ProtectionError("protection.overrides", "只有自定义保护可以覆盖具体项目。")
    for key, selected in overrides.items():
        if not isinstance(selected, str) or selected not in ITEMS[key]["options"]:
            raise ProtectionError(f"protection.overrides.{key}")
    return ProtectionPolicy(preset, tuple(sorted(overrides.items())))


def protection_capabilities():
    reason = "完整保护尚未接入，暂不能开启。"
    return {"presets": [
        {"id": "off", "label": "关闭", "supported": True, "reason": None},
        {"id": "recommended", "label": "推荐严格保护", "supported": False, "reason": reason},
        {"id": "custom", "label": "自定义", "supported": False, "reason": reason},
    ], "items": protection_details(ProtectionPolicy(), "inherit"),
        "probe_settings": {"supported": False, "mode": "recommended",
        "interval_seconds": 300, "timeout_seconds": 8, "reason": "服务检测尚未开放，参数暂不生效。"}}


def protection_details(policy, exit_kind):
    """Configured intent includes the existing mandatory ISP transport baseline."""
    overrides = dict(policy.overrides)
    rows = []
    for key, item in ITEMS.items():
        value = "inherit" if policy.preset == "off" else overrides.get(key, item["recommended"])
        mandatory = key == "udp" and exit_kind == "isp"
        if mandatory:
            value = "block"
        rows.append({"id": key, **item, "options": dict(item["options"]), "configured": value,
            "editable": False, "mandatory": mandatory,
            "reason": "指定 ISP 的基础约束，关闭额外保护仍保留。" if mandatory else
                      "需要客户端保护能力支持；推荐值尚未应用。",
            "runtime": {"state": "unverified", "value": None}})
    rows.append({"id": "webrtc", "label": "WebRTC", "scope": "browser",
        "recommended": "restrict", "configured": "unmanaged", "editable": False,
        "options": {"restrict": "限制非代理连接", "unmanaged": "需在浏览器中设置"},
        "reason": "浏览器设置不能由服务路由代替，需单独设置与验证。",
        "runtime": {"state": "unverified", "value": None}})
    return rows
