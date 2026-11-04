"""Virtual X11 desktop inside the guest; deliberately contains no proxy-client launch."""

import json
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import time


DIRECTORY = Path("/home/egress-test/p0-desktop")


def main():
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("This fixture may run only inside the disposable P0 guest")
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        os.execvp("dbus-run-session", ["dbus-run-session", "--", "python3", __file__])
    DIRECTORY.mkdir(exist_ok=True, mode=0o700)
    os.environ.update({"DISPLAY": ":99", "GDK_BACKEND": "x11", "XDG_SESSION_TYPE": "x11",
                       "LIBGL_ALWAYS_SOFTWARE": "1", "GTK_A11Y": "always"})
    children = []

    def launch(name, args):
        with (DIRECTORY / f"{name}.log").open("a") as log:
            child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        children.append(child)
        return child

    def stop(*_args):
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    launch("xvfb", ["Xvfb", ":99", "-screen", "0", "1280x800x24", "-nolisten", "tcp", "-ac"])
    for _ in range(30):
        if Path("/tmp/.X11-unix/X99").exists():
            break
        time.sleep(0.1)
    else:
        stop()
    subprocess.run(["dbus-update-activation-environment", "--systemd", "DISPLAY", "GDK_BACKEND",
                    "XDG_SESSION_TYPE"], check=True)
    launch("openbox", ["openbox"])
    launch("vnc", ["x11vnc", "-display", ":99", "-listen", "127.0.0.1", "-rfbport", "5900",
                   "-nopw", "-forever", "-shared", "-noxdamage"])
    launch("websockify", ["websockify", "--web", "/usr/share/novnc", "127.0.0.1:6080", "127.0.0.1:5900"])
    session = {key: os.environ[key] for key in ("DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "GDK_BACKEND",
                                               "XDG_SESSION_TYPE", "LIBGL_ALWAYS_SOFTWARE", "GTK_A11Y")}
    (DIRECTORY / "session.json").write_text(json.dumps(session))
    while True:
        if any(child.poll() is not None for child in children):
            stop()
        time.sleep(1)


if __name__ == "__main__":
    main()
