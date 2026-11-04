"""Disposable echo and forwarding proxies; every target stays on loopback."""

from contextlib import contextmanager
import select
import socket
import socketserver
import threading


def read_exact(connection, length):
    data = b""
    while len(data) < length:
        part = connection.recv(length - len(data))
        if not part:
            raise OSError("fixture peer closed")
        data += part
    return data


def read_headers(connection):
    data = b""
    while b"\r\n\r\n" not in data:
        data += read_exact(connection, 1)
        if len(data) > 8192:
            raise OSError("fixture headers too large")
    return data


def loopback_target(host, port):
    if host.endswith(".p0.invalid"):
        host = "127.0.0.1"
    if host != "127.0.0.1" or not 1 <= port <= 65535:
        raise OSError("fixture refused non-loopback target")
    return host, port


def relay(first, second):
    while True:
        readable, _, _ = select.select([first, second], [], [], 15)
        if not readable:
            return
        for source in readable:
            data = source.recv(65536)
            if not data:
                return
            (second if source is first else first).sendall(data)


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class UdpEcho(socketserver.BaseRequestHandler):
    def handle(self):
        data, connection = self.request
        connection.sendto(data, self.client_address)


class ForwardProxy(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(5)
            if self.server.kind == "http":
                command, authority, _ = read_headers(self.request).split(b"\r\n", 1)[0].split()
                if command != b"CONNECT":
                    return
                host, port = authority.decode().rsplit(":", 1)
                port = int(port)
            else:
                version, count = read_exact(self.request, 2)
                if version != 5 or 0 not in read_exact(self.request, count):
                    return
                self.request.sendall(b"\x05\x00")
                version, command, _, kind = read_exact(self.request, 4)
                if version != 5 or command != 1:
                    return
                if kind == 1:
                    host = socket.inet_ntoa(read_exact(self.request, 4))
                elif kind == 3:
                    host = read_exact(self.request, read_exact(self.request, 1)[0]).decode()
                else:
                    return
                port = int.from_bytes(read_exact(self.request, 2), "big")
            with socket.create_connection(loopback_target(host, port), timeout=5) as target:
                self.server.events.append({"proxy": self.server.label, "host": host, "port": port})
                if self.server.kind == "http":
                    self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                else:
                    self.request.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                relay(self.request, target)
        except (OSError, ValueError):
            return


class TcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, *_args):
        pass  # Disconnects are part of the cutover and failure scenarios.


@contextmanager
def fixture(kind, label="", events=None):
    handler = {"echo": Echo, "udp": UdpEcho}.get(kind, ForwardProxy)
    server_type = socketserver.ThreadingUDPServer if kind == "udp" else TcpServer
    with server_type(("127.0.0.1", 0), handler) as server:
        server.kind, server.label, server.events = kind, label, events
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join(timeout=2)


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def tunnel(mixed_port, host, port):
    connection = socket.create_connection(("127.0.0.1", mixed_port), timeout=4)
    connection.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    if not read_headers(connection).startswith(b"HTTP/1.1 200"):
        connection.close()
        raise OSError("core refused fixture tunnel")
    return connection


def ping(connection):
    connection.sendall(b"p0-ping")
    if read_exact(connection, 7) != b"p0-ping":
        raise AssertionError("fixture tunnel returned unexpected bytes")


def udp_probe(mixed_port, host, port):
    with socket.create_connection(("127.0.0.1", mixed_port), timeout=3) as control:
        control.sendall(b"\x05\x01\x00")
        if read_exact(control, 2) != b"\x05\x00":
            raise OSError("core refused SOCKS greeting")
        control.sendall(b"\x05\x03\x00\x01\x7f\x00\x00\x01\x00\x00")
        version, reply, _, kind = read_exact(control, 4)
        if (version, reply, kind) != (5, 0, 1):
            raise OSError("unexpected UDP association reply")
        address = socket.inet_ntoa(read_exact(control, 4))
        relay_port = int.from_bytes(read_exact(control, 2), "big")
        encoded = host.encode()
        packet = b"\x00\x00\x00\x03" + bytes([len(encoded)]) + encoded + port.to_bytes(2, "big") + b"p0-udp"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.settimeout(1)
            connection.sendto(packet, (address if address != "0.0.0.0" else "127.0.0.1", relay_port))
            try:
                return connection.recv(4096).endswith(b"p0-udp")
            except socket.timeout:
                return False
