"""Extract the pinned older client for a separate VM user; never run on the host."""

import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import socket
import subprocess
import time


def main():
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm" or os.getuid() != 0:
        raise SystemExit("Run as root only inside the disposable P0 guest")
    package = Path("/home/egress-test/p0-assets/FlClash-0.8.94-linux-arm64.deb")
    expected = "d48739dfb7f2f78498d93491a2f979e3e3584922d8abeff65777a6c61e0821fc"
    if hashlib.sha256(package.read_bytes()).hexdigest() != expected:
        raise ValueError("older client checksum mismatch")
    account = "egress-0894"
    try:
        user = pwd.getpwnam(account)
    except KeyError:
        subprocess.run(["useradd", "-m", "-s", "/bin/bash", account], check=True)
        user = pwd.getpwnam(account)
    if user.pw_uid in (0, pwd.getpwnam("egress-test").pw_uid):
        raise ValueError("the older client must have a separate unprivileged identity")
    target = Path("/opt/p0-flclash-0894")
    marker = target / ".p0-owned"
    if target.exists() and not marker.exists():
        raise ValueError("will not replace an unowned installation directory")
    target.mkdir(exist_ok=True)
    if not marker.exists():
        subprocess.run(["dpkg-deb", "-x", str(package), str(target)], check=True)
        marker.write_text(expected)
    elif marker.read_text() != expected:
        raise ValueError("test installation identity changed")
    code = Path("/opt/egress-p0-code")
    code.mkdir(mode=0o755, exist_ok=True)
    sources = list(Path("/home/egress-test/p0-assets").glob("guest_*.py"))
    sources += [Path("/home/egress-test/p0-assets") / name for name in ("loopback_fixtures.py", "guest_overlay_script.js")]
    for path in sources:
        shutil.copyfile(path, code / path.name)
        (code / path.name).chmod(0o644)
    user_root = Path(user.pw_dir)
    for relative in ("p0-desktop", "p0-fixtures", "p0-fixtures/reports"):
        path = user_root / relative
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chown(path, user.pw_uid, user.pw_gid)
    ports = user_root / "p0-fixtures/ports.json"
    shutil.copyfile("/home/egress-test/p0-fixtures/ports.json", ports)
    os.chown(ports, user.pw_uid, user.pw_gid)
    subprocess.run(["loginctl", "enable-linger", account], check=True)
    subprocess.run(["systemctl", "start", f"user@{user.pw_uid}.service"], check=True)
    runtime = f"/run/user/{user.pw_uid}"
    for _ in range(30):
        if Path(runtime + "/bus").exists():
            break
        time.sleep(0.1)
    else:
        raise ValueError("isolated test user's D-Bus did not become ready")
    session = {"DISPLAY": ":99", "GDK_BACKEND": "x11", "XDG_SESSION_TYPE": "x11",
               "LIBGL_ALWAYS_SOFTWARE": "1", "XDG_RUNTIME_DIR": runtime,
               "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + runtime + "/bus"}
    path = user_root / "p0-desktop/session.json"
    path.write_text(json.dumps(session))
    path.chmod(0o600)
    os.chown(path, user.pw_uid, user.pw_gid)
    assignments = [key + "=" + value for key, value in session.items()]
    subprocess.run(["sudo", "-H", "-u", account, "env", *assignments,
                    "dbus-update-activation-environment", "--systemd", "DISPLAY", "GDK_BACKEND", "XDG_SESSION_TYPE"], check=True)
    print(json.dumps({"prepared_version": "0.8.94", "test_user": account, "shared_client_data": False}))


if __name__ == "__main__":
    main()
