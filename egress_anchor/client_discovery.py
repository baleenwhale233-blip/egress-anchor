"""On-demand, read-only discovery; owns observations independently of saved services."""

from copy import deepcopy
from pathlib import Path
import sys
import threading
import time

from .flclash_readonly import ClientReadError, FlClashLocation, read_flclash


def default_locations(home=None, platform=None):
    home, platform = Path(home or Path.home()), platform or sys.platform
    if platform == "darwin":
        return [FlClashLocation(home / "Library/Application Support/com.follow.clash",
            home / "Library/Preferences/com.follow.clash.plist",
            Path("/Applications/FlClash.app/Contents/Info.plist"))]
    if platform.startswith("linux"):
        return [FlClashLocation(home / ".local/share" / name,
            home / ".local/share" / name / "shared_preferences.json")
            for name in ("com.follow.clash", "FlClash")]
    return []


class ClientDiscovery:
    def __init__(self, *, locations=(), reader=read_flclash, now=time.time):
        # Library/test callers opt into specific locations. Only the CLI enables
        # host discovery, so fixtures cannot accidentally inspect daily clients.
        self.locations = tuple(locations)
        self.reader, self.now = reader, now
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._observation = self._base("not_connected", None)

    @staticmethod
    def _base(state, checked_at):
        return {"state": state, "name": None, "checked_at": checked_at,
            "can_apply": False, "can_probe": False, "access": "read_only",
            "profile": None, "runtime": {"state": "unconfirmed"}, "candidates": [],
            "reason": "尚未检查客户端。"}

    def snapshot(self):
        with self._lock:
            return deepcopy(self._observation)

    def discover(self):
        with self._scan_lock:
            result = self._base("not_found", int(self.now()))
            candidates = []
            seen = set()
            for location in self.locations:
                try:
                    root = location.root.resolve()
                    if root in seen:
                        continue
                    seen.add(root)
                    if not location.root.exists():
                        continue
                    candidate = self.reader(location)
                except (ClientReadError, OSError) as error:
                    candidate = {"state": "unsupported" if getattr(error, "code", None) == "unsupported" else "unavailable",
                        "name": "FlClash", "reason": str(error) if isinstance(error, ClientReadError) else "无法读取客户端目录。",
                        "runtime": {"state": "unconfirmed"}, "profile": None}
                candidates.append(candidate)
            if len(candidates) == 1:
                result.update(candidates[0])
                if result["state"] == "discovered":
                    result["reason"] = "已读取客户端信息。自动应用与服务检测尚未开放。"
            elif candidates:
                result.update(state="multiple", reason="发现多个 FlClash 数据目录，尚未确定接入目标。")
            else:
                result["reason"] = "未找到 FlClash 数据，请先安装并打开客户端。"
            result["candidates"] = candidates
            with self._lock:
                self._observation = result
            return deepcopy(result)
