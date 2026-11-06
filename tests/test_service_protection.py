from copy import deepcopy
import unittest

from egress_anchor.service_catalog import default_services, service_view
from egress_anchor.service_policy import ServicePolicyError, parse_service_policies
from egress_anchor.service_protection import ProtectionError, parse_protection, protection_details
from egress_anchor.service_status import verification_view


class ProtectionContractTests(unittest.TestCase):
    def test_custom_overrides_are_immutable_and_validate_supported_values(self):
        raw = {'preset': 'custom', 'overrides': {'dns': 'via_exit', 'udp': 'block'}}
        policy = parse_protection(raw)
        raw['overrides']['dns'] = 'inherit'
        self.assertEqual('via_exit', policy.as_dict()['overrides']['dns'])
        cases = [(None, 'protection'), ({'preset': []}, 'protection.preset'),
                 ({'preset': 'off', 'overrides': {'udp': 'block'}}, 'protection.overrides'),
                 ({'preset': 'custom', 'overrides': {'tls': 'on'}}, 'protection.overrides'),
                 ({'preset': 'custom', 'overrides': {'udp': True}}, 'protection.overrides.udp')]
        for value, field in cases:
            with self.subTest(value=value), self.assertRaises(ProtectionError) as error:
                parse_protection(value)
            self.assertEqual(field, error.exception.field)

    def test_legacy_strict_input_maps_to_preset_without_accepting_contradictions(self):
        raw = dict(id='fixture', name='Fixture', domains=['fixture.test'], exit={'kind': 'direct'}, strict=True)
        policy = parse_service_policies([raw])[0]
        self.assertEqual('recommended', policy.protection.preset)
        self.assertTrue(policy.strict)
        raw['protection'] = {'preset': 'off'}
        with self.assertRaises(ServicePolicyError):
            parse_service_policies([raw])
        del raw['strict']
        self.assertFalse(parse_service_policies([raw])[0].strict)

    def test_off_keeps_isp_udp_constraint_and_separates_global_and_browser_scope(self):
        rows = {row['id']: row for row in protection_details(parse_protection({'preset': 'off'}), 'isp')}
        self.assertEqual('block', rows['udp']['configured'])
        self.assertTrue(rows['udp']['mandatory'])
        self.assertEqual('service', rows['udp']['scope'])
        for key in ('dns', 'ipv6'):
            self.assertEqual('client', rows[key]['scope'])
            self.assertEqual('inherit', rows[key]['configured'])
        self.assertEqual('browser', rows['webrtc']['scope'])
        self.assertTrue(all(row['runtime']['state'] == 'unverified' for row in rows.values()))
        rows['udp']['options']['block'] = 'changed by caller'
        fresh = protection_details(parse_protection({'preset': 'off'}), 'isp')
        self.assertEqual('拒绝 UDP', fresh[0]['options']['block'])

    def test_legacy_records_render_off_without_mutating_storage_or_claiming_runtime(self):
        records = default_services()
        for record in records:
            del record['protection']
        before = deepcopy(records)
        view = service_view({'services': records})
        self.assertEqual(before, records)
        for record in view['services']:
            self.assertEqual('off', record['protection']['preset'])
            self.assertEqual('saved', record['status']['configuration']['state'])
            self.assertEqual('unconfirmed', record['status']['runtime']['state'])
            self.assertIsNone(record['status']['application']['applied_revision'])
        self.assertEqual([True, False, False], [p['supported'] for p in view['capabilities']['protection']['presets']])

    def test_results_are_scoped_and_invalidated_by_configuration_identity(self):
        evidence = dict(state='verified', checked_at=123, service_revision=2, source_id='source-a',
                        generation='gen-a', items=[dict(id='exit', state='verified', reason='Matched')])
        current = dict(revision=2, source_id='source-a', generation='gen-a')
        view = verification_view(evidence, **current)
        self.assertEqual('verified', view['state'])
        self.assertEqual('unverified', view['items'][1]['state'])
        self.assertEqual(5, len(view['items']))
        for change in ({'revision': 3}, {'source_id': 'source-b'}, {'generation': 'gen-b'}):
            stale = verification_view(evidence, **{**current, **change})
            self.assertEqual('stale', stale['state'])
            self.assertTrue(all(row['state'] == 'stale' for row in stale['items']))
        self.assertEqual('verified', evidence['items'][0]['state'])
        self.assertEqual('unverified', verification_view({'state': []}, **current)['state'])
        malformed = {**evidence, 'items': [{'id': 'exit', 'state': []}]}
        self.assertEqual('unverified', verification_view(malformed, **current)['items'][0]['state'])

