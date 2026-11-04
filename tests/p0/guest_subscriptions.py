"""Local versioned subscriptions and proxies inside the offline test guest."""

from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import platform
import socket

import yaml

from loopback_fixtures import fixture


DIRECTORY = Path("/home/egress-test/p0-fixtures")


def main():
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("This fixture may run only inside the disposable P0 guest")
    DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    state_path = DIRECTORY / "subscriptions.json"
    if not state_path.exists():
        state_path.write_text(json.dumps({"a": 1, "b": 1}))
    events = []
    ports_path = DIRECTORY / "ports.json"
    previous = json.loads(ports_path.read_text()) if ports_path.exists() else {}
    with ExitStack() as stack:
        ports = {name: stack.enter_context(fixture(kind, name, events, port=previous.get(name, 0))) for name, kind in [
            ("front", "http"), ("ordinary", "http"), ("landing", "http"), ("origin", "echo"),
        ]}
        ports_path.write_text(json.dumps(ports))

        def subscription(name):
            version = json.loads(state_path.read_text())[name]
            front, other = f"airport-{name}-v{version}", f"ordinary-{name}-v{version}"
            return {
                "mode": "rule", "ipv6": False, "tun": {"enable": False},
                "hosts": {f"{label}.p0.invalid": "127.0.0.1" for label in ("managed", "unmanaged", "user-added")},
                "proxies": [
                    {"name": front, "type": "http", "server": "127.0.0.1", "port": ports["front"]},
                    {"name": other, "type": "http", "server": "127.0.0.1", "port": ports["ordinary"]},
                ],
                "proxy-groups": [
                    {"name": "OriginalAI", "type": "select", "proxies": [front]},
                    {"name": "OriginalDefault", "type": "select", "proxies": [other]},
                ],
                "rules": ["DOMAIN,managed.p0.invalid,OriginalAI",
                          "DOMAIN,unmanaged.p0.invalid,OriginalDefault", "MATCH,DIRECT"],
            }

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                name = self.path.removesuffix(".yaml").removeprefix("/")
                if name in ("a", "b"):
                    with (DIRECTORY / "fetches.jsonl").open("a") as stream:
                        stream.write(json.dumps({"source": name, "version": json.loads(state_path.read_text())[name]}) + "\n")
                    body = yaml.safe_dump(subscription(name)).encode()
                    content_type = "application/yaml"
                elif self.path == "/events":
                    body = json.dumps(events).encode()
                    content_type = "application/json"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                name = self.path.removeprefix("/advance/")
                if name not in ("a", "b"):
                    self.send_error(404)
                    return
                state = json.loads(state_path.read_text())
                state[name] += 1
                state_path.write_text(json.dumps(state))
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 18880), Handler)
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
