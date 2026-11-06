from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import json
import plistlib
import sqlite3
import threading
import unittest

from egress_anchor.client_discovery import ClientDiscovery, default_locations
from egress_anchor.diagnostics import DiagnosticsError, MihomoController
from egress_anchor.flclash_readonly import ClientReadError, FlClashLocation, read_flclash
from egress_anchor.gateway import GatewayService


class ClientDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.controller = Mock(return_value=Mock(runtime_info=Mock(return_value={
            'state': 'reachable', 'mode': 'rule', 'tun': True})))

    def fixture(self, name='client', schema=21, wal=False, plist=False, selected=7):
        root = self.root / name
        root.mkdir()
        db = sqlite3.connect(root / 'database.sqlite')
        db.execute(f'PRAGMA user_version={schema}')
        column = 'overwrite_type' if schema == 2 else 'extend_type'
        db.execute(f'CREATE TABLE profiles(id INTEGER PRIMARY KEY, label TEXT, {column} TEXT, url TEXT)')
        db.commit()
        if wal:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA wal_autocheckpoint=0')
            self.addCleanup(db.close)
        db.execute('INSERT INTO profiles VALUES(7, ?, ?, ?)',
                   ('合成订阅', 'standard', 'https://fixture.invalid/private-token'))
        db.commit()
        if not wal:
            db.close()
        prefs = root / ('preferences.plist' if plist else 'shared_preferences.json')
        value = {'flutter.config': json.dumps({'currentProfileId': selected, 'private': 'preferences-secret'})}
        prefs.write_bytes(plistlib.dumps(value) if plist else json.dumps(value).encode())
        (root / 'config.yaml').write_text('external-controller: :19090\nsecret: controller-secret\n')
        info = root / 'Info.plist'
        info.write_bytes(plistlib.dumps({'CFBundleShortVersionString': 'fixture-version'}))
        return FlClashLocation(root, prefs, info)

    def read(self, location):
        return read_flclash(location, controller_factory=self.controller)

    @staticmethod
    def snapshot(root):
        return {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                for p in root.iterdir() if p.is_file()}

    def test_both_schemas_and_mac_preferences_are_read_without_changing_source(self):
        for schema in (2, 21):
            with self.subTest(schema=schema):
                location = self.fixture(str(schema), schema=schema, plist=True)
                before = self.snapshot(location.root)
                result = self.read(location)
                self.assertEqual('合成订阅', result['profile']['name'])
                self.assertEqual(schema, result['schema'])
                self.assertEqual('fixture-version', result['version'])
                self.assertEqual(before, self.snapshot(location.root))
                for secret in ('private-token', 'controller-secret', 'preferences-secret', str(location.root)):
                    self.assertNotIn(secret, repr(result))
        self.controller.assert_called_with('http://127.0.0.1:19090', 'controller-secret')

    def test_live_wal_is_read_from_private_copy_without_changing_source(self):
        location = self.fixture(wal=True)
        before = self.snapshot(location.root)
        result = self.read(location)
        self.assertEqual(7, result['profile']['id'])
        self.assertEqual(before, self.snapshot(location.root))

    def test_no_selected_profile_or_runtime_config_stays_unconfirmed(self):
        location = self.fixture(selected=None)
        (location.root / 'config.yaml').unlink()
        result = self.read(location)
        self.assertIsNone(result['profile'])
        self.assertEqual('unconfirmed', result['runtime']['state'])
        self.controller.assert_not_called()

    def test_unsupported_schema_and_active_journal_are_not_accepted(self):
        location = self.fixture(schema=999)
        with self.assertRaises(ClientReadError) as error:
            self.read(location)
        self.assertEqual('unsupported', error.exception.code)
        location = self.fixture('journal')
        (location.root / 'database.sqlite-journal').write_bytes(b'active fixture journal')
        with self.assertRaises(ClientReadError) as error:
            self.read(location)
        self.assertEqual('changed', error.exception.code)
        self.controller.assert_not_called()

    def test_changed_selection_is_not_published_as_a_consistent_snapshot(self):
        location = self.fixture()
        def change_config(*_args):
            location.preferences.write_text(json.dumps({'flutter.config': {'currentProfileId': None}}))
            return Mock(runtime_info=lambda: {'state': 'reachable'})
        with self.assertRaises(ClientReadError) as error:
            read_flclash(location, controller_factory=change_config)
        self.assertEqual('changed', error.exception.code)

    def test_remote_controller_is_rejected_before_network_access(self):
        location = self.fixture()
        (location.root / 'config.yaml').write_text('external-controller: remote.invalid:9090\nsecret: private-secret\n')
        with patch('urllib.request.build_opener') as opener:
            result = read_flclash(location)
        self.assertEqual('unsupported', result['runtime']['state'])
        opener.assert_not_called()
        self.assertNotIn('private-secret', repr(result))

    def test_bad_data_and_controller_failure_have_safe_separate_results(self):
        location = self.fixture()
        self.controller.side_effect = DiagnosticsError('CONTROLLER_AUTH_FAILED', 'private-secret')
        result = self.read(location)
        self.assertEqual('discovered', result['state'])
        self.assertEqual('unreachable', result['runtime']['state'])
        self.assertNotIn('private-secret', repr(result))
        location.preferences.write_text('private-token, invalid json')
        with self.assertRaises(ClientReadError) as error:
            self.read(location)
        self.assertEqual('unreadable', error.exception.code)
        self.assertNotIn('private-token', str(error.exception))

    def test_missing_multiple_and_duplicate_locations_do_not_select_a_target(self):
        empty = ClientDiscovery(now=lambda: 123)
        self.assertEqual('not_connected', empty.snapshot()['state'])
        self.assertEqual('not_found', empty.discover()['state'])
        one, two = self.fixture('one'), self.fixture('two')
        duplicate = ClientDiscovery(locations=[one, one], reader=self.read)
        self.assertEqual('discovered', duplicate.discover()['state'])
        multi = ClientDiscovery(locations=[one, two], reader=self.read)
        result = multi.discover()
        self.assertEqual('multiple', result['state'])
        self.assertIsNone(result['profile'])
        self.assertFalse(result['can_apply'])
        result['candidates'].clear()
        self.assertEqual(2, len(multi.snapshot()['candidates']))

    def test_read_failure_clears_previous_observation(self):
        location = self.fixture()
        discovery = ClientDiscovery(locations=[location], reader=self.read)
        self.assertEqual('discovered', discovery.discover()['state'])
        location.preferences.unlink()
        result = discovery.discover()
        self.assertEqual('unavailable', result['state'])
        self.assertIsNone(result['profile'])

    def test_slow_discovery_does_not_hold_service_state_lock_or_mark_services_applied(self):
        location = self.fixture()
        started, release = threading.Event(), threading.Event()
        def slow_read(target):
            started.set()
            release.wait(3)
            return self.read(target)
        discovery = ClientDiscovery(locations=[location], reader=slow_read)
        service = GatewayService(self.root / 'gateway', client_discovery=discovery)
        before = service.state_path.read_bytes()
        thread = threading.Thread(target=service.discover_client)
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertEqual('not_connected', service.public_state()['client']['state'])
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        state = service.public_state()
        self.assertEqual('discovered', state['client']['state'])
        self.assertFalse(state['client']['can_probe'])
        self.assertTrue(all(s['status']['runtime']['state'] == 'unconfirmed' for s in state['services']))
        self.assertEqual(before, service.state_path.read_bytes())

    def test_host_locations_are_opt_in_and_platform_specific(self):
        self.assertEqual((), ClientDiscovery().locations)
        self.assertEqual([], default_locations(self.root, 'win32'))
        mac = default_locations(self.root, 'darwin')
        self.assertEqual('.plist', mac[0].preferences.suffix)
        self.assertTrue(all(p.root.is_relative_to(self.root) for p in default_locations(self.root, 'linux')))

    def test_runtime_info_only_exposes_allowlisted_fields(self):
        controller = MihomoController('http://127.0.0.1:9090')
        with patch.object(controller, '_get', return_value={'mode': 'Rule', 'tun': {'enable': True}, 'secret': 'private'}):
            self.assertEqual({'state': 'reachable', 'mode': 'rule', 'tun': True}, controller.runtime_info())
        with patch.object(controller, '_get', return_value={'mode': 'unexpected'}), self.assertRaises(DiagnosticsError):
            controller.runtime_info()

