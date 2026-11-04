"""Fixed disposable-guest identities; no caller-supplied data or executable paths."""

from pathlib import Path


FLCLASH = {
    "flclash": {"home": "/home/egress-test", "label": "FlClash 0.9.2",
                "binary": "/usr/bin/FlClash"},
    "flclash-0894": {"home": "/home/egress-0894", "label": "FlClash 0.8.94",
                     "binary": "/opt/p0-flclash-0894/usr/share/FlClash/FlClash", "data_dir": "FlClash"},
}


def flclash_context(client):
    if client not in FLCLASH:
        raise ValueError("unknown fixed FlClash test identity")
    value = dict(FLCLASH[client])
    value["root"] = Path(value["home"]) / ".local/share" / value.get("data_dir", "com.follow.clash")
    value["fixtures"] = Path(value["home"]) / "p0-fixtures"
    value["desktop"] = Path(value["home"]) / "p0-desktop"
    return value
