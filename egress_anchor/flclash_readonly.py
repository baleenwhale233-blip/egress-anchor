"""Bounded snapshots of FlClash metadata. The source directory is never opened for write.

SQLite read-only connections may create shared-memory files for WAL databases.
Copy the database and WAL into a private temporary directory instead; compare
source identities before and after reading so a changing snapshot is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import plistlib
import sqlite3
from tempfile import TemporaryDirectory

import yaml

from .diagnostics import DiagnosticsError, MihomoController


@dataclass(frozen=True)
class FlClashLocation:
    root: Path
    preferences: Path
    app_info: Path | None = None


class ClientReadError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _identity(path):
    try:
        value = path.stat()
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    except FileNotFoundError:
        return None


def _read(path, limit):
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ClientReadError("too_large", "客户端数据过大，暂无法读取。")
    return value


def _preferences(path, raw):
    value = plistlib.loads(raw) if path.suffix == ".plist" else json.loads(raw)
    config = value.get("flutter.config")
    if isinstance(config, str):
        config = json.loads(config)
    if not isinstance(config, dict):
        raise ValueError("missing preferences")
    selected = config.get("currentProfileId")
    if selected is not None and type(selected) is not int:
        raise ValueError("invalid profile")
    return selected


def _profile(database_bytes, wal_bytes, selected):
    with TemporaryDirectory(prefix="egress-flclash-read-") as directory:
        snapshot = Path(directory) / "database.sqlite"
        snapshot.write_bytes(database_bytes)
        if wal_bytes:
            snapshot.with_name("database.sqlite-wal").write_bytes(wal_bytes)
        connection = sqlite3.connect(snapshot, timeout=0.2)
        try:
            connection.execute("PRAGMA query_only=ON")
            schema = connection.execute("PRAGMA user_version").fetchone()[0]
            if schema not in {2, 21}:
                raise ClientReadError("unsupported", "此版本的数据结构尚不支持读取。")
            if selected is None:
                return schema, None
            column = "overwrite_type" if schema == 2 else "extend_type"
            row = connection.execute(f"SELECT id,label,{column} FROM profiles WHERE id=?", (selected,)).fetchone()
            if row is None:
                raise ClientReadError("profile_missing", "当前订阅记录不可用，请在 FlClash 重新选择订阅后检查。")
            label = row[1] if isinstance(row[1], str) else "未命名订阅"
            label = "".join(c if c.isprintable() else " " for c in label)[:160]
            return schema, {"id": row[0], "name": label, "mode": row[2] if row[2] in {"standard", "script"} else "unknown"}
        finally:
            connection.close()


def _runtime(raw, controller_factory):
    if raw is None:
        return {"state": "unconfirmed", "reason": "尚未找到运行配置，请先打开 FlClash。"}
    config = yaml.safe_load(raw)
    if not isinstance(config, dict):
        raise ValueError("invalid runtime")
    address = config.get("external-controller")
    if not isinstance(address, str) or not address:
        return {"state": "unsupported", "reason": "未提供本机 HTTP 控制接口，暂无法读取运行信息。"}
    if address.startswith(":"):
        address = "127.0.0.1" + address
    elif address.startswith("0.0.0.0:"):
        address = "127.0.0.1:" + address.split(":", 1)[1]
    # Validation in MihomoController rejects remote addresses and redirects.
    try:
        controller = controller_factory("http://" + address, config.get("secret", ""))
        return dict(controller.runtime_info())
    except DiagnosticsError as error:
        return {"state": "unsupported" if error.code == "CONTROLLER_INVALID" else "unreachable",
                "reason": "无法读取本机控制接口，请确认 FlClash 已启动且允许本机访问。"}


def read_flclash(location, *, controller_factory=MihomoController):
    root = location.root
    paths = [root / "database.sqlite", root / "database.sqlite-wal", location.preferences,
             root / "config.yaml", root / "database.sqlite-journal"]
    if location.app_info:
        paths.append(location.app_info)
    try:
        before = {path: _identity(path) for path in paths}
        if before[paths[0]] is None or before[location.preferences] is None:
            raise ClientReadError("incomplete", "客户端数据尚不完整，请先打开 FlClash 并选择订阅。")
        # An active rollback journal needs recovery. Never recover the user's
        # database, or mistake uncommitted pages for the selected profile.
        if before[paths[4]] is not None and before[paths[4]][2] > 0:
            raise ClientReadError("changed", "客户端正在更新数据，请稍后重新检查。")
        content = {path: _read(path, 64 * 1024 * 1024 if path in paths[:2] else 4 * 1024 * 1024)
                   for path in paths if before[path] is not None}
        selected = _preferences(location.preferences, content[location.preferences])
        schema, profile = _profile(content[paths[0]], content.get(paths[1]), selected)
        version = None
        if location.app_info in content:
            info = plistlib.loads(content[location.app_info])
            value = info.get("CFBundleShortVersionString")
            version = value[:40] if isinstance(value, str) else None
        runtime = _runtime(content.get(root / "config.yaml"), controller_factory)
        if before != {path: _identity(path) for path in paths}:
            raise ClientReadError("changed", "客户端数据刚刚发生变化，请重新检查。")
        return {"state": "discovered", "name": "FlClash", "version": version, "schema": schema,
                "profile": profile, "runtime": runtime}
    except ClientReadError:
        raise
    except PermissionError:
        raise ClientReadError("permission_denied", "无法读取客户端数据，请检查当前用户的文件访问权限。") from None
    except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error, yaml.YAMLError, plistlib.InvalidFileException):
        raise ClientReadError("unreadable", "客户端数据暂时无法读取，请打开 FlClash 后重试。") from None
