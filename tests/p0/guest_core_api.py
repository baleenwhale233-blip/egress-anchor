"""Small Mihomo transport restricted to the two test controllers inside our guest."""

import http.client
import json
import platform
import socket


def request(runtime, client, method, path, body=None):
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise ValueError("use only the disposable P0 guest controllers")

    class Connection(http.client.HTTPConnection):
        def connect(self):
            if client == "flclash" and runtime["external-controller"] == "127.0.0.1:9090":
                self.sock = socket.create_connection(("127.0.0.1", 9090), timeout=4)
                return
            expected = "/home/egress-test/.local/share/io.github.clash-verge-rev.clash-verge-rev/verge-mihomo.sock"
            if client != "verge" or runtime.get("external-controller-unix") != expected:
                raise ValueError("unexpected native test controller")
            self.sock = socket.socket(socket.AF_UNIX)
            self.sock.settimeout(4)
            self.sock.connect(expected)

    connection = Connection("localhost", timeout=4)
    try:
        headers = {"Authorization": "Bearer " + runtime.get("secret", "")}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        raw = response.read(16 * 1024 * 1024 + 1)
        if response.status not in (200, 204) or len(raw) > 16 * 1024 * 1024:
            raise ValueError(f"fixture controller returned {response.status}")
        return json.loads(raw) if raw else None
    finally:
        connection.close()
