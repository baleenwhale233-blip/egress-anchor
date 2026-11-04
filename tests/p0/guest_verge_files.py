"""Plan only owned extension-file changes, preserving every original byte for restore."""

from pathlib import Path

import yaml

from guest_overlay_common import atomic_write, digest, managed_script


GUARD_RULE = "DOMAIN,managed.p0.invalid,EA-P0-Deny"
GUARD_GROUP = {"name": "EA-P0-Deny", "type": "select", "proxies": ["REJECT"]}


def file_path(root, relative):
    path = root / relative
    if path.is_symlink() or path.resolve().parent != (root / "profiles").resolve():
        raise ValueError("extension path is outside the fixture profile directory")
    return path


def guarded_merge(before):
    config = yaml.safe_load(before) or {}
    if not isinstance(config, dict):
        raise ValueError("fixture merge must be a mapping")
    changed = False
    for key in list(config):
        if str(key).lower() in ("rules", "proxy-groups"):
            if not isinstance(config[key], list):
                raise ValueError("fixture merge routing fields must be lists")
            guard = GUARD_RULE if str(key).lower() == "rules" else GUARD_GROUP
            config[key] = [guard, *config[key]]
            changed = True
    return yaml.safe_dump(config, sort_keys=False) if changed else before


def plan(root, landing_port, originals=None):
    metadata = yaml.safe_load((root / "profiles.yaml").read_text())
    changes = {}
    owners = {}

    def add(relative, transform):
        if relative in changes:
            return
        path = file_path(root, relative)
        info = path.stat()
        identity = (info.st_dev, info.st_ino)
        if identity in owners and owners[identity] != relative:
            raise ValueError("extension paths alias the same file")
        owners[identity] = relative
        before = (originals or {}).get(relative, path.read_text())
        if "EA-P0-" in before:
            raise ValueError("fixture namespace already exists in an extension")
        after = transform(before)
        if before != after:
            changes[relative] = {"path": relative, "before": before, "after": after,
                                 "after_sha256": digest(after)}

    wrap = lambda text: managed_script(text, landing_port, stage="profile")
    add("profiles/Script.js", lambda text: managed_script(text, landing_port, stage="global"))
    add("profiles/Merge.yaml", guarded_merge)
    for row in metadata["items"]:
        if row.get("type") not in ("remote", "local"):
            continue
        raw = yaml.safe_load(file_path(root, "profiles/" + row["file"]).read_text())
        if any(str(item.get("name", "")).startswith("EA-P0-")
               for key in ("proxies", "proxy-groups") for item in raw.get(key, [])):
            raise ValueError("fixture namespace collides with source nodes")
        options = row["option"]
        for key, suffix in (("script", ".js"), ("merge", ".yaml"), ("rules", ".yaml"), ("groups", ".yaml")):
            uid = options[key]
            if not isinstance(uid, str) or Path(uid).name != uid:
                raise ValueError("invalid native extension ID")
            if key in ("script", "merge"):
                transform = wrap if key == "script" else guarded_merge
            else:
                guard = GUARD_RULE if key == "rules" else GUARD_GROUP

                def transform(text, guard=guard):
                    seq = yaml.safe_load(text) or {}
                    if not isinstance(seq, dict) or not isinstance(seq.get("prepend", []), list):
                        raise ValueError("fixture sequence must have a prepend list")
                    seq["prepend"] = [guard, *seq.get("prepend", [])]
                    return yaml.safe_dump(seq, sort_keys=False)
            add(f"profiles/{uid}{suffix}", transform)
    return list(changes.values())


def preflight(root, changes, restoring=False):
    for row in changes:
        current = file_path(root, row["path"]).read_text()
        allowed = (row["before"], row["after"]) if restoring else (row["before"],)
        if current not in allowed:
            raise ValueError("restore conflict: a native extension changed; nothing was overwritten")


def write_plan(root, changes, restoring=False):
    preflight(root, changes, restoring)
    for row in changes:
        path = file_path(root, row["path"])
        current = path.read_text()
        allowed = (row["before"], row["after"]) if restoring else (row["before"],)
        if current not in allowed:
            raise ValueError("restore conflict: an extension changed during the operation")
        target = row["before"] if restoring else row["after"]
        if current != target:
            atomic_write(path, target)
