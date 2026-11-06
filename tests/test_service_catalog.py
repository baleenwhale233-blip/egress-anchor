from copy import deepcopy
import unittest

from egress_anchor.service_catalog import change_service, default_services, resolve_services
from egress_anchor.service_policy import ServicePolicyError


def edit(record, **changes):
    keys = ('id', 'name', 'template_id', 'extra_domains', 'exclude_domains', 'exit', 'strict')
    return dict(service={**{key: deepcopy(record[key]) for key in keys}, **changes}, revision=record['revision'])


class ServiceCatalogTests(unittest.TestCase):
    def test_defaults_do_not_silently_change_a_route(self):
        records = default_services()
        self.assertEqual(['claude', 'chatgpt', 'deepseek'], [s['id'] for s in records])
        self.assertTrue(all(s['exit']['kind'] == 'inherit' for s in records))
        self.assertTrue(all(not s['strict'] for s in records))
        records[0]['extra_domains'].append('local.test')
        self.assertEqual([], default_services()[0]['extra_domains'])

    def test_templates_merge_without_copying_into_user_storage(self):
        records = default_services()
        updated = change_service(records, edit(records[0], extra_domains=['Docs.Example.com.']), now=123)
        self.assertEqual(['docs.example.com'], updated[0]['extra_domains'])
        self.assertIn('claude.ai', resolve_services(updated)[0].domains)
        self.assertEqual([], records[0]['extra_domains'])
        self.assertEqual(1, updated[0]['revision'])

    def test_stale_revision_rejected_and_independent_service_edit_allowed(self):
        records = default_services()
        first = change_service(records, edit(records[0], name='Claude work'), now=123)
        with self.assertRaises(ServicePolicyError) as context:
            change_service(first, edit(records[0]), now=124)
        self.assertEqual('SERVICE_CHANGED', context.exception.code)
        second = change_service(first, edit(records[1], exit={'kind': 'direct'}), now=124)
        self.assertEqual('Claude work', second[0]['name'])
        self.assertEqual('direct', second[1]['exit']['kind'])

    def test_conflicting_active_domains_rejected_and_exclusion_resolves(self):
        records = default_services()
        records = change_service(records, edit(records[0], exit={'kind': 'direct'}), now=123)
        custom = dict(name='Docs', extra_domains=['docs.claude.ai'], exit={'kind': 'direct'})
        with self.assertRaises(ServicePolicyError) as context:
            change_service(records, dict(service=custom, revision=0), now=123)
        self.assertEqual('SERVICE_DOMAIN_CONFLICT', context.exception.code)
        records = change_service(records, edit(records[0], exclude_domains=['docs.claude.ai']), now=124)
        updated = change_service(records, dict(service=custom, revision=0), now=125)
        self.assertEqual(4, len(updated))
        self.assertTrue(updated[-1]['id'].startswith('custom-'))

    def test_custom_delete_and_builtin_cannot_be_deleted(self):
        records = default_services()
        with self.assertRaises(ServicePolicyError):
            change_service(records, edit(records[0]), now=123, delete=True)
        records = change_service(records, dict(service=dict(name='Docs', exit={'kind': 'inherit'}), revision=0), now=123)
        self.assertEqual(3, len(change_service(records, edit(records[-1]), now=124, delete=True)))

    def test_malformed_payloads_use_field_errors_not_internal_exceptions(self):
        for raw in (None, [], {}, {'template_id': []}, {'template_id': {}},
                    {'name': 'x', 'exit': []}, {'name': 12, 'exit': {'kind': 'direct'}},
                    {'name': 'x', 'exit': {'kind': 'inherit'}, 'extra_domains': 'x'},
                    {'name': 'x', 'exit': {'kind': 'inherit'}, 'extra_domains': [False]}):
            with self.subTest(raw=raw), self.assertRaises(ServicePolicyError):
                change_service(default_services(), dict(service=raw, revision=0), now=123)

    def test_strict_switch_cannot_claim_unimplemented_protection(self):
        records = default_services()
        with self.assertRaises(ServicePolicyError) as context:
            change_service(records, edit(records[0], strict=True, exit={'kind': 'direct'}), now=123)
        self.assertEqual('SERVICE_STRICT_UNAVAILABLE', context.exception.code)
