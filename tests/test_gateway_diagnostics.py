from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from egress_anchor.gateway import FetchResult, GatewayError, GatewayService


class FixtureDiagnostics:
    def __init__(self):
        self.calls = []
        self.during_probe = None

    def run(self, plan, **settings):
        self.calls.append((plan, settings))
        if self.during_probe:
            self.during_probe()
        return {'source_id': plan.source_id, 'generation': plan.generation,
                'group': plan.group, 'chain': {'ok': True, 'ms': 192}}


class GatewayDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name) / 'gateway'
        self.source = (Path(__file__).parent / 'fixtures' / 'inline.yaml').read_text()
        self.diagnostics = FixtureDiagnostics()
        self.service = GatewayService(self.data_dir,
            fetcher=lambda *_args, **_kwargs: FetchResult(self.source),
            diagnostics=self.diagnostics)
        self.source_profile = self.service.import_source('Primary', 'https://source.invalid/')
        self.landing = self.service.upsert_landing({
            'name': 'Fixture landing', 'type': 'socks5', 'server': 'landing.invalid',
            'port': 1080, 'username': 'fixture-user', 'password': 'fixture-password',
        })
        self.binding = {'group': 'AI', 'enabled': True, 'landing_id': self.landing['id'],
                        'front_hop': {'mode': 'source'}}
        self.service.update_bindings([self.binding])

    def payload(self):
        state = self.service.public_state()
        return {'source_id': state['selected_source_id'],
                'generation': state['subscription']['generation'],
                'group': 'AI', 'binding': deepcopy(self.binding)}

    def test_probe_uses_an_immutable_saved_binding_and_does_not_change_subscription(self):
        before = self.service.public_state()
        compiled = self.service.read_subscription(before['subscription']['token'])

        report = self.service.probe_binding(self.payload())

        self.assertEqual(192, report['chain']['ms'])
        self.assertEqual(before, self.service.public_state())
        self.assertEqual(compiled, self.service.read_subscription(before['subscription']['token']))
        plan, settings = self.diagnostics.calls[0]
        self.assertEqual('fixture-password', plan.landing['password'])
        self.assertTrue(plan.landing_name.startswith('EA·'))
        self.assertTrue(plan.front_name.endswith('·front'))
        self.assertEqual('http://127.0.0.1:9090', settings['controller_url'])
        self.assertNotIn('fixture-password', repr(report))

    def test_controller_key_is_local_private_and_can_be_kept_or_cleared(self):
        configured = self.service.set_diagnostics_controller({
            'url': 'http://localhost:19090/', 'secret': 'fixture-controller-secret',
        })
        self.assertEqual({'url': 'http://localhost:19090', 'has_secret': True}, configured)
        self.assertNotIn('fixture-controller-secret', repr(self.service.public_state()))
        self.assertEqual(0o600, self.service.state_path.stat().st_mode & 0o777)
        self.service.set_diagnostics_controller({'url': 'http://localhost:19090', 'secret': ''})
        self.service.probe_binding(self.payload())
        self.assertEqual('fixture-controller-secret', self.diagnostics.calls[-1][1]['controller_secret'])
        self.service.set_diagnostics_controller({'url': 'http://localhost:19090', 'clear_secret': True})
        self.service.probe_binding(self.payload())
        self.assertEqual('', self.diagnostics.calls[-1][1]['controller_secret'])
        reloaded = GatewayService(self.data_dir)
        self.assertFalse(reloaded.public_state()['diagnostics_controller']['has_secret'])

    def test_invalid_controller_settings_do_not_modify_state_or_probe_anything(self):
        before = self.service.public_state()
        for payload in ({'url': 'http://evil.example:9090'},
                        {'url': 'http://127.0.0.1:9090', 'secret': 'key\r\nInjected: yes'},
                        {'url': 'http://127.0.0.1:9090', 'clear_secret': 'yes'}):
            with self.subTest(payload=payload), self.assertRaises(GatewayError):
                self.service.set_diagnostics_controller(payload)
            self.assertEqual(before, self.service.public_state())
        self.assertEqual([], self.diagnostics.calls)

    def test_failed_connection_settings_write_keeps_the_previous_settings(self):
        before = self.service.public_state()
        with patch('egress_anchor.gateway._atomic_write', side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError):
                self.service.set_diagnostics_controller({'url': 'http://localhost:19090'})
        self.assertEqual(before, self.service.public_state())

    def test_unsaved_binding_is_rejected_instead_of_testing_the_old_configuration(self):
        payload = self.payload()
        payload['binding']['front_hop'] = {'mode': 'direct'}

        with self.assertRaises(GatewayError) as raised:
            self.service.probe_binding(payload)

        self.assertEqual('PROBE_UNSAVED', raised.exception.code)
        self.assertEqual([], self.diagnostics.calls)

    def test_stale_source_or_generation_is_rejected_before_network_work(self):
        payload = self.payload()
        self.service.update_bindings([self.binding])
        with self.assertRaises(GatewayError) as raised:
            self.service.probe_binding(payload)
        self.assertEqual('PROBE_STALE', raised.exception.code)
        self.service.import_source('Backup', 'https://backup.invalid/')
        with self.assertRaises(GatewayError) as raised:
            self.service.probe_binding(payload)
        self.assertEqual('SOURCE_CHANGED', raised.exception.code)
        self.assertEqual([], self.diagnostics.calls)

    def test_results_are_discarded_if_the_configuration_changes_during_the_probe(self):
        self.diagnostics.during_probe = lambda: self.service.update_bindings([
            {**self.binding, 'front_hop': {'mode': 'direct'}}])

        with self.assertRaises(GatewayError) as raised:
            self.service.probe_binding(self.payload())

        self.assertEqual('PROBE_STALE', raised.exception.code)

    def test_subscription_delivery_is_available_while_the_probe_runs(self):
        token = self.service.public_state()['subscription']['token']
        completed = threading.Event()
        def during_probe():
            def read_subscription():
                self.service.read_subscription(token)
                completed.set()
            thread = threading.Thread(target=read_subscription, daemon=True)
            thread.start()
            self.assertTrue(completed.wait(1), 'Probe held the gateway lock during network work')
            thread.join(1)
        self.diagnostics.during_probe = during_probe

        self.service.probe_binding(self.payload())

        self.assertTrue(completed.is_set())


if __name__ == '__main__':
    unittest.main()
