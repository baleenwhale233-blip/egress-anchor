"""Start an official test client only inside the named disposable Linux guest."""

import argparse
import json
import os
from pathlib import Path
import platform
import pwd
import signal
import socket
import subprocess

from guest_client_context import FLCLASH, flclash_context


DIRECTORY = Path("/home/egress-test/p0-desktop")
CLIENTS = {"verge": "/usr/bin/clash-verge", **{key: value["binary"] for key, value in FLCLASH.items()}}


def main():
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("This fixture may run only inside the disposable P0 guest")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop"])
    parser.add_argument("client", choices=CLIENTS)
    args = parser.parse_args()
    directory = DIRECTORY
    if args.client == "flclash-0894":
        if pwd.getpwuid(os.getuid()).pw_name != "egress-0894":
            raise ValueError("run this variant as the isolated egress-0894 user")
        directory = flclash_context(args.client)["desktop"]
    pid_file = directory / f"{args.client}.pid"
    binary = Path(CLIENTS[args.client]).resolve()
    if args.action == "stop":
        pid = int(pid_file.read_text())
        executable = Path(f"/proc/{pid}/exe")
        if executable.exists():
            if executable.resolve() != binary or os.getpgid(pid) != pid:
                raise SystemExit("Refusing to stop a process not owned by the test launcher")
            os.killpg(pid, signal.SIGTERM)
        print(json.dumps({"stopped_test_client": args.client}))
        return
    if pid_file.exists() and Path(f"/proc/{pid_file.read_text().strip()}/exe").exists():
        raise SystemExit("The test client is already running")
    env = os.environ.copy()
    env.update(json.loads((directory / "session.json").read_text()))
    with (directory / f"{args.client}.log").open("a") as log:
        process = subprocess.Popen([str(binary)], env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True)
    pid_file.write_text(str(process.pid))
    print(json.dumps({"started_test_client": args.client, "pid": process.pid}))


if __name__ == "__main__":
    main()
