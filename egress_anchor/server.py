"""Loopback HTTP delivery adapter for EgressAnchor."""

from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional
import urllib.parse

from .gateway import GatewayError, GatewayService


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATIC_DIR = ROOT / "static"
DEFAULT_DATA_DIR = ROOT / "dev-data"
MAX_JSON_BYTES = 1024 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _json_body(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _handler_class(service: GatewayService, static_dir: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "EgressAnchor/0.1"
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *_args):
            # Request lines may contain subscription tokens. Do not log them.
            return

        def _host_allowed(self) -> bool:
            host = self.headers.get("Host", "")
            try:
                parsed = urllib.parse.urlsplit(f"//{host}")
            except ValueError:
                return False
            if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                return False
            try:
                parsed.port
            except ValueError:
                return False
            return parsed.hostname in LOOPBACK_HOSTS

        def _security_headers(self) -> dict[str, str]:
            return {
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
                    "img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            }

        def _send(
            self,
            status: int,
            content_type: str,
            body: bytes,
            *,
            extra_headers: Optional[Mapping[str, str]] = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in self._security_headers().items():
                self.send_header(name, value)
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, value: Mapping[str, Any]) -> None:
            self._send(status, "application/json; charset=utf-8", _json_body(value))

        def _send_error(self, error: GatewayError) -> None:
            self._send_json(
                error.status,
                {
                    "ok": False,
                    "error": {"code": error.code, "message": error.safe_message},
                },
            )

        def _guard_host(self) -> bool:
            if self._host_allowed():
                return True
            self._send_error(
                GatewayError(
                    "HOST_NOT_ALLOWED",
                    "This local gateway only accepts loopback hosts.",
                    status=421,
                )
            )
            return False

        def _read_json(self) -> Mapping[str, Any]:
            content_type = self.headers.get("Content-Type", "")
            if not content_type.lower().startswith("application/json"):
                raise GatewayError("JSON_REQUIRED", "Send an application/json body.")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise GatewayError("JSON_INVALID", "The request body is invalid.") from None
            if length < 0 or length > MAX_JSON_BYTES:
                raise GatewayError("JSON_TOO_LARGE", "The request body is too large.", status=413)
            try:
                value = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise GatewayError("JSON_INVALID", "The request body is invalid.") from None
            if not isinstance(value, Mapping):
                raise GatewayError("JSON_INVALID", "The request body must be an object.")
            return value

        def _state_with_url(self) -> dict[str, Any]:
            state = dict(service.public_state())
            state["subscription"] = dict(state["subscription"])
            state["subscription"]["url"] = (
                f"http://{self.headers['Host']}{state['subscription']['path']}"
            )
            return state

        def _dispatch(self, action) -> None:
            if not self._guard_host():
                return
            try:
                action()
            except GatewayError as error:
                self._send_error(error)
            except Exception:
                self._send_error(
                    GatewayError(
                        "INTERNAL_ERROR",
                        "The local gateway could not complete the request.",
                        status=500,
                    )
                )

        def do_GET(self):
            def action():
                path = urllib.parse.urlsplit(self.path).path
                if path in {"/", "/index.html"}:
                    index_path = static_dir / "index.html"
                    if not index_path.exists():
                        raise GatewayError(
                            "UI_NOT_FOUND",
                            "The local UI is unavailable.",
                            status=500,
                        )
                    self._send(200, "text/html; charset=utf-8", index_path.read_bytes())
                    return
                if path == "/api/health":
                    self._send_json(200, {"ok": True, "service": "egress-anchor"})
                    return
                if path == "/api/state":
                    self._send_json(200, {"ok": True, "state": self._state_with_url()})
                    return
                if path.startswith("/sub/"):
                    token = path.removeprefix("/sub/")
                    compiled = service.read_subscription(token).encode("utf-8")
                    etag = f'"{hashlib.sha256(compiled).hexdigest()}"'
                    if self.headers.get("If-None-Match") == etag:
                        self._send(
                            304,
                            "application/yaml; charset=utf-8",
                            b"",
                            extra_headers={"ETag": etag},
                        )
                        return
                    self._send(
                        200,
                        "application/yaml; charset=utf-8",
                        compiled,
                        extra_headers={
                            "Content-Disposition": "inline; filename=egress-anchor.yaml",
                            "ETag": etag,
                        },
                    )
                    return
                raise GatewayError("NOT_FOUND", "The requested resource was not found.", status=404)

            self._dispatch(action)

        def do_POST(self):
            def action():
                path = urllib.parse.urlsplit(self.path).path
                payload = self._read_json()
                source_prefix = "/api/sources/"
                source_suffix = "/activate"
                if path.startswith(source_prefix) and path.endswith(source_suffix):
                    source_id = path[
                        len(source_prefix) : -len(source_suffix)
                    ]
                    if source_id and "/" not in source_id:
                        result = service.activate_source(
                            urllib.parse.unquote(source_id)
                        )
                        self._send_json(200, result)
                        return
                if path == "/api/sources":
                    source = service.import_source(
                        payload.get("name"), payload.get("url")
                    )
                    self._send_json(200, {"ok": True, "source": source})
                    return
                if path == "/api/landings":
                    landing = service.upsert_landing(payload)
                    self._send_json(200, {"ok": True, "landing": landing})
                    return
                raise GatewayError("NOT_FOUND", "The requested resource was not found.", status=404)

            self._dispatch(action)

        def do_PUT(self):
            def action():
                path = urllib.parse.urlsplit(self.path).path
                payload = self._read_json()
                source_prefix = "/api/sources/"
                source_suffix = "/select"
                if path.startswith(source_prefix) and path.endswith(source_suffix):
                    source_id = path[
                        len(source_prefix) : -len(source_suffix)
                    ]
                    if source_id and "/" not in source_id:
                        source = service.select_source(
                            urllib.parse.unquote(source_id)
                        )
                        self._send_json(200, {"ok": True, "source": source})
                        return
                if path == "/api/bindings":
                    result = service.update_bindings(payload.get("bindings"))
                    self._send_json(200, result)
                    return
                raise GatewayError("NOT_FOUND", "The requested resource was not found.", status=404)

            self._dispatch(action)

        def do_DELETE(self):
            def action():
                path = urllib.parse.urlsplit(self.path).path
                source_prefix = "/api/sources/"
                if path.startswith(source_prefix):
                    source_id = path.removeprefix(source_prefix)
                    if source_id and "/" not in source_id:
                        service.delete_source(urllib.parse.unquote(source_id))
                        self._send_json(200, {"ok": True})
                        return
                prefix = "/api/landings/"
                if path.startswith(prefix):
                    service.delete_landing(urllib.parse.unquote(path.removeprefix(prefix)))
                    self._send_json(200, {"ok": True})
                    return
                raise GatewayError("NOT_FOUND", "The requested resource was not found.", status=404)

            self._dispatch(action)

    return Handler


def create_server(
    service: GatewayService,
    *,
    host: str = "127.0.0.1",
    port: int = 8788,
    static_dir: Path = DEFAULT_STATIC_DIR,
) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("EgressAnchor V0.1 only binds to loopback hosts.")
    server = ThreadingHTTPServer((host, port), _handler_class(service, Path(static_dir)))
    server.daemon_threads = True
    return server


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="EgressAnchor local subscription gateway")
    parser.add_argument("--host", choices=("127.0.0.1",), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("EGRESS_ANCHOR_DATA_DIR", DEFAULT_DATA_DIR)),
    )
    args = parser.parse_args(argv)
    service = GatewayService(args.data_dir)
    server = create_server(service, host=args.host, port=args.port)
    print(f"EgressAnchor  http://{args.host}:{server.server_port}", flush=True)
    print(f"data          {args.data_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
