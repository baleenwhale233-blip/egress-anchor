from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import threading
import unittest

from egress_anchor.gateway import GatewayService
from egress_anchor.server import create_server
from test_service_catalog import edit


class ServiceHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        cls.service = GatewayService(Path(cls.temp.name))
        cls.server = create_server(cls.service, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def request(self, path, method='GET', data=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=json.dumps(data) if data is not None else None,
                               headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, response.getheader('Content-Type'), response.read()
        finally: connection.close()

    def test_new_shell_modules_and_official_icons_are_available(self):
        status, mime, body = self.request('/')
        self.assertEqual(200, status)
        self.assertIn('服务出口', body.decode())
        self.assertIn(b'type="module"', body)
        for name in ('app', 'api', 'ui', 'services', 'service-editor', 'service-details', 'client', 'resources', 'settings'):
            status, mime, _ = self.request(f'/static/{name}.js')
            self.assertEqual(200, status)
            self.assertIn('javascript', mime)
        status, mime, body = self.request('/static/assets/lucide.svg')
        self.assertEqual(200, status)
        self.assertEqual('image/svg+xml', mime)
        self.assertIn(b'<symbol id="anchor"', body)

    def test_discovery_api_does_not_scan_host_or_claim_application_in_library_mode(self):
        status, _, body = self.request('/api/client')
        self.assertEqual(200, status)
        client = json.loads(body)['client']
        self.assertEqual('not_found', client['state'])
        self.assertFalse(client['can_apply'])
        self.assertFalse(client['can_probe'])

    def test_static_allowlist_does_not_expose_source_or_private_files(self):
        for path in ('/static/../app.py', '/static/assets/SOURCES.md', '/static/legacy.html',
                     '/dev-data/state.json', '/static/%2e%2e/egress_anchor/gateway.py'):
            self.assertEqual(404, self.request(path)[0])

    def test_create_update_delete_custom_service_over_http(self):
        draft = dict(name='Custom HTTP test', extra_domains=['http.example.test'], exit={'kind': 'inherit'})
        status, _, result = self.request('/api/services', 'POST', dict(service=draft, revision=0, source_id=None))
        self.assertEqual(200, status, result)
        _, _, body = self.request('/api/state')
        created = json.loads(body)['state']['services'][-1]
        payload = {**edit(created, exit={'kind': 'direct'}), 'source_id': None}
        self.assertEqual(200, self.request('/api/services', 'PUT', payload)[0])
        stale_status, _, stale_body = self.request('/api/services', 'PUT', payload)
        self.assertEqual(409, stale_status)
        self.assertEqual('revision', json.loads(stale_body)['error']['field'])
        _, _, body = self.request('/api/state')
        current = json.loads(body)['state']['services'][-1]
        self.assertEqual(200, self.request('/api/services', 'DELETE', {**edit(current), 'source_id': None})[0])

    def test_invalid_payload_is_safe_and_field_addressable(self):
        status, _, body = self.request('/api/services', 'POST', dict(service=dict(name='X', template_id=[]), revision=0, source_id=None))
        self.assertEqual(422, status)
        self.assertEqual('service.template_id', json.loads(body)['error']['field'])
        status, _, body = self.request('/api/services', 'POST', dict(service=dict(name='X', extra_domains=['https://secret.invalid/key'], exit={'kind':'direct'}), revision=0, source_id=None))
        self.assertEqual(422, status)
        self.assertNotIn(b'secret.invalid', body)
