"""On-demand diagnostics through a client's existing Mihomo controller."""

from concurrent.futures import ThreadPoolExecutor
import base64
from dataclasses import dataclass, field
import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from typing import Any, Callable, Mapping, Optional
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_CONTROLLER_URL = "http://127.0.0.1:9090"
DELAY_URL = "https://www.gstatic.com/generate_204"
DELAY_TIMEOUT_MS = 6000
MAX_RESPONSE_BYTES = 65536
IP_ENDPOINT = "https://api.ipify.org?format=json"


class DiagnosticsError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.status = status


@dataclass(frozen=True)
class BindingProbe:
    source_id: str
    generation: str
    group: str
    landing_name: str
    front_name: Optional[str]
    front_mode: str
    landing: Mapping[str, Any] = field(repr=False)


def validate_controller_url(value: Any) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = urllib.parse.urlsplit(value.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not parsed.port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    except ValueError:
        raise DiagnosticsError(
            "CONTROLLER_INVALID", "Use a loopback HTTP controller address with a port."
        ) from None


def validate_controller_secret(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 1024
        or any(ord(char) < 32 or ord(char) > 126 for char in value)
    ):
        raise DiagnosticsError("CONTROLLER_INVALID", "The controller key is invalid.")
    return value


def _failure(error: DiagnosticsError) -> dict[str, Any]:
    return {"ok": False, "code": error.code, "message": error.safe_message}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class MihomoController:
    """Only node/selection reads and individual delay requests are exposed."""

    def __init__(self, url: str, secret: str = ""):
        self.url = validate_controller_url(url)
        self._secret = validate_controller_secret(secret)
        self._deadline = time.monotonic() + 25
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    def proxy(self, name: str) -> Mapping[str, Any]:
        return self._get("/proxies/" + urllib.parse.quote(name, safe=""))

    def runtime_info(self) -> Mapping[str, Any]:
        """Read core settings, without issuing traffic or changing configuration."""
        value = self._get("/configs", timeout=0.8)
        mode = value.get("mode")
        tun = value.get("tun")
        if not isinstance(mode, str) or mode.lower() not in {"rule", "global", "direct"}:
            raise DiagnosticsError("CONTROLLER_RESPONSE_INVALID", "The client returned an invalid mode.")
        return {"state": "reachable", "mode": mode.lower(),
                "tun": tun.get("enable") if isinstance(tun, dict) and type(tun.get("enable")) is bool else None}

    def delay(self, name: str) -> int:
        query = urllib.parse.urlencode({
            "url": DELAY_URL, "timeout": DELAY_TIMEOUT_MS, "expected": "204",
        })
        result = self._get(
            "/proxies/" + urllib.parse.quote(name, safe="") + "/delay?" + query,
            timeout=DELAY_TIMEOUT_MS / 1000 + 1,
        )
        delay = result.get("delay")
        if isinstance(delay, bool) or not isinstance(delay, int) or not 0 <= delay <= 65535:
            raise DiagnosticsError("PROBE_FAILED", "The core did not return a valid delay.")
        return delay

    def _get(self, path: str, *, timeout: float = 3) -> Mapping[str, Any]:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise DiagnosticsError("PROBE_TIMEOUT", "The test timed out.")
        headers = {"User-Agent": "EgressAnchor/0.1"}
        if self._secret:
            headers["Authorization"] = "Bearer " + self._secret
        request = urllib.request.Request(self.url + path, headers=headers, method="GET")
        try:
            with self._opener.open(request, timeout=min(timeout, remaining)) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except urllib.error.HTTPError as error:
            error.close()
            if error.code in {401, 403}:
                raise DiagnosticsError("CONTROLLER_AUTH_FAILED", "The client rejected the access key.") from None
            if error.code == 404:
                raise DiagnosticsError("RUNTIME_PROXY_MISSING", "Update the local subscription in your client first.") from None
            if error.code in {408, 504}:
                raise DiagnosticsError("PROBE_TIMEOUT", "The test timed out.") from None
            raise DiagnosticsError("PROBE_FAILED", "The core could not complete the test.") from None
        except (TimeoutError, socket.timeout):
            raise DiagnosticsError("PROBE_TIMEOUT", "The test timed out.") from None
        except (urllib.error.URLError, OSError):
            raise DiagnosticsError("CONTROLLER_UNAVAILABLE", "Could not connect to the client's controller.") from None
        except (ValueError, UnicodeError):
            raise DiagnosticsError("CONTROLLER_RESPONSE_INVALID", "The client returned an invalid response.") from None

    def selected_front(self, name: str) -> tuple[str, bool]:
        visited = set()
        for _ in range(16):
            if name in visited:
                break
            visited.add(name)
            proxy = self.proxy(name)
            selected = proxy.get("now")
            if selected is None:
                if not isinstance(proxy.get("type"), str):
                    break
                return name, isinstance(proxy.get("all"), list)
            members = proxy.get("all")
            if not isinstance(selected, str) or not isinstance(members, list) or selected not in members:
                break
            name = selected
        raise DiagnosticsError("CONTROLLER_RESPONSE_INVALID", "The client's current selection is invalid.")


class LandingProbe:
    """Authenticate directly to a landing and fetch a small exit-IP response."""

    def __init__(self, *, endpoint: str = IP_ENDPOINT, timeout: float = 8):
        self._endpoint = urllib.parse.urlsplit(endpoint)
        self._timeout = timeout

    def __call__(self, landing: Mapping[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        connection = None
        try:
            target = self._endpoint
            port = target.port or (443 if target.scheme == "https" else 80)
            if landing.get("type") == "http":
                connection = http.client.HTTPConnection(
                    landing["server"], landing["port"], timeout=self._timeout
                )
                headers = {}
                if landing.get("username") or landing.get("password"):
                    credentials = f"{landing.get('username', '')}:{landing.get('password', '')}"
                    headers["Proxy-Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode()
                connection.set_tunnel(target.hostname, port, headers=headers)
                connection.connect()
            elif landing.get("type") == "socks5":
                connection = http.client.HTTPConnection(target.hostname, port, timeout=self._timeout)
                connection.sock = self._socks_tunnel(landing, target.hostname, port)
            else:
                raise DiagnosticsError("LANDING_CONNECT_FAILED", "This landing proxy could not be tested.")
            if target.scheme == "https":
                connection.sock = ssl.create_default_context().wrap_socket(
                    connection.sock, server_hostname=target.hostname
                )
            path = urllib.parse.urlunsplit(("", "", target.path or "/", target.query, ""))
            connection.request("GET", path, headers={
                "Host": target.netloc, "User-Agent": "EgressAnchor/0.1", "Connection": "close",
            })
            response = connection.getresponse()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if response.status != 200 or len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError
            body = json.loads(raw)
            address = str(ipaddress.ip_address(body["ip"]))
            return {"ok": True, "ip": address, "via": "direct",
                    "authentication": "accepted" if landing.get("username") or landing.get("password") else "not_required",
                    "elapsed_ms": int((time.monotonic() - started) * 1000)}
        except DiagnosticsError as error:
            return _failure(error)
        except (TimeoutError, socket.timeout):
            return _failure(DiagnosticsError("LANDING_TIMEOUT", "The landing verification timed out."))
        except OSError as error:
            if str(error).startswith("Tunnel connection failed: 407"):
                return _failure(DiagnosticsError("LANDING_AUTH_FAILED", "The landing rejected the username or password."))
            return _failure(DiagnosticsError("LANDING_CONNECT_FAILED", "Could not establish a connection through the landing."))
        except (ValueError, KeyError, TypeError, http.client.HTTPException):
            return _failure(DiagnosticsError("IP_LOOKUP_FAILED", "The exit-IP lookup did not return a valid address."))
        finally:
            if connection is not None:
                connection.close()

    def _socks_tunnel(self, landing: Mapping[str, Any], host: str, port: int):
        username = landing.get("username", "").encode("utf-8")
        password = landing.get("password", "").encode("utf-8")
        if len(username) > 255 or len(password) > 255:
            raise DiagnosticsError("LANDING_AUTH_FAILED", "SOCKS5 credentials exceed the supported length.")
        sock = socket.create_connection((landing["server"], landing["port"]), timeout=self._timeout)
        try:
            method = 2 if username or password else 0
            sock.sendall(bytes([5, 1, method]))
            if _recv_exact(sock, 2) != bytes([5, method]):
                raise DiagnosticsError("LANDING_AUTH_FAILED", "The landing rejected the authentication method.")
            if method == 2:
                sock.sendall(bytes([1, len(username)]) + username + bytes([len(password)]) + password)
                if _recv_exact(sock, 2) != b"\x01\x00":
                    raise DiagnosticsError("LANDING_AUTH_FAILED", "The landing rejected the username or password.")
            address = host.encode("idna")
            sock.sendall(b"\x05\x01\x00\x03" + bytes([len(address)]) + address + port.to_bytes(2, "big"))
            version, reply, reserved, kind = _recv_exact(sock, 4)
            if version != 5 or reply != 0 or reserved != 0:
                raise DiagnosticsError("LANDING_CONNECT_FAILED", "The landing could not connect to the exit-IP service.")
            if kind == 1:
                _recv_exact(sock, 4)
            elif kind == 4:
                _recv_exact(sock, 16)
            elif kind == 3:
                _recv_exact(sock, _recv_exact(sock, 1)[0])
            else:
                raise OSError
            _recv_exact(sock, 2)
            return sock
        except Exception:
            sock.close()
            raise


def _recv_exact(sock, length: int) -> bytes:
    result = b""
    while len(result) < length:
        chunk = sock.recv(length - len(result))
        if not chunk:
            raise OSError
        result += chunk
    return result


class BindingDiagnostics:
    def __init__(self, *, landing_probe: Optional[Callable[[Mapping[str, Any]], Mapping[str, Any]]] = None):
        self._landing_probe = landing_probe if landing_probe is not None else LandingProbe()
        self._slots = threading.BoundedSemaphore(2)

    def run(self, plan: BindingProbe, *, controller_url: str, controller_secret: str = "") -> dict[str, Any]:
        if not self._slots.acquire(blocking=False):
            raise DiagnosticsError("PROBE_BUSY", "Two tests are already running. Try again shortly.", status=429)
        try:
            return self._run(plan, controller_url, controller_secret)
        finally:
            self._slots.release()

    def _run(self, plan: BindingProbe, url: str, secret: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source_id": plan.source_id, "generation": plan.generation,
            "group": plan.group, "tested_at": int(time.time()), "scope": "client_runtime",
        }
        with ThreadPoolExecutor(max_workers=1) as executor:
            landing = executor.submit(self._landing_probe, plan.landing)
            try:
                controller = MihomoController(url, secret)
                group = controller.proxy(plan.group)
                result["selection"] = {
                    "ok": group.get("now") == plan.landing_name and group.get("all") == [plan.landing_name],
                    "name": group.get("now") if isinstance(group.get("now"), str) else None,
                }
                if not result["selection"]["ok"]:
                    error = DiagnosticsError("RUNTIME_MISMATCH", "The client has not loaded this fail-closed binding. Update its subscription first.")
                    result["selection"].update(_failure(error))
                    raise error
                controller.proxy(plan.landing_name)
                front = controller.selected_front(plan.front_name) if plan.front_name else ("DIRECT", False)
                result["chain"] = self._measure(controller, plan.landing_name)
                if front[0] in {"DIRECT", "COMPATIBLE"}:
                    result["front"] = {"ok": True, "name": "DIRECT", "skipped": True}
                else:
                    result["front"] = {**self._measure(controller, front[0]), "is_group": front[1]}
                    result["front"]["selection_changed"] = controller.selected_front(plan.front_name)[0] != front[0]
            except DiagnosticsError as error:
                result.setdefault("selection", _failure(error))
                result.setdefault("chain", _failure(error))
                result.setdefault("front", _failure(error))
            try:
                result["landing"] = dict(landing.result())
            except Exception:
                result["landing"] = _failure(DiagnosticsError("LANDING_PROBE_FAILED", "Could not verify the landing proxy."))
        return result

    @staticmethod
    def _measure(controller: MihomoController, name: str) -> dict[str, Any]:
        try:
            return {"ok": True, "name": name, "ms": controller.delay(name)}
        except DiagnosticsError as error:
            return {**_failure(error), "name": name}
