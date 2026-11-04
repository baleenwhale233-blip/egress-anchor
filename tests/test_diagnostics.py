from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import replace
import json
import socketserver
import threading
import unittest
import urllib.parse

from egress_anchor.diagnostics import (
    BindingDiagnostics, BindingProbe, LandingProbe, DiagnosticsError,
    validate_controller_url,
)


class FixtureController(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.server.requests.append((self.command, self.path))
        path = urllib.parse.urlsplit(self.path).path
        name = urllib.parse.unquote(path.removeprefix('/proxies/').removesuffix('/delay'))
        if self.headers.get('Authorization') != 'Bearer fixture-controller-secret':
            self.send_json(401, {'message': 'fixture-controller-secret'})
        elif (name, path.endswith('/delay')) in self.server.responses:
            self.send_json(*self.server.responses[(name, path.endswith('/delay'))])
        elif path.endswith('/delay'):
            self.send_json(200, {'delay': 192 if name == 'EA·fixture·landing' else 133})
        else:
            proxies = {
                'AI': {'now': 'EA·fixture·landing', 'all': ['EA·fixture·landing']},
                'EA·fixture·landing': {'type': 'Socks5'},
                'EA·fixture·front': {'now': '🇺🇸 US 01', 'all': ['🇺🇸 US 01']},
                '🇺🇸 US 01': {'type': 'Shadowsocks'},
            }
            self.send_json(200, proxies.get(name, {}))

    def send_json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        if 300 <= status < 400:
            self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/untrusted')
        self.end_headers()
        self.wfile.write(data)


class BindingDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureController)
        self.server.requests = []
        self.server.responses = {}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.plan = BindingProbe(
            source_id='fixture-source', generation='fixture-generation', group='AI',
            landing_name='EA·fixture·landing', front_name='EA·fixture·front',
            front_mode='source', landing={'type': 'socks5', 'server': 'landing.invalid',
                'port': 1080, 'username': 'fixture-user', 'password': 'fixture-password'},
        )

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def test_reports_real_core_chain_and_front_delays_separately(self):
        diagnostics = BindingDiagnostics(landing_probe=lambda _landing: {
            'ok': True, 'ip': '198.51.100.23', 'elapsed_ms': 1000, 'via': 'direct',
        })

        report = diagnostics.run(self.plan, controller_url=self.url,
                                 controller_secret='fixture-controller-secret')

        self.assertTrue(report['chain']['ok'])
        self.assertEqual(192, report['chain']['ms'])
        self.assertTrue(report['selection']['ok'])
        self.assertEqual('🇺🇸 US 01', report['front']['name'])
        self.assertEqual(133, report['front']['ms'])
        self.assertEqual('198.51.100.23', report['landing']['ip'])
        self.assertEqual(1000, report['landing']['elapsed_ms'])
        self.assertTrue(all(method == 'GET' for method, _path in self.server.requests))
        for secret in ('fixture-controller-secret', 'fixture-user', 'fixture-password'):
            self.assertNotIn(secret, repr(report))
        self.assertNotIn('fixture-password', repr(self.plan))

    def run_probe(self, *, plan=None, secret='fixture-controller-secret', landing=None):
        diagnostics = BindingDiagnostics(landing_probe=lambda _landing: landing or {
            'ok': True, 'ip': '198.51.100.23', 'elapsed_ms': 1000, 'via': 'direct',
        })
        return diagnostics.run(plan or self.plan, controller_url=self.url,
                               controller_secret=secret)

    def test_chain_timeout_still_reports_working_front_and_landing(self):
        self.server.responses[('EA·fixture·landing', True)] = (504, {'message': 'fixture-password'})
        report = self.run_probe()

        self.assertFalse(report['chain']['ok'])
        self.assertEqual('PROBE_TIMEOUT', report['chain']['code'])
        self.assertTrue(report['front']['ok'])
        self.assertTrue(report['landing']['ok'])
        self.assertNotIn('fixture-password', repr(report))

    def test_controller_auth_failure_does_not_hide_landing_result_or_leak_key(self):
        report = self.run_probe(secret='wrong-fixture-controller-secret')

        self.assertEqual('CONTROLLER_AUTH_FAILED', report['chain']['code'])
        self.assertTrue(report['landing']['ok'])
        self.assertNotIn('wrong-fixture-controller-secret', repr(report))

    def test_stale_client_topology_is_not_reported_as_verified(self):
        self.server.responses[('AI', False)] = (200, {'now': '🇺🇸 US 01',
            'all': ['EA·fixture·landing', '🇺🇸 US 01']})

        report = self.run_probe()

        self.assertFalse(report['selection']['ok'])
        self.assertEqual('RUNTIME_MISMATCH', report['chain']['code'])
        self.assertEqual('RUNTIME_MISMATCH', report['selection']['code'])
        self.assertFalse(any('/delay?' in path for _method, path in self.server.requests))

    def test_zero_millisecond_delay_is_a_valid_success(self):
        self.server.responses[('EA·fixture·landing', True)] = (200, {'delay': 0})
        self.assertEqual({'ok': True, 'name': 'EA·fixture·landing', 'ms': 0},
                         self.run_probe()['chain'])

    def test_controller_redirect_is_not_followed_with_the_access_key(self):
        self.server.responses[('AI', False)] = (302, {'message': 'fixture-controller-secret'})

        report = self.run_probe()

        self.assertFalse(report['chain']['ok'])
        self.assertEqual(1, len(self.server.requests))
        self.assertNotIn('fixture-controller-secret', repr(report))

    def test_invalid_runtime_group_members_return_a_safe_result(self):
        self.server.responses[('EA·fixture·front', False)] = (200, {'now': '🇺🇸 US 01', 'all': 7})

        report = self.run_probe()

        self.assertEqual('CONTROLLER_RESPONSE_INVALID', report['chain']['code'])
        self.assertTrue(report['landing']['ok'])

    def test_two_running_tests_limit_further_network_work(self):
        condition = threading.Condition()
        release = threading.Event()
        started = []
        errors = []
        def landing(_landing):
            with condition:
                started.append(True)
                condition.notify_all()
            release.wait(5)
            return {'ok': True, 'ip': '198.51.100.23', 'elapsed_ms': 1}
        diagnostics = BindingDiagnostics(landing_probe=landing)
        def run():
            try:
                diagnostics.run(self.plan, controller_url=self.url, controller_secret='fixture-controller-secret')
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=run, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        try:
            with condition:
                self.assertTrue(condition.wait_for(lambda: len(started) == 2, timeout=3))
            with self.assertRaises(DiagnosticsError) as raised:
                diagnostics.run(self.plan, controller_url=self.url)
            self.assertEqual('PROBE_BUSY', raised.exception.code)
            self.assertEqual(429, raised.exception.status)
        finally:
            release.set()
            for thread in threads:
                thread.join(5)
        self.assertEqual([], errors)

    def test_direct_mode_skips_the_front_probe(self):
        report = self.run_probe(plan=replace(self.plan, front_name=None, front_mode='direct'))

        self.assertTrue(report['front']['skipped'])
        self.assertNotIn('ms', report['front'])
        self.assertTrue(report['chain']['ok'])
        self.assertFalse(any('front' in path for _method, path in self.server.requests))

    def test_controller_addresses_are_loopback_only_and_have_no_embedded_credentials(self):
        for address in ('https://example.com:9090', 'http://127.0.0.1',
                        'http://secret@localhost:9090', 'http://localhost:9090/path',
                        'http://localhost:9090?secret=fixture', 'http://localhost:bad'):
            with self.subTest(address=address), self.assertRaises(DiagnosticsError):
                validate_controller_url(address)


