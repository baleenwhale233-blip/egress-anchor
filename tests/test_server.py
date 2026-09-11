from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import threading
import unittest
import urllib.error
import urllib.request

from egress_anchor.gateway import FetchResult, GatewayService
from egress_anchor.server import create_server


FIXTURES = Path(__file__).parent / "fixtures"


class SingleFetcher:
    def __init__(self, source):
        self.source = source

    def __call__(self, _url, **_validators):
        return FetchResult(self.source)


class GatewayHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        service = GatewayService(
            Path(self.temp.name) / "dev-data",
            fetcher=SingleFetcher(source),
        )
        self.server = create_server(service, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, *, method="GET", payload=None, headers=None):
        data = None
        headers = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.headers, exc.read()
            finally:
                exc.close()

    def json_request(self, path, *, method="GET", payload=None):
        status, headers, body = self.request(path, method=method, payload=payload)
        return status, headers, json.loads(body.decode("utf-8"))

    def test_state_endpoint_is_loopback_scoped_and_returns_local_subscription_url(self):
        status, headers, body = self.json_request("/api/state")

        self.assertEqual(200, status)
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertTrue(body["ok"])
        self.assertTrue(
            body["state"]["subscription"]["url"].startswith(
                f"http://127.0.0.1:{self.server.server_port}/sub/"
            )
        )

    def test_http_workflow_imports_compiles_and_serves_subscription(self):
        status, _, imported = self.json_request(
            "/api/sources",
            method="POST",
            payload={
                "name": "Fixture source",
                "url": "https://subscription.example.invalid/full.yaml?token=fixture",
            },
        )
        self.assertEqual(200, status)
        self.assertTrue(imported["ok"])
        self.assertEqual("Fixture source", imported["source"]["name"])

        status, _, imported_state = self.json_request("/api/state")
        self.assertEqual(200, status)
        self.assertEqual(
            ["AI", "Default"],
            [g["name"] for g in imported_state["state"]["catalog"]["groups"]],
        )

        status, _, landing_body = self.json_request(
            "/api/landings",
            method="POST",
            payload={
                "name": "Fixture landing",
                "type": "socks5",
                "server": "landing.example.invalid",
                "port": 1080,
                "username": "fixture-user",
                "password": "fixture-password",
                "udp": True,
            },
        )
        self.assertEqual(200, status)
        landing_id = landing_body["landing"]["id"]

        status, _, bound = self.json_request(
            "/api/bindings",
            method="PUT",
            payload={
                "bindings": [
                    {
                        "group": "AI",
                        "enabled": True,
                        "landing_id": landing_id,
                        "front_hop": {"mode": "source"},
                    }
                ]
            },
        )
        self.assertEqual(200, status)
        self.assertTrue(bound["compiled"])

        _, _, state = self.json_request("/api/state")
        path = state["state"]["subscription"]["path"]
        status, headers, compiled = self.request(path)
        self.assertEqual(200, status)
        self.assertIn("application/yaml", headers["Content-Type"])
        self.assertIn(b"landing.example.invalid", compiled)
        self.assertEqual("no-store", headers["Cache-Control"])
        etag = headers["ETag"]
        status, conditional_headers, conditional_body = self.request(
            path,
            headers={"If-None-Match": etag},
        )
        self.assertEqual(304, status)
        self.assertEqual(etag, conditional_headers["ETag"])
        self.assertEqual(b"", conditional_body)

    def test_http_can_select_a_saved_source(self):
        _, _, first = self.json_request(
            "/api/sources",
            method="POST",
            payload={
                "name": "Primary",
                "url": "https://primary.example.invalid/import?token=fixture-a",
            },
        )
        _, _, second = self.json_request(
            "/api/sources",
            method="POST",
            payload={
                "name": "Backup",
                "url": "https://backup.example.invalid/import?token=fixture-b",
            },
        )
        self.assertNotEqual(first["source"]["id"], second["source"]["id"])

        status, _, selected = self.json_request(
            f"/api/sources/{first['source']['id']}/select",
            method="PUT",
            payload={},
        )

        self.assertEqual(200, status)
        self.assertEqual(first["source"]["id"], selected["source"]["id"])
        _, _, state = self.json_request("/api/state")
        self.assertEqual(
            first["source"]["id"], state["state"]["selected_source_id"]
        )

    def test_http_can_rename_a_saved_source(self):
        _, _, imported = self.json_request(
            "/api/sources",
            method="POST",
            payload={
                "name": "Original name",
                "url": "https://primary.example.invalid/import?token=fixture",
            },
        )

        status, _, renamed = self.json_request(
            f"/api/sources/{imported['source']['id']}",
            method="PUT",
            payload={"name": "Renamed source"},
        )

        self.assertEqual(200, status)
        self.assertEqual("Renamed source", renamed["source"]["name"])
        _, _, state = self.json_request("/api/state")
        self.assertEqual("Renamed source", state["state"]["sources"][0]["name"])

    def test_http_activate_source_requires_saved_bindings(self):
        _, _, imported = self.json_request(
            "/api/sources",
            method="POST",
            payload={
                "name": "Primary",
                "url": "https://primary.example.invalid/import?token=fixture",
            },
        )

        status, _, activated = self.json_request(
            f"/api/sources/{imported['source']['id']}/activate",
            method="POST",
            payload={},
        )

        self.assertEqual(422, status)
        self.assertEqual("SOURCE_NOT_CONFIGURED", activated["error"]["code"])

    def test_http_can_delete_an_inactive_source(self):
        _, _, imported = self.json_request(
            "/api/sources",
            method="POST",
            payload={
                "name": "Disposable",
                "url": "https://source.example.invalid/import?token=fixture",
            },
        )

        status, _, deleted = self.json_request(
            f"/api/sources/{imported['source']['id']}",
            method="DELETE",
        )

        self.assertEqual(200, status)
        self.assertTrue(deleted["ok"])
        _, _, state = self.json_request("/api/state")
        self.assertEqual([], state["state"]["sources"])

    def test_legacy_persistent_upstream_routes_are_not_available(self):
        status, _, configured = self.json_request(
            "/api/upstream",
            method="PUT",
            payload={"url": "https://legacy.example.invalid/?token=fixture"},
        )
        refresh_status, _, refreshed = self.json_request(
            "/api/refresh",
            method="POST",
            payload={},
        )

        self.assertEqual(404, status)
        self.assertEqual("NOT_FOUND", configured["error"]["code"])
        self.assertEqual(404, refresh_status)
        self.assertEqual("NOT_FOUND", refreshed["error"]["code"])

    def test_invalid_host_is_rejected_before_api_access(self):
        for host in ("attacker.example", "attacker@127.0.0.1", "127.0.0.1:bad"):
            with self.subTest(host=host):
                connection = HTTPConnection(
                    "127.0.0.1", self.server.server_port, timeout=3
                )
                connection.putrequest("GET", "/api/state", skip_host=True)
                connection.putheader("Host", host)
                connection.endheaders()
                response = connection.getresponse()
                body = json.loads(response.read().decode("utf-8"))
                connection.close()

                self.assertEqual(421, response.status)
                self.assertEqual("HOST_NOT_ALLOWED", body["error"]["code"])

    def test_wrong_subscription_token_returns_generic_not_found(self):
        status, _, body = self.json_request("/sub/super-secret-wrong-token")

        self.assertEqual(404, status)
        self.assertEqual("SUBSCRIPTION_NOT_FOUND", body["error"]["code"])
        self.assertNotIn("super-secret-wrong-token", repr(body))

    def test_root_serves_reused_gateway_component_system(self):
        status, _, body = self.request("/")
        html = body.decode("utf-8")

        self.assertEqual(200, status)
        self.assertIn("出口锚点", html)
        for component_class in ("card", "pill", "grid", "modal", "dock", "sw"):
            self.assertIn(f".{component_class}", html)
        for element_id in (
            "source-list",
            "source-module-hint",
            "btn-add-source",
            "source-modal",
            "source-name",
            "source-url",
            "source-url-field",
            "btn-save-source",
            "landing-list",
            "landing-url",
            "landing-url-hint",
            "landing-manual-fields",
            "landing-password-hint",
            "binding-rows",
            "subscription-url",
            "btn-save-bindings",
        ):
            self.assertIn(f'id="{element_id}"', html)
        for removed_id in (
            "upstream-url",
            "btn-save-upstream",
            "btn-refresh",
            "btn-dock-refresh",
        ):
            self.assertNotIn(f'id="{removed_id}"', html)
        self.assertIn("保存并读取", html)
        self.assertIn("完整地址仅用于本次导入", html)
        self.assertIn("data-rename-source", html)
        self.assertNotIn("const setupHint", html)
        self.assertIn("socks5://用户名:密码@服务器:端口", html)
        self.assertIn("http://", html)
        self.assertIn("服务器:端口:用户名:密码", html)
        self.assertIn("用户名:密码@服务器:端口", html)
        self.assertIn("用户名:密码:服务器:端口", html)
        self.assertIn("无协议时使用“方式”", html)
        self.assertIn("••••••••", html)
        self.assertIn("密码已保存；留空保留，输入可替换", html)
        for label in (
            "沿用订阅策略",
            "自动测速选择",
            "固定节点/组",
            "直连落地",
            "仅固定模式可选",
            "请选择节点或策略组",
        ):
            self.assertIn(label, html)
        self.assertIn("select:disabled", html)
        self.assertNotIn(">source</option>", html)
        self.assertNotIn("/api/repair-dns", html)
        self.assertNotIn("应用到 FlClash", html)
        self.assertIn('UPSTREAM_FETCH_FAILED: "无法获取上游订阅。"', html)
        self.assertIn('UPSTREAM_FORMAT_BASE64: "上游返回了 Base64 通用订阅', html)


if __name__ == "__main__":
    unittest.main()
