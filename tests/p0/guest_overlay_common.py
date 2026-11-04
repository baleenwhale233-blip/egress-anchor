"""Shared script and process operations for the disposable guest's P0 overlays."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from guest_client_context import flclash_context


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def atomic_write(path, text):
    fd, name = tempfile.mkstemp(prefix=".p0-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if Path(name).exists():
            Path(name).unlink()


def managed_script(user_script, landing_port, *, stage="standalone"):
    settings = {"userSource": user_script, "landingPort": landing_port, "stage": stage}
    return "var eaP0Settings = " + json.dumps(settings) + ";\n" + Path(__file__).with_name("guest_overlay_script.js").read_text()


def run_client(action, client):
    launcher = Path(__file__).with_name("guest_client.py")
    subprocess.run([sys.executable, str(launcher), action, client], check=True)


def stop_client(client):
    directory = Path("/home/egress-test/p0-desktop") if client == "verge" else flclash_context(client)["desktop"]
    pid = int((directory / f"{client}.pid").read_text())
    run_client("stop", client)
    deadline = time.monotonic() + 8
    while Path(f"/proc/{pid}/exe").exists():
        if time.monotonic() >= deadline:
            raise ValueError("test client has not stopped; nothing was replaced")
        time.sleep(0.1)
