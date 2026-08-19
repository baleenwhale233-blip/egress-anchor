"""State, upstream delivery, and last-known-good storage for EgressAnchor."""

from __future__ import annotations

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


STATE_VERSION = 1
MAX_REDIRECTS = 3
FETCH_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class FetchResult:
    body: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    not_modified: bool = False


class GatewayError(RuntimeError):
    """A caller-safe gateway failure."""

    def __init__(self, code: str, safe_message: str, *, status: int = 400) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.status = status


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


def _display_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    path = parsed.path or "/"
    suffix = "…" if parsed.query else ""
    return urllib.parse.urlunsplit((parsed.scheme, host, path, suffix, ""))


def fetch_subscription(
    url: str,
    *,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> FetchResult:
    """Fetch a bounded UTF-8 subscription without exposing its URL in errors."""
    url = _validated_http_url(url)
    headers = {
        "Accept": "application/yaml, text/yaml, text/plain, */*",
        "User-Agent": "EgressAnchor/0.1",
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


def _default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "token": secrets.token_urlsafe(32),
        "upstream": {
            "url": "",
            "display_url": "",
            "etag": None,
            "last_modified": None,
        },
        "landings": [],
        "bindings": [],
        "catalog": {"groups": [], "proxies": [], "providers": []},
        "active_generation": None,
        "active_report": None,
        "source_updated_at": None,
        "last_success_at": None,
        "last_refresh_at": None,
        "last_error": None,
    }


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
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)
        self.state_path = self.data_dir / "state.json"
        self.source_path = self.data_dir / "source.yaml"
        self.generations_dir = self.data_dir / "generations"
        self.generations_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.generations_dir, 0o700)
        self.fetcher = fetcher
        self.now = now
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
        if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
            raise GatewayError(
                "STATE_INVALID",
                "The local gateway state is unsupported.",
                status=500,
            )
        os.chmod(self.state_path, 0o600)
        return state

    def _save_state_value(self, state: Mapping[str, Any]) -> None:
        _atomic_write(
            self.state_path,
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )

    def _save_state(self) -> None:
        self._save_state_value(self._state)

    def _timestamp(self) -> int:
        return int(self.now())

    def _compiled_path(self, generation: Any) -> Optional[Path]:
        if not isinstance(generation, str) or not re.fullmatch(
            r"[0-9]+-[0-9a-f]{8}", generation
        ):
            return None
        return self.generations_dir / generation / "compiled.yaml"

    def _record_error(self, error: GatewayError) -> None:
        self._state["last_refresh_at"] = self._timestamp()
        self._state["last_error"] = {
            "code": error.code,
            "message": error.safe_message,
            "at": self._timestamp(),
        }
        self._save_state()

    def public_state(self) -> Mapping[str, Any]:
        with self._lock:
            upstream = self._state["upstream"]
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
                "upstream": {
                    "configured": bool(upstream.get("url")),
                    "display_url": upstream.get("display_url", ""),
                },
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
                "source_updated_at": self._state.get("source_updated_at"),
                "last_success_at": self._state.get("last_success_at"),
                "last_refresh_at": self._state.get("last_refresh_at"),
                "last_error": deepcopy(self._state.get("last_error")),
            }

    def configure_upstream(self, url: Any) -> Mapping[str, Any]:
        with self._lock:
            url = _validated_http_url(url)
            if url != self._state["upstream"].get("url"):
                self._state["upstream"]["etag"] = None
                self._state["upstream"]["last_modified"] = None
            self._state["upstream"]["url"] = url
            self._state["upstream"]["display_url"] = _display_url(url)
            self._save_state()
            return self.public_state()["upstream"]

    def upsert_landing(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        with self._lock:
            if not isinstance(payload, Mapping):
                raise GatewayError("LANDING_INVALID", "The landing proxy is invalid.")
            name = payload.get("name")
            kind = payload.get("type")
            server = payload.get("server")
            port = payload.get("port")
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
            password = payload.get("password")
            if password in (None, "") and original.get("password"):
                password = original["password"]
            if password is None:
                password = ""
            if not isinstance(password, str):
                raise GatewayError("LANDING_INVALID", "The landing password is invalid.")
            username = payload.get("username", "")
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
            if self.source_path.exists() and any(
                binding.get("enabled") and binding.get("landing_id") == landing_id
                for binding in candidate["bindings"]
            ):
                source_yaml = self.source_path.read_text(encoding="utf-8")
                self._compiled_state(candidate, source_yaml)
            candidate["last_error"] = None
            self._state = candidate
            self._save_state()
            return next(
                item
                for item in self.public_state()["landings"]
                if item["id"] == landing_id
            )

    def delete_landing(self, landing_id: str) -> None:
        with self._lock:
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
            for binding in candidate["bindings"]:
                if binding.get("landing_id") == landing_id:
                    affected = affected or bool(binding.get("enabled"))
                    binding["enabled"] = False
                    binding["landing_id"] = ""
            if affected:
                candidate["active_generation"] = None
                candidate["active_report"] = None
            candidate["last_error"] = None
            self._state = candidate
            self._save_state()

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

    def _policy_for_state(self, state: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        landings = {landing["id"]: landing for landing in state["landings"]}
        managed = {}
        for binding in state["bindings"]:
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
    ) -> Optional[CompileResult]:
        policy = self._policy_for_state(state)
        if policy is None:
            state["active_generation"] = None
            state["active_report"] = None
            return None
        try:
            result = compile_subscription(source_yaml, policy)
        except CompileError as exc:
            raise GatewayError(exc.code, exc.safe_message, status=422) from None
        generation = self._write_generation(source_yaml, result)
        state["active_generation"] = generation
        state["active_report"] = deepcopy(result.report)
        state["last_success_at"] = self._timestamp()
        return result

    def refresh(self) -> Mapping[str, Any]:
        with self._lock:
            upstream = self._state["upstream"]
            if not upstream.get("url"):
                raise GatewayError("UPSTREAM_NOT_CONFIGURED", "Add an upstream subscription first.")
            try:
                fetched = self.fetcher(
                    upstream["url"],
                    etag=upstream.get("etag"),
                    last_modified=upstream.get("last_modified"),
                )
                if fetched.not_modified:
                    if not self.source_path.exists():
                        raise GatewayError(
                            "SOURCE_CACHE_MISSING",
                            "The upstream returned not-modified but no source cache exists.",
                            status=502,
                        )
                    source_yaml = self.source_path.read_text(encoding="utf-8")
                else:
                    source_yaml = fetched.body
                catalog = inspect_subscription(source_yaml)
                candidate = deepcopy(self._state)
                candidate["catalog"] = deepcopy(catalog)
                candidate["source_updated_at"] = self._timestamp()
                candidate["last_refresh_at"] = self._timestamp()
                candidate["last_error"] = None
                if not fetched.not_modified:
                    candidate["upstream"]["etag"] = fetched.etag
                    candidate["upstream"]["last_modified"] = fetched.last_modified
                result = self._compiled_state(candidate, source_yaml)
                _atomic_write(self.source_path, source_yaml)
                self._state = candidate
                self._save_state()
                return {
                    "ok": True,
                    "compiled": result is not None,
                    "catalog": deepcopy(catalog),
                }
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
                    "The upstream subscription could not be refreshed.",
                    status=502,
                )
                self._record_error(error)
                raise error from None

    def update_bindings(self, bindings: Any) -> Mapping[str, Any]:
        with self._lock:
            normalized = self._normalized_bindings(bindings)
            candidate = deepcopy(self._state)
            candidate["bindings"] = normalized
            if self.source_path.exists():
                source_yaml = self.source_path.read_text(encoding="utf-8")
                try:
                    result = self._compiled_state(candidate, source_yaml)
                except GatewayError as exc:
                    candidate["last_error"] = {
                        "code": exc.code,
                        "message": exc.safe_message,
                        "at": self._timestamp(),
                    }
                    self._state = candidate
                    self._save_state()
                    raise
            else:
                result = None
            candidate["last_error"] = None
            self._state = candidate
            self._save_state()
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
