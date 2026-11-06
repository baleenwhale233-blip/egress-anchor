"""State, upstream delivery, and last-known-good storage for EgressAnchor."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading
import time
from typing import Any, Callable, Mapping, Optional
import urllib.error
import urllib.parse
import urllib.request

from .compiler import (
    CompileError,
    CompileResult,
    MAX_SOURCE_BYTES,
    compile_subscription,
    inspect_subscription,
)
from .diagnostics import (
    BindingDiagnostics,
    BindingProbe,
    DEFAULT_CONTROLLER_URL,
    DiagnosticsError,
    validate_controller_secret,
    validate_controller_url,
)
from .service_catalog import change_service, default_services, service_view
from .service_compiler import airport_choices, compile_service_subscription, validate_service_references
from .service_policy import ServicePolicyError
from .client_discovery import ClientDiscovery


STATE_VERSION = 2
MAX_SOURCES = 20
MAX_REDIRECTS = 3
FETCH_TIMEOUT_SECONDS = 15
DEFAULT_USER_AGENT = "EgressAnchor/0.1"
MIHOMO_USER_AGENT = "clash.meta"
URI_SCHEMES = (
    "ss://",
    "ssr://",
    "vmess://",
    "vless://",
    "trojan://",
    "hysteria://",
    "hysteria2://",
    "hy2://",
    "tuic://",
    "socks://",
    "socks5://",
)


@dataclass(frozen=True)
class FetchResult:
    body: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    not_modified: bool = False
    content_type: Optional[str] = None


class GatewayError(RuntimeError):
    """A caller-safe gateway failure."""

    def __init__(self, code: str, safe_message: str, *, status: int = 400,
                 field=None, service_ids=()) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.status = status
        self.field = field
        self.service_ids = service_ids


class _LimitedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        self.redirects = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirects += 1
        if self.redirects > MAX_REDIRECTS:
            raise GatewayError(
                "UPSTREAM_REDIRECT_LIMIT",
                "The upstream subscription redirected too many times.",
                status=502,
            )
        _validated_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validated_http_url(raw_url: Any) -> str:
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise GatewayError("UPSTREAM_URL_INVALID", "Enter an HTTP or HTTPS subscription URL.")
    url = raw_url.strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise GatewayError("UPSTREAM_URL_INVALID", "Enter an HTTP or HTTPS subscription URL.")
    return url


def _display_url(url: Any) -> str:
    """Retain only the origin; any URL component may contain credentials."""
    try:
        parsed = urllib.parse.urlsplit(_validated_http_url(url))
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
    except (GatewayError, ValueError):
        return ""
    return urllib.parse.urlunsplit((parsed.scheme, host, "", "", ""))


def _validated_source_name(raw_name: Any) -> str:
    if (
        not isinstance(raw_name, str)
        or not raw_name.strip()
        or len(raw_name.strip()) > 80
    ):
        raise GatewayError(
            "SOURCE_NAME_INVALID",
            "Enter a source name between 1 and 80 characters.",
        )
    return raw_name.strip()


def _landing_fields_from_input(
    raw_value: Any,
    *,
    default_type: Any = None,
) -> dict[str, Any]:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise GatewayError(
            "LANDING_URL_INVALID",
            "Enter a valid SOCKS5 or HTTP proxy URL.",
        )
    value = raw_value.strip()
    kinds = {"socks5": "socks5", "http": "http"}
    if "://" not in value:
        if default_type not in kinds:
            raise GatewayError(
                "LANDING_URL_INVALID",
                "Choose HTTP or SOCKS5 for a proxy string without a scheme.",
            )
        if "@" in value:
            parsed = None
            try:
                parsed = urllib.parse.urlsplit(f"//{value}")
                hostname = parsed.hostname
                port = parsed.port
            except ValueError:
                hostname = None
                port = None
            if (
                not hostname
                or port is None
                or parsed is None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise GatewayError(
                    "LANDING_URL_INVALID",
                    "Enter a supported proxy string or URL.",
                )
            return {
                "type": kinds[default_type],
                "server": hostname,
                "port": port,
                "username": urllib.parse.unquote(parsed.username or ""),
                "password": urllib.parse.unquote(parsed.password or ""),
            }
        parts = value.split(":")
        if len(parts) == 2:
            hostname, raw_port = parts
            username = ""
            password = ""
        elif len(parts) == 4:
            second_is_port = parts[1].isdigit() and 1 <= int(parts[1]) <= 65535
            fourth_is_port = parts[3].isdigit() and 1 <= int(parts[3]) <= 65535
            if second_is_port and not fourth_is_port:
                hostname, raw_port, username, password = parts
            elif fourth_is_port and not second_is_port:
                username, password, hostname, raw_port = parts
            else:
                raise GatewayError(
                    "LANDING_URL_INVALID",
                    "The four-part proxy string is ambiguous.",
                )
        else:
            raise GatewayError(
                "LANDING_URL_INVALID",
                "Enter a supported proxy string or URL.",
            )
        try:
            port = int(raw_port)
        except ValueError:
            port = 0
        if (
            not hostname
            or any(character.isspace() for character in hostname)
            or not 1 <= port <= 65535
        ):
            raise GatewayError(
                "LANDING_URL_INVALID",
                "Enter a supported proxy string or URL.",
            )
        return {
            "type": kinds[default_type],
            "server": hostname,
            "port": port,
            "username": username,
            "password": password,
        }
    try:
        parsed = urllib.parse.urlsplit(value)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise GatewayError(
            "LANDING_URL_INVALID",
            "Enter a valid SOCKS5 or HTTP proxy URL.",
        ) from None
    if (
        scheme not in kinds
        or not hostname
        or port is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise GatewayError(
            "LANDING_URL_INVALID",
            "Enter a valid SOCKS5 or HTTP proxy URL.",
        )
    return {
        "type": kinds[scheme],
        "server": hostname,
        "port": port,
        "username": urllib.parse.unquote(parsed.username or ""),
        "password": urllib.parse.unquote(parsed.password or ""),
    }


def detect_subscription_format(
    body: str,
    *,
    content_type: Optional[str] = None,
) -> str:
    """Classify common non-Mihomo subscription response shapes."""
    text = body.strip()
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    lowered = text[:256].lower()
    if lowered.startswith(("<!doctype html", "<html", "<body")):
        return "html"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and all(line.lower().startswith(URI_SCHEMES) for line in lines):
        return "uri-list"
    compact = "".join(text.split())
    if len(compact) >= 16 and re.fullmatch(r"[A-Za-z0-9+/_=-]+", compact):
        padded = compact + ("=" * (-len(compact) % 4))
        try:
            decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            decoded = ""
        decoded_lines = [line.strip() for line in decoded.splitlines() if line.strip()]
        if decoded_lines and all(
            line.lower().startswith(URI_SCHEMES) for line in decoded_lines
        ):
            return "base64"
    if media_type in {"text/html", "application/xhtml+xml"}:
        return "html"
    return "unknown"


def _fetch_subscription_once(
    url: str,
    *,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    user_agent: str,
) -> FetchResult:
    url = _validated_http_url(url)
    headers = {
        "Accept": "application/yaml, text/yaml, text/plain, */*",
        "User-Agent": user_agent,
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_LimitedRedirectHandler())
    try:
        with opener.open(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_SOURCE_BYTES + 1)
            if len(body) > MAX_SOURCE_BYTES:
                raise GatewayError(
                    "UPSTREAM_TOO_LARGE",
                    "The upstream subscription exceeds the size limit.",
                    status=502,
                )
            try:
                text = body.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise GatewayError(
                    "UPSTREAM_ENCODING_INVALID",
                    "The upstream subscription is not valid UTF-8 text.",
                    status=502,
                ) from None
            return FetchResult(
                text,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                content_type=response.headers.get("Content-Type"),
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return FetchResult("", etag=etag, last_modified=last_modified, not_modified=True)
        raise GatewayError(
            "UPSTREAM_HTTP_ERROR",
            "The upstream subscription returned an HTTP error.",
            status=502,
        ) from None
    except GatewayError:
        raise
    except (OSError, urllib.error.URLError, TimeoutError):
        raise GatewayError(
            "UPSTREAM_FETCH_FAILED",
            "The upstream subscription could not be fetched.",
            status=502,
        ) from None


def fetch_subscription(
    url: str,
    *,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> FetchResult:
    """Fetch a subscription and retry generic node formats as a Mihomo client."""
    primary = _fetch_subscription_once(
        url,
        etag=etag,
        last_modified=last_modified,
        user_agent=DEFAULT_USER_AGENT,
    )
    if primary.not_modified:
        return primary
    detected = detect_subscription_format(
        primary.body,
        content_type=primary.content_type,
    )
    if detected not in {"base64", "uri-list"}:
        return primary
    return _fetch_subscription_once(
        url,
        etag=etag,
        last_modified=last_modified,
        user_agent=MIHOMO_USER_AGENT,
    )


def _format_error(
    source_yaml: str,
    *,
    content_type: Optional[str],
    compiler_code: str,
) -> GatewayError:
    detected = detect_subscription_format(source_yaml, content_type=content_type)
    errors = {
        "base64": (
            "UPSTREAM_FORMAT_BASE64",
            "The upstream returned a Base64 subscription instead of Mihomo YAML.",
        ),
        "uri-list": (
            "UPSTREAM_FORMAT_URI_LIST",
            "The upstream returned a URI node list instead of Mihomo YAML.",
        ),
        "html": (
            "UPSTREAM_FORMAT_HTML",
            "The upstream returned an HTML page instead of a subscription.",
        ),
    }
    if detected in errors:
        code, message = errors[detected]
        return GatewayError(code, message, status=422)
    if compiler_code == "FULL_CONFIG_REQUIRED":
        return GatewayError(
            "UPSTREAM_FORMAT_NODE_ONLY",
            "The upstream returned nodes without a complete Mihomo configuration.",
            status=422,
        )
    return GatewayError(
        "UPSTREAM_FORMAT_UNSUPPORTED",
        "The upstream response is not a supported complete Mihomo configuration.",
        status=422,
    )


def _default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "token": secrets.token_urlsafe(32),
        "sources": [],
        "selected_source_id": None,
        "active_source_id": None,
        "landings": [],
        "bindings": [],
        "catalog": {"groups": [], "proxies": [], "providers": []},
        "active_generation": None,
        "active_report": None,
        "last_success_at": None,
        "last_import_at": None,
        "last_error": None,
        "diagnostics_controller": {"url": DEFAULT_CONTROLLER_URL, "secret": ""},
    }


def _state_with_display_origins(state: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = deepcopy(dict(state))
    sources = sanitized.get("sources", [])
    if isinstance(sources, list):
        for source in sources:
            if isinstance(source, dict):
                source["display_url"] = _display_url(source.get("display_url"))
    return sanitized


def _atomic_write(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


class GatewayService:
    """Own gateway state and compiled generations behind a small interface."""

    def __init__(
        self,
        data_dir: Path,
        *,
        fetcher: Callable[..., FetchResult] = fetch_subscription,
        now: Callable[[], float] = time.time,
        diagnostics: Optional[BindingDiagnostics] = None,
        client_discovery=None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)
        self.state_path = self.data_dir / "state.json"
        self.source_path = self.data_dir / "source.yaml"
        self.sources_dir = self.data_dir / "sources"
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.sources_dir, 0o700)
        self.generations_dir = self.data_dir / "generations"
        self.generations_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.generations_dir, 0o700)
        self.fetcher = fetcher
        self.now = now
        self.diagnostics = diagnostics if diagnostics is not None else BindingDiagnostics()
        self.client_discovery = client_discovery if client_discovery is not None else ClientDiscovery()
        self._lock = threading.RLock()
        self._state = self._load_or_create_state()

    def _load_or_create_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            state = _default_state()
            self._save_state_value(state)
            return state
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise GatewayError(
                "STATE_INVALID",
                "The local gateway state could not be read.",
                status=500,
            ) from None
        if not isinstance(state, dict):
            raise GatewayError(
                "STATE_INVALID",
                "The local gateway state is unsupported.",
                status=500,
            )
        if state.get("version") == 1:
            return self._migrate_v1_state(state)
        if state.get("version") != STATE_VERSION:
            raise GatewayError(
                "STATE_INVALID",
                "The local gateway state is unsupported.",
                status=500,
            )
        sanitized = _state_with_display_origins(state)
        if sanitized != state:
            self._save_state_value(sanitized)
            state = sanitized
        os.chmod(self.state_path, 0o600)
        return state

    def _migrate_v1_state(self, legacy: Mapping[str, Any]) -> dict[str, Any]:
        state = _default_state()
        token = legacy.get("token")
        if isinstance(token, str) and token:
            state["token"] = token
        for field in (
            "landings",
            "active_generation",
            "active_report",
            "last_success_at",
            "last_error",
        ):
            if field in legacy:
                state[field] = deepcopy(legacy[field])
        state["last_import_at"] = legacy.get("last_refresh_at")
        if self.source_path.exists():
            try:
                source_yaml = self.source_path.read_text(encoding="utf-8")
                catalog = inspect_subscription(source_yaml)
            except (OSError, CompileError):
                source_yaml = ""
                catalog = None
            if catalog is not None:
                source_id = secrets.token_hex(6)
                imported_at = legacy.get("source_updated_at")
                if not isinstance(imported_at, int):
                    imported_at = self._timestamp()
                display_url = ""
                upstream = legacy.get("upstream")
                if isinstance(upstream, Mapping):
                    legacy_url = upstream.get("url")
                    try:
                        display_url = _display_url(_validated_http_url(legacy_url))
                    except GatewayError:
                        display_url = ""
                bindings = legacy.get("bindings")
                if not isinstance(bindings, list):
                    bindings = []
                state["sources"] = [
                    {
                        "id": source_id,
                        "name": "已迁移订阅",
                        "display_url": display_url,
                        "imported_at": imported_at,
                        "catalog": deepcopy(catalog),
                        "bindings": deepcopy(bindings),
                    }
                ]
                state["selected_source_id"] = source_id
                state["active_source_id"] = (
                    source_id if state.get("active_generation") else None
                )
                state["catalog"] = deepcopy(catalog)
                state["bindings"] = deepcopy(bindings)
                snapshot_path = self._source_snapshot_path(source_id)
                if snapshot_path is None:
                    raise GatewayError(
                        "STATE_INVALID",
                        "The local gateway state could not be migrated.",
                        status=500,
                    )
                snapshot_path.parent.mkdir(parents=True, exist_ok=False)
                os.chmod(snapshot_path.parent, 0o700)
                _atomic_write(snapshot_path, source_yaml)
        self._save_state_value(state)
        try:
            self.source_path.unlink()
        except FileNotFoundError:
            pass
        os.chmod(self.state_path, 0o600)
        return state

    def _save_state_value(self, state: Mapping[str, Any]) -> None:
        _atomic_write(
            self.state_path,
            json.dumps(
                _state_with_display_origins(state),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
        )

    def _timestamp(self) -> int:
        return int(self.now())

    def _compiled_path(self, generation: Any) -> Optional[Path]:
        if not isinstance(generation, str) or not re.fullmatch(
            r"[0-9]+-[0-9a-f]{8}", generation
        ):
            return None
        return self.generations_dir / generation / "compiled.yaml"

    def _source_snapshot_path(self, source_id: Any) -> Optional[Path]:
        if not isinstance(source_id, str) or not re.fullmatch(r"[0-9a-f]{12}", source_id):
            return None
        return self.sources_dir / source_id / "source.yaml"

    def _record_error(self, error: GatewayError) -> None:
        candidate = deepcopy(self._state)
        candidate["last_error"] = {
            "code": error.code,
            "message": error.safe_message,
            "at": self._timestamp(),
        }
        self._save_state_value(candidate)
        self._state = candidate

    def public_state(self) -> Mapping[str, Any]:
        client = self.client_discovery.snapshot()
        with self._lock:
            selected_source_id = self._state.get("selected_source_id")
            active_source_id = self._state.get("active_source_id")
            sources = []
            for source in self._state.get("sources", []):
                catalog = source.get("catalog") or {}
                sources.append(
                    {
                        "id": source["id"],
                        "name": source["name"],
                        "display_url": source.get("display_url", ""),
                        "imported_at": source.get("imported_at"),
                        "group_count": len(catalog.get("groups") or []),
                        "proxy_count": len(catalog.get("proxies") or []),
                        "provider_count": len(catalog.get("providers") or []),
                        "managed_group_count": sum(
                            1
                            for binding in source.get("bindings", [])
                            if binding.get("enabled")
                        ),
                        "selected": source["id"] == selected_source_id,
                        "active": source["id"] == active_source_id,
                    }
                )
            landings = []
            for landing in self._state["landings"]:
                landings.append(
                    {
                        "id": landing["id"],
                        "name": landing["name"],
                        "type": landing["type"],
                        "server": landing["server"],
                        "port": landing["port"],
                        "username": landing.get("username", ""),
                        "udp": bool(landing.get("udp", True)),
                        "has_password": bool(landing.get("password")),
                    }
                )
            active = self._state.get("active_generation")
            compiled_path = self._compiled_path(active)
            available = bool(compiled_path and compiled_path.exists())
            return {
                **service_view(self._state),
                "client": client,
                "airport_choices": airport_choices(self._state["catalog"], self._state["bindings"]),
                "sources": sources,
                "selected_source_id": selected_source_id,
                "active_source_id": active_source_id,
                "landings": landings,
                "bindings": deepcopy(self._state["bindings"]),
                "catalog": deepcopy(self._state["catalog"]),
                "subscription": {
                    "token": self._state["token"],
                    "path": f"/sub/{self._state['token']}",
                    "available": available,
                    "generation": active,
                    "report": deepcopy(self._state.get("active_report")),
                },
                "last_success_at": self._state.get("last_success_at"),
                "last_import_at": self._state.get("last_import_at"),
                "last_error": deepcopy(self._state.get("last_error")),
                "diagnostics_controller": {
                    "url": self._controller_settings()["url"],
                    "has_secret": bool(self._controller_settings()["secret"]),
                },
            }

    def discover_client(self):
        return self.client_discovery.discover()

    def update_service(self, payload: Mapping[str, Any], *, delete=False) -> Mapping[str, Any]:
        """Atomically save one service and its local generation, never a client file."""
        with self._lock:
            if "source_id" not in payload or payload["source_id"] != self._state.get("selected_source_id"):
                raise GatewayError("SOURCE_CHANGED", "当前订阅已变化，请刷新后重试。", status=409)
            candidate = deepcopy(self._state)
            try:
                records = change_service(candidate.get("services", default_services()), payload,
                                         now=self._timestamp(), delete=delete)
                edited_id = payload["service"].get("id") or records[-1]["id"]
                blocked = []
                for record in records:
                    try:
                        validate_service_references([record], candidate["landings"], candidate["catalog"], candidate["bindings"])
                    except ServicePolicyError:
                        if record["id"] == edited_id:
                            raise
                        blocked.append(record["id"])
                candidate["services"] = records
                candidate["services_revision"] = candidate.get("services_revision", 0) + 1
                source_id = candidate.get("selected_source_id")
                source = next((s for s in candidate.get("sources", []) if s["id"] == source_id), None)
                compiled = False
                # A source switch can invalidate several references at once.
                # Allow repairing them one at a time, keeping the prior delivery
                # until every reference is valid; reject a newly invalid edit.
                if source is not None and not blocked:
                    snapshot = self._source_snapshot_path(source_id)
                    if snapshot is None or not snapshot.exists():
                        raise GatewayError("SOURCE_CACHE_MISSING", "订阅快照不可用，请重新导入。", status=503)
                    self._compiled_state(candidate, snapshot.read_text(encoding="utf-8"), source=source)
                    candidate["active_source_id"] = source_id
                    compiled = True
            except ServicePolicyError as error:
                raise GatewayError(error.code, error.safe_message,
                    status=409 if error.code == "SERVICE_CHANGED" else 422,
                    field=error.field, service_ids=error.service_ids) from None
            candidate["last_error"] = None
            self._save_state_value(candidate)
            self._state = candidate
            return {"ok": True, "compiled": compiled, "blocked_service_ids": blocked}

    def _controller_settings(self) -> dict[str, Any]:
        settings = self._state.get("diagnostics_controller") or {}
        return {
            "url": settings.get("url", DEFAULT_CONTROLLER_URL),
            "secret": settings.get("secret", ""),
        }

    def set_diagnostics_controller(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        with self._lock:
            current = self._controller_settings()
            try:
                url = validate_controller_url(payload.get("url"))
                clear = payload.get("clear_secret", False)
                if not isinstance(clear, bool):
                    raise DiagnosticsError("CONTROLLER_INVALID", "The controller key option is invalid.")
                raw_secret = payload.get("secret", "")
                validate_controller_secret(raw_secret)
                secret = "" if clear else (raw_secret or current["secret"])
            except DiagnosticsError as error:
                raise GatewayError(error.code, error.safe_message, status=error.status) from None
            candidate = deepcopy(self._state)
            candidate["diagnostics_controller"] = {"url": url, "secret": secret}
            self._save_state_value(candidate)
            self._state = candidate
            return self.public_state()["diagnostics_controller"]

    def probe_binding(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        with self._lock:
            source_id = payload.get("source_id")
            if not isinstance(source_id, str) or source_id != self._state.get("selected_source_id"):
                raise GatewayError("SOURCE_CHANGED", "Reload the selected source before testing.", status=409)
            generation = self._state.get("active_generation")
            if source_id != self._state.get("active_source_id") or not generation:
                raise GatewayError("PROBE_NOT_READY", "Save and activate this source before testing.", status=422)
            if payload.get("generation") != generation:
                raise GatewayError("PROBE_STALE", "The active configuration changed. Reload before testing.", status=409)
            group = payload.get("group")
            saved = next((item for item in self._state["bindings"] if item["group"] == group), None)
            if saved is None or not saved.get("enabled"):
                raise GatewayError("PROBE_NOT_READY", "Enable and save this binding before testing.", status=422)
            requested = self._normalized_bindings([payload.get("binding")])[0]
            if requested != saved:
                raise GatewayError("PROBE_UNSAVED", "Save your changes and update the client subscription before testing.", status=409)
            report = self._state.get("active_report") or {}
            compiled = next((item for item in report.get("managed_groups", []) if item["group"] == group), None)
            landing = next((item for item in self._state["landings"] if item["id"] == saved["landing_id"]), None)
            if compiled is None or landing is None:
                raise GatewayError("PROBE_NOT_READY", "No compiled binding is available to test.", status=422)
            plan = BindingProbe(
                source_id=source_id, generation=generation, group=group,
                landing_name=compiled["landing"], front_name=compiled["front"],
                front_mode=compiled["front_mode"], landing=deepcopy(landing),
            )
            settings = deepcopy(self._controller_settings())
        try:
            result = self.diagnostics.run(plan, controller_url=settings["url"], controller_secret=settings["secret"])
        except DiagnosticsError as error:
            raise GatewayError(error.code, error.safe_message, status=error.status) from None
        with self._lock:
            if (
                self._state.get("selected_source_id") != source_id
                or self._state.get("active_generation") != generation
                or self._controller_settings() != settings
            ):
                raise GatewayError("PROBE_STALE", "The configuration changed during the test. Run it again.", status=409)
        return result

    def import_source(self, name: Any, url: Any) -> Mapping[str, Any]:
        with self._lock:
            name = _validated_source_name(name)
            url = _validated_http_url(url)
            sources = self._state.get("sources", [])
            if len(sources) >= MAX_SOURCES:
                raise GatewayError(
                    "SOURCE_LIMIT_REACHED",
                    "Remove a saved source before importing another one.",
                )
            if any(source.get("name") == name for source in sources):
                raise GatewayError(
                    "SOURCE_NAME_CONFLICT",
                    "Choose a unique source name.",
                )
            try:
                fetched = self.fetcher(url, etag=None, last_modified=None)
                if fetched.not_modified:
                    raise GatewayError(
                        "UPSTREAM_NOT_MODIFIED_INVALID",
                        "The one-time import did not return a subscription body.",
                        status=502,
                    )
                source_yaml = fetched.body
                try:
                    catalog = inspect_subscription(source_yaml)
                except CompileError as exc:
                    if exc.code in {"SOURCE_NOT_MAPPING", "FULL_CONFIG_REQUIRED"}:
                        raise _format_error(
                            source_yaml,
                            content_type=fetched.content_type,
                            compiler_code=exc.code,
                        ) from None
                    raise
                source_id = secrets.token_hex(6)
                while any(source.get("id") == source_id for source in sources):
                    source_id = secrets.token_hex(6)
                imported_at = self._timestamp()
                candidate = deepcopy(self._state)
                candidate.setdefault("sources", []).append(
                    {
                        "id": source_id,
                        "name": name,
                        "display_url": _display_url(url),
                        "imported_at": imported_at,
                        "catalog": deepcopy(catalog),
                        "bindings": [],
                    }
                )
                candidate["selected_source_id"] = source_id
                candidate["catalog"] = deepcopy(catalog)
                candidate["bindings"] = []
                candidate["last_import_at"] = imported_at
                candidate["last_error"] = None
                snapshot_path = self._source_snapshot_path(source_id)
                if snapshot_path is None:
                    raise GatewayError(
                        "SOURCE_ID_INVALID",
                        "The imported source could not be stored.",
                        status=500,
                    )
                snapshot_path.parent.mkdir(parents=True, exist_ok=False)
                os.chmod(snapshot_path.parent, 0o700)
                _atomic_write(snapshot_path, source_yaml)
                self._save_state_value(candidate)
                self._state = candidate
                return next(
                    source
                    for source in self.public_state()["sources"]
                    if source["id"] == source_id
                )
            except GatewayError as exc:
                self._record_error(exc)
                raise
            except CompileError as exc:
                error = GatewayError(exc.code, exc.safe_message, status=422)
                self._record_error(error)
                raise error from None
            except Exception:
                error = GatewayError(
                    "UPSTREAM_FETCH_FAILED",
                    "The subscription could not be imported.",
                    status=502,
                )
                self._record_error(error)
                raise error from None

    def select_source(self, source_id: Any) -> Mapping[str, Any]:
        with self._lock:
            source = next(
                (
                    item
                    for item in self._state.get("sources", [])
                    if item.get("id") == source_id
                ),
                None,
            )
            if source is None:
                raise GatewayError(
                    "SOURCE_NOT_FOUND",
                    "The saved source was not found.",
                    status=404,
                )
            candidate = deepcopy(self._state)
            selected = next(
                item for item in candidate["sources"] if item["id"] == source_id
            )
            candidate["selected_source_id"] = source_id
            candidate["catalog"] = deepcopy(selected["catalog"])
            candidate["bindings"] = deepcopy(selected.get("bindings", []))
            candidate["last_error"] = None
            self._save_state_value(candidate)
            self._state = candidate
            return next(
                item
                for item in self.public_state()["sources"]
                if item["id"] == source_id
            )

    def rename_source(self, source_id: Any, name: Any) -> Mapping[str, Any]:
        with self._lock:
            name = _validated_source_name(name)
            source = next(
                (
                    item
                    for item in self._state.get("sources", [])
                    if item.get("id") == source_id
                ),
                None,
            )
            if source is None:
                raise GatewayError(
                    "SOURCE_NOT_FOUND",
                    "The saved source was not found.",
                    status=404,
                )
            if any(
                item.get("id") != source_id and item.get("name") == name
                for item in self._state.get("sources", [])
            ):
                raise GatewayError(
                    "SOURCE_NAME_CONFLICT",
                    "Choose a unique source name.",
                )
            candidate = deepcopy(self._state)
            renamed = next(
                item for item in candidate["sources"] if item["id"] == source_id
            )
            renamed["name"] = name
            candidate["last_error"] = None
            self._save_state_value(candidate)
            self._state = candidate
            return next(
                item
                for item in self.public_state()["sources"]
                if item["id"] == source_id
            )

    def activate_source(self, source_id: Any) -> Mapping[str, Any]:
        with self._lock:
            source = next(
                (
                    item
                    for item in self._state.get("sources", [])
                    if item.get("id") == source_id
                ),
                None,
            )
            if source is None:
                raise GatewayError(
                    "SOURCE_NOT_FOUND",
                    "The saved source was not found.",
                    status=404,
                )
            snapshot_path = self._source_snapshot_path(source_id)
            if snapshot_path is None or not snapshot_path.exists():
                raise GatewayError(
                    "SOURCE_CACHE_MISSING",
                    "The saved source snapshot is unavailable.",
                    status=503,
                )
            try:
                source_yaml = snapshot_path.read_text(encoding="utf-8")
                candidate = deepcopy(self._state)
                selected = next(
                    item for item in candidate["sources"] if item["id"] == source_id
                )
                candidate["selected_source_id"] = source_id
                candidate["catalog"] = deepcopy(selected["catalog"])
                candidate["bindings"] = deepcopy(selected.get("bindings", []))
                result = self._compiled_state(
                    candidate,
                    source_yaml,
                    source=selected,
                )
                if result is None:
                    raise GatewayError(
                        "SOURCE_NOT_CONFIGURED",
                        "Choose at least one managed group before activating this source.",
                        status=422,
                    )
                candidate["active_source_id"] = source_id
                candidate["last_error"] = None
                self._save_state_value(candidate)
                self._state = candidate
                return {"ok": True, "compiled": True, "source_id": source_id}
            except GatewayError as exc:
                self._record_error(exc)
                raise
            except OSError:
                error = GatewayError(
                    "SOURCE_CACHE_MISSING",
                    "The saved source snapshot is unavailable.",
                    status=503,
                )
                self._record_error(error)
                raise error from None

    def delete_source(self, source_id: Any) -> None:
        with self._lock:
            source = next(
                (
                    item
                    for item in self._state.get("sources", [])
                    if item.get("id") == source_id
                ),
                None,
            )
            if source is None:
                raise GatewayError(
                    "SOURCE_NOT_FOUND",
                    "The saved source was not found.",
                    status=404,
                )
            if self._state.get("active_source_id") == source_id:
                raise GatewayError(
                    "SOURCE_ACTIVE",
                    "Activate another source before deleting this one.",
                    status=409,
                )
            candidate = deepcopy(self._state)
            candidate["sources"] = [
                item for item in candidate["sources"] if item["id"] != source_id
            ]
            if candidate.get("selected_source_id") == source_id:
                next_selected = next(
                    (
                        item
                        for item in candidate["sources"]
                        if item["id"] == candidate.get("active_source_id")
                    ),
                    candidate["sources"][0] if candidate["sources"] else None,
                )
                if next_selected is None:
                    candidate["selected_source_id"] = None
                    candidate["catalog"] = {
                        "groups": [],
                        "proxies": [],
                        "providers": [],
                    }
                    candidate["bindings"] = []
                else:
                    candidate["selected_source_id"] = next_selected["id"]
                    candidate["catalog"] = deepcopy(next_selected["catalog"])
                    candidate["bindings"] = deepcopy(
                        next_selected.get("bindings", [])
                    )
            candidate["last_error"] = None
            snapshot_path = self._source_snapshot_path(source_id)
            source_dir = snapshot_path.parent if snapshot_path is not None else None
            deleted_dir = None
            if source_dir is not None and source_dir.exists():
                deleted_dir = self.sources_dir / (
                    f".delete-{source_id}-{secrets.token_hex(4)}"
                )
                os.replace(source_dir, deleted_dir)
            try:
                self._save_state_value(candidate)
            except Exception:
                if deleted_dir is not None and deleted_dir.exists():
                    os.replace(deleted_dir, source_dir)
                raise
            self._state = candidate
            if deleted_dir is not None:
                shutil.rmtree(deleted_dir, ignore_errors=True)

    def upsert_landing(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        with self._lock:
            if not isinstance(payload, Mapping):
                raise GatewayError("LANDING_INVALID", "The landing proxy is invalid.")
            name = payload.get("name")
            landing_url = payload.get("url")
            url_fields = (
                _landing_fields_from_input(
                    landing_url,
                    default_type=payload.get("type"),
                )
                if landing_url not in (None, "")
                else None
            )
            kind = url_fields["type"] if url_fields else payload.get("type")
            server = url_fields["server"] if url_fields else payload.get("server")
            port = url_fields["port"] if url_fields else payload.get("port")
            if not isinstance(name, str) or not name.strip():
                raise GatewayError("LANDING_INVALID", "Enter a landing name.")
            if kind not in {"http", "socks5"}:
                raise GatewayError("LANDING_INVALID", "Choose HTTP or SOCKS5.")
            if not isinstance(server, str) or not server.strip():
                raise GatewayError("LANDING_INVALID", "Enter the landing server.")
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise GatewayError("LANDING_INVALID", "Enter a valid landing port.")
            landing_id = payload.get("id")
            original = None
            if isinstance(landing_id, str):
                original = next(
                    (item for item in self._state["landings"] if item["id"] == landing_id),
                    None,
                )
            if original is None:
                landing_id = secrets.token_hex(6)
                original = {"id": landing_id}
            if url_fields is not None:
                password = url_fields["password"]
                username = url_fields["username"]
            else:
                password = payload.get("password")
                if password in (None, "") and original.get("password"):
                    password = original["password"]
                if password is None:
                    password = ""
                username = payload.get("username", "")
            if not isinstance(password, str):
                raise GatewayError("LANDING_INVALID", "The landing password is invalid.")
            if not isinstance(username, str):
                raise GatewayError("LANDING_INVALID", "The landing username is invalid.")
            candidate = deepcopy(self._state)
            existing = next(
                (item for item in candidate["landings"] if item["id"] == landing_id),
                None,
            )
            if existing is None:
                existing = {"id": landing_id}
                candidate["landings"].append(existing)
            existing.clear()
            existing.update(
                {
                    "id": landing_id,
                    "name": name.strip(),
                    "type": kind,
                    "server": server.strip(),
                    "port": port,
                    "username": username,
                    "password": password,
                    "udp": bool(payload.get("udp", True)),
                }
            )
            active_source_id = candidate.get("active_source_id")
            active_source = next(
                (
                    source
                    for source in candidate.get("sources", [])
                    if source.get("id") == active_source_id
                ),
                None,
            )
            service_affected = any(
                item["exit"].get("kind") == "isp" and item["exit"].get("reference") == landing_id
                for item in candidate.get("services", [])
            )
            if active_source is not None and (service_affected or any(
                binding.get("enabled") and binding.get("landing_id") == landing_id
                for binding in active_source.get("bindings", [])
            )):
                snapshot_path = self._source_snapshot_path(active_source_id)
                if snapshot_path is None or not snapshot_path.exists():
                    raise GatewayError(
                        "SOURCE_CACHE_MISSING",
                        "The active source snapshot is unavailable.",
                        status=503,
                    )
                source_yaml = snapshot_path.read_text(encoding="utf-8")
                self._compiled_state(candidate, source_yaml, source=active_source)
            candidate["last_error"] = None
            self._save_state_value(candidate)
            self._state = candidate
            return next(
                item
                for item in self.public_state()["landings"]
                if item["id"] == landing_id
            )

    def delete_landing(self, landing_id: str) -> None:
        with self._lock:
            if any(item["exit"].get("kind") == "isp" and item["exit"].get("reference") == landing_id
                   for item in self._state.get("services", [])):
                raise GatewayError("LANDING_IN_USE", "此代理仍被服务使用，请先更改服务出口。", status=409)
            candidate = deepcopy(self._state)
            before = len(candidate["landings"])
            candidate["landings"] = [
                item for item in candidate["landings"] if item["id"] != landing_id
            ]
            if len(candidate["landings"]) == before:
                raise GatewayError(
                    "LANDING_NOT_FOUND",
                    "The landing proxy was not found.",
                    status=404,
                )
            affected = False
            if candidate.get("sources"):
                for source in candidate["sources"]:
                    for binding in source.get("bindings", []):
                        if binding.get("landing_id") == landing_id:
                            if source["id"] == candidate.get("active_source_id"):
                                affected = affected or bool(binding.get("enabled"))
                            binding["enabled"] = False
                            binding["landing_id"] = ""
                selected = next(
                    (
                        source
                        for source in candidate["sources"]
                        if source["id"] == candidate.get("selected_source_id")
                    ),
                    None,
                )
                candidate["bindings"] = deepcopy(
                    selected.get("bindings", []) if selected is not None else []
                )
            else:
                for binding in candidate["bindings"]:
                    if binding.get("landing_id") == landing_id:
                        affected = affected or bool(binding.get("enabled"))
                        binding["enabled"] = False
                        binding["landing_id"] = ""
            if affected:
                candidate["active_generation"] = None
                candidate["active_report"] = None
                candidate["active_source_id"] = None
            candidate["last_error"] = None
            self._save_state_value(candidate)
            self._state = candidate

    def _normalized_bindings(self, bindings: Any) -> list[dict[str, Any]]:
        if not isinstance(bindings, list):
            raise GatewayError("BINDINGS_INVALID", "The group bindings are invalid.")
        group_names = {group["name"] for group in self._state["catalog"]["groups"]}
        landing_ids = {landing["id"] for landing in self._state["landings"]}
        normalized = []
        seen = set()
        for binding in bindings:
            if not isinstance(binding, Mapping):
                raise GatewayError("BINDINGS_INVALID", "The group bindings are invalid.")
            group = binding.get("group")
            if not isinstance(group, str) or group not in group_names or group in seen:
                raise GatewayError("BINDINGS_INVALID", "A group binding is stale or duplicated.")
            seen.add(group)
            enabled = bool(binding.get("enabled"))
            landing_id = binding.get("landing_id") or ""
            if enabled and landing_id not in landing_ids:
                raise GatewayError("BINDINGS_INVALID", "Choose a landing for every managed group.")
            front_hop = binding.get("front_hop", {"mode": "source"})
            if not isinstance(front_hop, Mapping):
                raise GatewayError("BINDINGS_INVALID", "The front-hop policy is invalid.")
            normalized.append(
                {
                    "group": group,
                    "enabled": enabled,
                    "landing_id": landing_id,
                    "front_hop": deepcopy(dict(front_hop)),
                }
            )
        return normalized

    def _policy_for_state(
        self,
        state: Mapping[str, Any],
        *,
        source: Optional[Mapping[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        landings = {landing["id"]: landing for landing in state["landings"]}
        managed = {}
        bindings = source.get("bindings", []) if source is not None else state["bindings"]
        for binding in bindings:
            if not binding.get("enabled"):
                continue
            landing = landings.get(binding.get("landing_id"))
            if landing is None:
                raise GatewayError("BINDINGS_INVALID", "A managed group has no landing.")
            managed[binding["group"]] = {
                "landing": {
                    key: deepcopy(landing[key])
                    for key in (
                        "type",
                        "server",
                        "port",
                        "username",
                        "password",
                        "udp",
                    )
                },
                "front_hop": deepcopy(binding["front_hop"]),
            }
        if not managed:
            return None
        return {"version": 1, "managed_groups": managed}

    def _write_generation(
        self,
        source_yaml: str,
        result: CompileResult,
    ) -> str:
        generation = f"{int(self.now() * 1000)}-{secrets.token_hex(4)}"
        temp_dir = Path(tempfile.mkdtemp(prefix=".tmp-", dir=str(self.generations_dir)))
        os.chmod(temp_dir, 0o700)
        try:
            files = {
                "source.yaml": source_yaml,
                "compiled.yaml": result.compiled_yaml,
                "report.json": json.dumps(
                    result.report,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
            }
            for name, content in files.items():
                path = temp_dir / name
                path.write_text(content, encoding="utf-8")
                os.chmod(path, 0o600)
            os.replace(temp_dir, self.generations_dir / generation)
            return generation
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def _compiled_state(
        self,
        state: dict[str, Any],
        source_yaml: str,
        *,
        source: Optional[Mapping[str, Any]] = None,
    ) -> Optional[CompileResult]:
        policy = self._policy_for_state(state, source=source)
        if policy is None and "services" not in state:
            return None
        try:
            if "services" in state:
                result = compile_service_subscription(source_yaml, state["services"], state["landings"], policy)
            else:
                result = compile_subscription(source_yaml, policy)
        except ServicePolicyError as exc:
            raise GatewayError(exc.code, exc.safe_message, status=422,
                               field=exc.field, service_ids=exc.service_ids) from None
        except CompileError as exc:
            raise GatewayError(exc.code, exc.safe_message, status=422) from None
        generation = self._write_generation(source_yaml, result)
        state["active_generation"] = generation
        state["active_report"] = deepcopy(result.report)
        state["last_success_at"] = self._timestamp()
        if "services" in state:
            state["compiled_services_revision"] = state.get("services_revision", 0)
        return result

    def update_bindings(
        self,
        bindings: Any,
        *,
        expected_source_id: Optional[str] = None,
    ) -> Mapping[str, Any]:
        with self._lock:
            if (
                expected_source_id is not None
                and expected_source_id != self._state.get("selected_source_id")
            ):
                raise GatewayError(
                    "SOURCE_CHANGED",
                    "The selected source changed. Reload the page before saving.",
                    status=409,
                )
            normalized = self._normalized_bindings(bindings)
            candidate = deepcopy(self._state)
            candidate["bindings"] = normalized
            selected_source_id = candidate.get("selected_source_id")
            selected_source = next(
                (
                    source
                    for source in candidate.get("sources", [])
                    if source.get("id") == selected_source_id
                ),
                None,
            )
            if selected_source is None:
                raise GatewayError(
                    "SOURCE_NOT_SELECTED",
                    "Select a saved source before editing group bindings.",
                    status=422,
                )
            selected_source["bindings"] = deepcopy(normalized)
            snapshot_path = self._source_snapshot_path(selected_source_id)
            if snapshot_path is None or not snapshot_path.exists():
                raise GatewayError(
                    "SOURCE_CACHE_MISSING",
                    "The selected source snapshot is unavailable.",
                    status=503,
                )
            source_yaml = snapshot_path.read_text(encoding="utf-8")
            try:
                result = self._compiled_state(
                    candidate,
                    source_yaml,
                    source=selected_source,
                )
            except GatewayError as exc:
                candidate["last_error"] = {
                    "code": exc.code,
                    "message": exc.safe_message,
                    "at": self._timestamp(),
                }
                self._save_state_value(candidate)
                self._state = candidate
                raise
            if result is None:
                if self._state.get("active_source_id") == selected_source_id:
                    candidate["active_generation"] = None
                    candidate["active_report"] = None
                    candidate["active_source_id"] = None
            else:
                candidate["active_source_id"] = selected_source_id
            candidate["last_error"] = None
            self._save_state_value(candidate)
            self._state = candidate
            return {"ok": True, "compiled": result is not None}

    def read_subscription(self, token: str) -> str:
        with self._lock:
            expected = self._state["token"]
            if not isinstance(token, str) or not hmac.compare_digest(token, expected):
                raise GatewayError(
                    "SUBSCRIPTION_NOT_FOUND",
                    "The subscription was not found.",
                    status=404,
                )
            generation = self._state.get("active_generation")
            if not generation:
                raise GatewayError(
                    "NO_COMPILED_SUBSCRIPTION",
                    "No compiled subscription is available yet.",
                    status=503,
                )
            path = self._compiled_path(generation)
            if path is None or not path.exists():
                raise GatewayError(
                    "COMPILED_CACHE_MISSING",
                    "The compiled subscription cache is unavailable.",
                    status=503,
                )
            return path.read_text(encoding="utf-8")
