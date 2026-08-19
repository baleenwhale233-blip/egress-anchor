from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import base64
import threading
import unittest

from egress_anchor.gateway import detect_subscription_format, fetch_subscription


FIXTURES = Path(__file__).parent / "fixtures"


class UserAgentSubscriptionHandler(BaseHTTPRequestHandler):
    source = ""
    user_agents = []
    always_yaml = False

    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        user_agent = self.headers.get("User-Agent", "")
        type(self).user_agents.append(user_agent)
        if type(self).always_yaml or user_agent == "clash.meta":
            body = type(self).source.encode("utf-8")
            content_type = "application/yaml"
        else:
            uri_list = b"ss://fixture-a\nvmess://fixture-b"
            body = base64.b64encode(uri_list)
            content_type = "text/plain"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SubscriptionFormatTests(unittest.TestCase):
    def test_format_detection_distinguishes_common_subscription_responses(self):
        encoded = base64.b64encode(b"ss://fixture-a\nvmess://fixture-b").decode()
        cases = {
            "html": ("<html><body>login required</body></html>", "text/html"),
            "uri-list": ("ss://fixture-a\nvmess://fixture-b", "text/plain"),
            "base64": (encoded, "text/plain"),
            "unknown": ("temporary provider error", "text/plain"),
        }

        for expected, (body, content_type) in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(
                    expected,
                    detect_subscription_format(body, content_type=content_type),
                )

    def test_fetch_retries_base64_response_with_clash_meta_user_agent(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        UserAgentSubscriptionHandler.source = source
        UserAgentSubscriptionHandler.user_agents = []
        UserAgentSubscriptionHandler.always_yaml = False
        server = ThreadingHTTPServer(("127.0.0.1", 0), UserAgentSubscriptionHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: thread.join(timeout=3))

        result = fetch_subscription(
            f"http://127.0.0.1:{server.server_port}/subscription"
        )

        self.assertEqual(source, result.body)
        self.assertEqual(
            ["EgressAnchor/0.1", "clash.meta"],
            UserAgentSubscriptionHandler.user_agents,
        )

    def test_fetch_does_not_retry_a_yaml_response(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        UserAgentSubscriptionHandler.source = source
        UserAgentSubscriptionHandler.user_agents = []
        UserAgentSubscriptionHandler.always_yaml = True
        server = ThreadingHTTPServer(("127.0.0.1", 0), UserAgentSubscriptionHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: thread.join(timeout=3))

        result = fetch_subscription(
            f"http://127.0.0.1:{server.server_port}/subscription"
        )

        self.assertEqual(source, result.body)
        self.assertEqual(["EgressAnchor/0.1"], UserAgentSubscriptionHandler.user_agents)


if __name__ == "__main__":
    unittest.main()
