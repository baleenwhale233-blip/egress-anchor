"""Real native-core regression: hot apply/restore preserve unrelated live TCP flows."""

from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import socket
import subprocess
import sys

from guest_client_probe import connection_chain, native_state, observe
from guest_core_api import request
from loopback_fixtures import ping, tunnel


def run():
    root = Path("/home/egress-test/p0-fixtures")
    ports = json.loads((root / "ports.json").read_text())
    runtime, selected = native_state("verge")
    pid_path = Path("/home/egress-test/p0-desktop/verge.pid")
    pid = pid_path.read_text()
    command = [sys.executable, str(Path(__file__).with_name("guest_verge_overlay.py"))]

    def change(action):
        result = subprocess.run([*command, action], text=True, capture_output=True, timeout=20)
        if result.returncode:
            raise AssertionError(result.stderr)
        assert pid_path.read_text() == pid and Path(f"/proc/{pid}/exe").exists()
        return json.loads(result.stdout)

    def ids():
        flows = request(runtime, "verge", "GET", "/connections")["connections"]
        return {row["id"] for row in flows if row["metadata"].get("host") == "unmanaged.p0.invalid"
                and row["metadata"].get("network") == "tcp"}

    with tunnel(runtime["mixed-port"], "unmanaged.p0.invalid", ports["origin"]) as unrelated:
        ping(unrelated)
        keep_ids = ids()
        assert keep_ids
        with tunnel(runtime["mixed-port"], "managed.p0.invalid", ports["origin"]) as old:
            ping(old)
            flows = request(runtime, "verge", "GET", "/connections")["connections"]
            initial_chain = connection_chain(flows, "managed.p0.invalid", old.getsockname()[1])
            original_route = "direct" if "DIRECT" in initial_chain else "source"
            applied = change("install")
            assert old.recv(1) == b"", "old protected flow was not closed"
        ping(unrelated)
        assert keep_ids <= ids()
        active = observe(True, client="verge", user_rule=True)
        with tunnel(runtime["mixed-port"], "managed.p0.invalid", ports["origin"]) as managed:
            ping(managed)
            restored = change("restore")
            assert managed.recv(1) == b"", "old landing flow survived restore"
        ping(unrelated)
        assert keep_ids <= ids()
        final = observe(False, expected_route=original_route, client="verge", user_rule=True)
    assert final["current_profile"] == selected
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "client_not_restarted": True,
            "unrelated_connection_ids_preserved": True, "protected_old_connections_closed": True,
            "apply": applied, "restore": restored, "active": active, "restored": final}


if __name__ == "__main__":
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("This fixture may run only inside the disposable P0 guest")
    report = run()
    Path("/home/egress-test/p0-fixtures/reports/opt-verge-hot-cycle.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key not in ("active", "restored")}))
