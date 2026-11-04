"""Own one pinned, TUN-disabled Mihomo child and its temporary configuration."""

import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

import yaml

from egress_anchor import compile_subscription
from loopback_fixtures import free_port


class KernelSession:
    def __init__(self, binary):
        self.binary = Path(binary).resolve()
        self.lock = json.loads(Path(__file__).with_name("versions.json").read_text())
        actual = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        if actual != self.lock["mihomo"]["binary_sha256"]:
            raise ValueError("binary does not match the pinned darwin-arm64 checksum")
        self.temp = tempfile.TemporaryDirectory(prefix="egress-p0-")
        self.directory = Path(self.temp.name)
        self.mixed_port, self.controller_port = free_port(), free_port()
        self.secret = secrets.token_urlsafe(16)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.process = None
        self.log = None
        self.report = None

    def configuration(self, source, landing, *, udp_guards=(), front_hop=None, extra_managed=None):
        source = dict(source)
        source.update({
            "mixed-port": self.mixed_port, "bind-address": "127.0.0.1",
            "external-controller": f"127.0.0.1:{self.controller_port}",
            "secret": self.secret, "mode": "rule", "log-level": "warning",
            "allow-lan": False, "ipv6": False, "tun": {"enable": False},
            "dns": {"enable": False},
        })
        source["rules"] = [
            *[f"AND,((NETWORK,UDP),(DOMAIN,{host})),REJECT" for host in udp_guards],
            *source["rules"],
        ]
        managed = {"AI": {
            "landing": landing, "front_hop": front_hop or {"mode": "fixed", "proxy": "front"},
        }}
        managed.update(extra_managed or {})
        policy = {"version": 1, "managed_groups": managed}
        result = compile_subscription(yaml.safe_dump(source), policy)
        self.report = result.report
        path = self.directory / "fixture.yaml"
        path.write_text(result.compiled_yaml)
        checked = subprocess.run([str(self.binary), "-t", "-d", str(self.directory), "-f", str(path)],
                                 capture_output=True, text=True, timeout=15)
        if checked.returncode:
            raise AssertionError("Mihomo rejected generated fixture: " + checked.stdout[-1500:] + checked.stderr[-1500:])
        return path

    def start(self, source, landing):
        path = self.configuration(source, landing)
        self.log = (self.directory / "core.log").open("w")
        self.process = subprocess.Popen([str(self.binary), "-d", str(self.directory), "-f", str(path)],
                                        stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError("isolated core exited during startup")
            try:
                return self.api("GET", "/version")
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        raise TimeoutError("isolated controller did not become ready")

    def reload(self, source, landing, **options):
        path = self.configuration(source, landing, **options)
        self.api("PUT", "/configs?force=true", {"path": str(path)})

    def api(self, method, path, payload=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.controller_port}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": f"Bearer {self.secret}", "Content-Type": "application/json"},
            method=method,
        )
        with self.opener.open(request, timeout=4) as response:
            body = response.read()
            return json.loads(body) if body else None

    def close(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        if self.log is not None:
            self.log.close()
        self.temp.cleanup()
