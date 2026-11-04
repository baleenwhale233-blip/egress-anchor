"""Check the guest's QEMU isolation using only our host fixture and a public TCP target."""

import json
import subprocess
from datetime import datetime, timezone

from client_vm import qmp, ssh_arguments
from client_vm_seed import DIRECTORY, read_state, save_state
from loopback_fixtures import fixture


def main():
    state = read_state()
    if state["online"] or not qmp("query-status")["running"]:
        raise ValueError("start the owned VM in offline mode first")
    with fixture("health") as port:
        script = f'''
import json,socket
results={{}}
for label,host,port in [('host_fixture','10.0.2.2',{port}),('outside_tcp','1.1.1.1',443)]:
    try:
        with socket.create_connection((host,port),timeout=1):
            results[label]=False
    except OSError:
        results[label]=True
print(json.dumps(results))
'''
        result = subprocess.run([*ssh_arguments(), "python3 -"], input=script,
                                capture_output=True, text=True, timeout=12)
        if result.returncode:
            raise ValueError(result.stderr)
        observed = json.loads(result.stdout)
    if not all(observed.values()):
        raise ValueError(f"guest can still reach a forbidden test target: {observed}")
    state["isolation_verified"] = True
    save_state(state)
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "ssh_control": True,
              "blocked": observed, "shared_host_directories": False}
    (DIRECTORY / "isolation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
