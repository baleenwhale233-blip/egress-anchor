"""Prepare only the disposable P0 guest's assets, key and NoCloud seed."""

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

import yaml


DIRECTORY = Path(__file__).resolve().parents[2] / "dev-data/p0/client-vm"
ASSETS = Path(__file__).with_name("client_vm_assets.json")
INSTANCE = "egress-p0-client-vm"


def read_state():
    state = json.loads((DIRECTORY / "state.json").read_text())
    if state.get("instance") != INSTANCE:
        raise ValueError("directory is not owned by the P0 VM fixture")
    return state


def save_state(state):
    path = DIRECTORY / "state.json"
    path.write_text(json.dumps(state, indent=2) + "\n")
    os.chmod(path, 0o600)


def verify_assets():
    specs = json.loads(ASSETS.read_text())
    for name, spec in specs.items():
        if not isinstance(spec, dict):
            continue
        digest = hashlib.sha256()
        with (DIRECTORY / spec["file"]).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != spec["sha256"]:
            raise ValueError(f"checksum mismatch for {name}; finish the pinned download first")
    return specs


def prepare():
    DIRECTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
    verify_assets()
    if not (DIRECTORY / "state.json").exists():
        with socket.socket() as connection:
            connection.bind(("127.0.0.1", 0))
            port = connection.getsockname()[1]
        save_state({"instance": INSTANCE, "ssh_port": port, "online": False})
    state = read_state()
    key = DIRECTORY / "id_ed25519"
    if not key.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    seed = DIRECTORY / "seed"
    seed.mkdir(exist_ok=True)
    config = {
        "hostname": INSTANCE,
        "users": [{"name": "egress-test", "shell": "/bin/bash", "groups": ["sudo"],
                   "sudo": "ALL=(ALL) NOPASSWD:ALL", "lock_passwd": True,
                   "ssh_authorized_keys": [(key.with_suffix(".pub")).read_text().strip()]}],
        "ssh_pwauth": False, "disable_root": True,
        "package_update": True, "package_upgrade": False,
        "packages": ["xvfb", "openbox", "x11vnc", "novnc", "websockify", "dbus-x11", "xdg-utils",
                     "python3-yaml", "sqlite3", "libgtk-3-0t64", "libwebkit2gtk-4.1-0",
                     "libayatana-appindicator3-1", "libfuse2t64", "libkeybinder-3.0-0", "fonts-noto-cjk"],
        "runcmd": [["touch", "/home/egress-test/p0-bootstrap-ready"],
                   ["chown", "egress-test:egress-test", "/home/egress-test/p0-bootstrap-ready"]],
    }
    if not (DIRECTORY / "seed.iso").exists():
        (seed / "user-data").write_text("#cloud-config\n" + yaml.safe_dump(config))
        (seed / "meta-data").write_text(f"instance-id: {INSTANCE}\nlocal-hostname: {INSTANCE}\n")
        subprocess.run(["hdiutil", "makehybrid", "-o", str(DIRECTORY / "seed.iso"), str(seed),
                        "-iso", "-joliet", "-default-volume-name", "cidata"], check=True)
    if not (DIRECTORY / "root.qcow2").exists():
        subprocess.run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b",
                        str(DIRECTORY / "noble-server-cloudimg-arm64.img"),
                        str(DIRECTORY / "root.qcow2"), "12G"], check=True)
    return state
