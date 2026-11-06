from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import yaml

from egress_anchor.gateway import FetchResult, GatewayError, GatewayService
from test_service_catalog import edit

SOURCE = (Path(__file__).parent / 'fixtures/inline.yaml').read_text()


class ServiceGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.service = GatewayService(self.directory, fetcher=lambda *_a, **_kw: FetchResult(SOURCE))

    def save(self, index=0, **changes):
        state = self.service.public_state()
        payload = edit(state['services'][index], **changes)
        payload['source_id'] = state['selected_source_id']
        return self.service.update_service(payload)

    def import_source(self, name='Primary'):
        return self.service.import_source(name, 'https://fixture.example.invalid/sub')

    def subscription(self):
        state = self.service.public_state()
        return self.service.read_subscription(state['subscription']['token'])

    def test_save_without_source_survives_reload_but_never_claims_applied(self):
        self.assertFalse(self.save(2, exit={'kind': 'direct'})['compiled'])
        reloaded = GatewayService(self.directory).public_state()
        self.assertEqual('direct', reloaded['services'][2]['exit']['kind'])
        self.assertEqual('saved', reloaded['services'][2]['application'])
        self.assertEqual('unverified', reloaded['services'][2]['verification'])
        self.assertFalse(reloaded['client']['can_apply'])

    def test_generate_and_restore_inherited_rules_without_touching_other_services(self):
        self.import_source()
        self.assertTrue(self.save(2, exit={'kind': 'direct'})['compiled'])
        self.assertIn('DOMAIN-SUFFIX,deepseek.com,DIRECT', self.subscription())
        state = self.service.public_state()
        self.assertEqual('pending_client', state['services'][2]['application'])
        self.assertEqual('inherited', state['services'][0]['application'])
        self.save(2, exit={'kind': 'inherit'})
        self.assertEqual(yaml.safe_load(SOURCE), yaml.safe_load(self.subscription()))
        self.assertEqual('pending_client', self.service.public_state()['services'][2]['application'])

    def test_stale_service_and_source_cannot_overwrite_saved_state(self):
        self.import_source()
        initial = self.service.public_state()
        stale = edit(initial['services'][0], name='Stale name')
        stale['source_id'] = initial['selected_source_id']
        self.save(0, name='Current name')
        before = self.service.public_state()
        with self.assertRaises(GatewayError) as context: self.service.update_service(stale)
        self.assertEqual('SERVICE_CHANGED', context.exception.code)
        self.assertEqual(before, self.service.public_state())
        self.import_source('Secondary')
        with self.assertRaises(GatewayError) as context: self.service.update_service(stale)
        self.assertEqual('SOURCE_CHANGED', context.exception.code)

    def test_compile_failure_keeps_last_good_subscription_and_saved_records(self):
        self.import_source()
        self.save(2, exit={'kind': 'direct'})
        before, body = self.service.public_state(), self.subscription()
        with self.assertRaises(GatewayError): self.save(0, exit={'kind': 'airport', 'reference': 'Missing'})
        self.assertEqual(before, self.service.public_state())
        self.assertEqual(body, self.subscription())

    def test_persistence_failure_does_not_publish_a_new_generation(self):
        self.import_source()
        self.save(2, exit={'kind': 'direct'})
        before, body = self.service.public_state(), self.subscription()
        with patch.object(self.service, '_save_state_value', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.save(2, exit={'kind': 'inherit'})
        self.assertEqual(before, self.service.public_state())
        self.assertEqual(body, self.subscription())
        self.assertEqual(body, GatewayService(self.directory).read_subscription(before['subscription']['token']))

    def test_landing_edit_recompiles_and_referenced_deletion_is_blocked(self):
        self.import_source()
        landing = self.service.upsert_landing(dict(name='Home', type='http', server='fixture.invalid', port=8080, password='private-fixture'))
        self.save(0, exit={'kind': 'isp', 'reference': landing['id']})
        before = self.service.public_state()
        with self.assertRaises(GatewayError) as context: self.service.delete_landing(landing['id'])
        self.assertEqual('LANDING_IN_USE', context.exception.code)
        self.service.upsert_landing({**landing, 'server': 'changed.invalid', 'password': ''})
        self.assertIn('changed.invalid', self.subscription())
        self.assertIn('private-fixture', self.subscription())
        self.assertNotEqual(before['subscription']['generation'], self.service.public_state()['subscription']['generation'])
        self.assertNotIn('private-fixture', repr(self.service.public_state()))
        self.save(0, exit={'kind': 'inherit'})
        self.service.delete_landing(landing['id'])
        self.assertEqual([], self.service.public_state()['landings'])

    def test_selecting_another_source_keeps_last_good_output_and_resets_status(self):
        first = self.import_source()
        self.save(2, exit={'kind': 'direct'})
        body = self.subscription()
        second = self.import_source('Secondary')
        self.assertEqual('saved', self.service.public_state()['services'][2]['application'])
        self.assertEqual(body, self.subscription())
        self.save(2, exit={'kind': 'inherit'})
        self.assertEqual(second['id'], self.service.public_state()['active_source_id'])
        self.assertNotEqual(first['id'], second['id'])

    def test_multiple_missing_references_can_be_repaired_without_publishing_partial_config(self):
        self.import_source()
        for index in (0, 1): self.save(index, exit={'kind': 'airport', 'reference': 'AI'})
        body = self.subscription()
        with patch.object(self.service, 'fetcher', return_value=FetchResult(SOURCE.replace('AI', 'NewAirport'))):
            target = self.import_source('Changed groups')
        result = self.save(0, exit={'kind': 'airport', 'reference': 'NewAirport'})
        self.assertFalse(result['compiled'])
        self.assertEqual(['chatgpt'], result['blocked_service_ids'])
        self.assertEqual(body, self.subscription())
        self.assertEqual('saved', self.service.public_state()['services'][0]['application'])
        self.assertTrue(self.service.public_state()['services_need_generation'])
        before = self.service.public_state()
        with self.assertRaises(GatewayError): self.save(1, exit={'kind': 'airport', 'reference': 'Missing'})
        self.assertEqual(before, self.service.public_state())
        self.assertTrue(self.save(1, exit={'kind': 'airport', 'reference': 'NewAirport'})['compiled'])
        self.assertNotEqual(body, self.subscription())
        self.assertEqual(target['id'], self.service.public_state()['active_source_id'])
        self.assertFalse(self.service.public_state()['services_need_generation'])
