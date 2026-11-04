"""Use Verge's authenticated public subscription deep-link endpoint in the P0 guest."""

import json
from pathlib import Path
import platform
import socket
import time
from urllib.parse import urlencode
import urllib.request

import yaml


def main():
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("Use only the disposable P0 guest")
    root = Path("/home/egress-test/.local/share/io.github.clash-verge-rev.clash-verge-rev")
    source = "http://localhost:18880/b.yaml"
    existing = yaml.safe_load((root / "profiles.yaml").read_text())
    for row in existing["items"]:
        if row.get("url") == source:
            print(json.dumps({"reused_native_profile": True, "id": row["uid"], "name": row.get("name")}))
            return
    record = json.loads((root / "singleton-instance.json").read_text())
    scheme = "clash://install-config?" + urlencode({"name": "Fresh-P0", "url": source})
    address = f'http://127.0.0.1:{int(record["port"])}/commands/scheme?' + urlencode({"param": scheme})
    request = urllib.request.Request(address, headers={"x-instance-token": record["token"]})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=4) as response:
        if response.status != 200:
            raise ValueError("native deep-link import was not accepted")
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        metadata = yaml.safe_load((root / "profiles.yaml").read_text())
        for row in metadata["items"]:
            if row.get("url") == source:
                print(json.dumps({"native_profile_imported": True, "id": row["uid"], "name": row.get("name")}))
                return
        time.sleep(0.1)
    raise ValueError("native import did not produce a persisted test profile")


if __name__ == "__main__":
    main()