class FixtureHttpProxy(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_CONNECT(self):
        if self.server.reject_auth:
            self.send_response(407)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.rfile.readline()
        while self.rfile.readline() not in (b'\r\n', b'\n', b''):
            pass
        body = json.dumps({'ip': self.server.exit_ip}).encode()
        self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(body)).encode()
                         + b'\r\nConnection: close\r\n\r\n' + body)


class LandingProbeTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHttpProxy)
        self.server.reject_auth = True
        self.server.exit_ip = '198.51.100.23'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.landing = {'type': 'http', 'server': '127.0.0.1',
                        'port': self.server.server_port, 'username': 'fixture-user',
                        'password': 'fixture-password'}

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def test_http_authentication_failure_is_safe_and_not_reported_as_connected(self):
        result = LandingProbe(endpoint='http://exit.example.invalid/json')(self.landing)

        self.assertFalse(result['ok'])
        self.assertEqual('LANDING_AUTH_FAILED', result['code'])
        self.assertNotIn('fixture-password', repr(result))
        self.assertNotIn('fixture-user', repr(result))

    def test_http_proxy_returns_an_exit_ip_and_verification_duration(self):
        self.server.reject_auth = False

        result = LandingProbe(endpoint='http://exit.example.invalid/json')(self.landing)

        self.assertTrue(result['ok'])
        self.assertEqual('198.51.100.23', result['ip'])
        self.assertEqual('direct', result['via'])
        self.assertEqual('accepted', result['authentication'])
        self.assertGreaterEqual(result['elapsed_ms'], 0)
        self.assertNotIn('ms', result)

    def test_invalid_ip_response_cannot_look_like_a_success(self):
        self.server.reject_auth = False
        self.server.exit_ip = 'not-an-ip'

        result = LandingProbe(endpoint='http://exit.example.invalid/json')(self.landing)

        self.assertFalse(result['ok'])
        self.assertEqual('IP_LOOKUP_FAILED', result['code'])

    def test_socks5_authenticates_then_looks_up_the_exit_through_the_proxy(self):
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), FixtureSocksProxy)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        landing = {**self.landing, 'type': 'socks5', 'port': server.server_address[1]}

        result = LandingProbe(endpoint='http://exit.example.invalid/json')(landing)

        self.assertTrue(result['ok'])
        self.assertEqual('198.51.100.24', result['ip'])
        self.assertNotIn('fixture-password', repr(result))

    def test_socks5_authentication_rejection_does_not_leak_credentials(self):
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), FixtureSocksProxy)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        landing = {**self.landing, 'type': 'socks5', 'port': server.server_address[1],
                   'password': 'wrong-fixture-password'}

        result = LandingProbe(endpoint='http://exit.example.invalid/json')(landing)

        self.assertFalse(result['ok'])
        self.assertEqual('LANDING_AUTH_FAILED', result['code'])
        self.assertNotIn('wrong-fixture-password', repr(result))


class FixtureSocksProxy(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        version, count = self.rfile.read(2)
        self.rfile.read(count)
        self.wfile.write(bytes([version, 2]))
        version, length = self.rfile.read(2)
        username = self.rfile.read(length)
        password = self.rfile.read(self.rfile.read(1)[0])
        accepted = username == b'fixture-user' and password == b'fixture-password'
        self.wfile.write(bytes([version, 0 if accepted else 1]))
        if not accepted:
            return
        self.rfile.read(4)
        self.rfile.read(self.rfile.read(1)[0])
        self.rfile.read(2)
        self.wfile.write(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00')
        self.rfile.readline()
        while self.rfile.readline() not in (b'\r\n', b'\n', b''):
            pass
        body = b'{"ip":"198.51.100.24"}'
        self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(body)).encode()
                         + b'\r\nConnection: close\r\n\r\n' + body)


if __name__ == '__main__':
    unittest.main()
