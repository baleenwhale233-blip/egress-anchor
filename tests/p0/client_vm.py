"""Own one isolated Linux guest; no host client files, mounts, TUN or proxy changes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import socket
import subprocess
import time

from client_vm_seed import DIRECTORY, INSTANCE, prepare, read_state, save_state, verify_assets


def qmp(command):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(4)
        connection.connect(str(DIRECTORY / "qmp.sock"))
        stream = connection.makefile("rwb")
        greeting = stream.readline()
        if not greeting:
            raise ConnectionResetError("owned QMP connection closed")
        json.loads(greeting)

        def execute(name):
            stream.write((json.dumps({"execute": name}) + "\n").encode())
            stream.flush()
            while True:
                line = stream.readline()
                if not line:
                    raise ConnectionResetError("owned QMP connection closed")
                result = json.loads(line)
                if "error" in result:
                    raise ValueError(result["error"])
                if "return" in result:
                    return result["return"]

        execute("qmp_capabilities")
        if execute("query-name").get("name") != INSTANCE:
            raise ValueError("refusing to control a different VM")
        return execute(command)


def start(online):
    state = read_state()
    monitor = DIRECTORY / "qmp.sock"
    if monitor.exists():
        try:
            qmp("query-status")
        except (ConnectionRefusedError, FileNotFoundError):
            monitor.unlink()
        else:
            if state["online"] != online:
                raise ValueError("stop the test VM before changing its network mode")
            return state
    args = [
        "qemu-system-aarch64", "-name", INSTANCE, "-M", "virt,gic-version=3", "-accel", "hvf",
        "-cpu", "host", "-smp", "2", "-m", "2048", "-display", "none", "-monitor", "none",
        "-drive", "if=pflash,format=raw,readonly=on,file=/opt/homebrew/share/qemu/edk2-aarch64-code.fd",
        "-drive", f"if=virtio,format=qcow2,file={DIRECTORY / 'root.qcow2'}",
        "-drive", f"if=virtio,format=raw,readonly=on,file={DIRECTORY / 'seed.iso'}",
        "-device", "virtio-rng-pci", "-device", "virtio-net-pci,netdev=net0",
        "-netdev", f"user,id=net0,restrict={'off' if online else 'on'},hostfwd=tcp:127.0.0.1:{state['ssh_port']}-:22",
        "-qmp", f"unix:{monitor},server=on,wait=off", "-serial", f"file:{DIRECTORY / 'serial.log'}",
    ]
    with (DIRECTORY / "qemu.log").open("a") as log:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    time.sleep(0.5)
    if process.poll() is not None:
        raise ValueError("QEMU exited; inspect the fixture's qemu.log")
    state.update({"online": online, "pid": process.pid, "isolation_verified": False})
    save_state(state)
    return state


def ssh_arguments():
    state = read_state()
    return ["ssh", "-F", "/dev/null", "-T", "-i", str(DIRECTORY / "id_ed25519"),
            "-p", str(state["ssh_port"]), "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"UserKnownHostsFile={DIRECTORY / 'known_hosts'}",
            "-o", "GlobalKnownHostsFile=/dev/null", "egress-test@127.0.0.1"]


def stop():
    qmp("system_powerdown")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            qmp("query-status")
        except (ConnectionRefusedError, ConnectionResetError, FileNotFoundError):
            state = read_state()
            state["isolation_verified"] = False
            save_state(state)
            return {"powered_down_owned_vm": True}
        time.sleep(0.2)
    raise ValueError("guest shutdown is still pending; no process was force-stopped")


def guest(arguments):
    return subprocess.call([*ssh_arguments(), shlex.join(arguments)])


def copy_assets():
    specs = verify_assets()
    paths = [str(DIRECTORY / specs[name]["file"]) for name in ("flclash", "flclash_0894", "clash_verge_rev", "legacy_keybinder")]
    return copy_files(paths)


def copy_files(paths):
    if guest(["mkdir", "-p", "/home/egress-test/p0-assets"]):
        raise ValueError("could not prepare guest asset directory")
    options = ["-P" if value == "-p" else value for value in ssh_arguments()[1:-1] if value != "-T"]
    subprocess.run(["scp", *options, *paths, "egress-test@127.0.0.1:/home/egress-test/p0-assets/"], check=True)
    return {"copied": len(paths)}


def collect_evidence():
    destination = DIRECTORY / "evidence" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, mode=0o700)
    options = ["-P" if value == "-p" else value for value in ssh_arguments()[1:-1] if value != "-T"]
    for name in ("reports", "fetches.jsonl", "subscriptions.json", "ports.json"):
        source = f"egress-test@127.0.0.1:/home/egress-test/p0-fixtures/{name}"
        subprocess.run(["scp", *options, "-r", source, str(destination)], check=True)
    paths = sorted(path for path in destination.rglob("*") if path.is_file())
    hashes = {str(path.relative_to(destination)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    (destination / "sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
    return {"evidence": str(destination), "files": len(paths)}


def ui():
    state = read_state()
    if state["online"] or not state.get("isolation_verified"):
        raise ValueError("verify offline isolation before starting the desktop")
    copy_files([str(Path(__file__).with_name("guest_desktop.py"))])
    guest(["sh", "-c", "nohup python3 /home/egress-test/p0-assets/guest_desktop.py > /home/egress-test/p0-desktop.log 2>&1 < /dev/null &"])
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    ssh = ssh_arguments()
    args = [*ssh[:-1], "-N", "-o", "ExitOnForwardFailure=yes", "-L", f"127.0.0.1:{port}:127.0.0.1:6080", ssh[-1]]
    with (DIRECTORY / "ui-tunnel.log").open("a") as log:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    state.update({"ui_port": port, "ui_tunnel_pid": process.pid})
    save_state(state)
    return {"url": f"http://127.0.0.1:{port}/vnc.html?autoconnect=true&resize=scale"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ["prepare", "status", "stop", "copy-assets", "collect-evidence", "ui"]:
        commands.add_parser(action)
    boot = commands.add_parser("start")
    boot.add_argument("--online", action="store_true", help="package bootstrap only; test apps offline")
    remote = commands.add_parser("guest")
    remote.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare()
    elif args.action == "start":
        result = start(args.online)
    elif args.action == "status":
        result = {**read_state(), "qmp": qmp("query-status")}
    elif args.action == "stop":
        result = stop()
    elif args.action == "copy-assets":
        result = copy_assets()
    elif args.action == "collect-evidence":
        result = collect_evidence()
    elif args.action == "ui":
        result = ui()
    else:
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            raise ValueError("a guest command is required")
        return guest(command)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
