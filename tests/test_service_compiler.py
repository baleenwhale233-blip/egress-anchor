from copy import deepcopy
from pathlib import Path
import unittest
import yaml

from egress_anchor.compiler import CompileError, inspect_subscription
from egress_anchor.service_catalog import default_services
from egress_anchor.service_compiler import airport_choices, compile_service_subscription
from egress_anchor.service_policy import ServicePolicyError

SOURCE = (Path(__file__).parent / 'fixtures/inline.yaml').read_text()
LANDING = dict(id='residential', name='Home', type='http', server='landing.example.invalid',
               port=8080, username='fixture-user', password='fixture-pass', udp=False)


class ServiceCompilerTests(unittest.TestCase):
    def setUp(self):
        self.records = default_services()

    def compile(self, source=SOURCE, legacy=None):
        return compile_service_subscription(source, self.records, [LANDING], legacy)

    def test_inherit_preserves_every_original_setting(self):
        output = self.compile()
        self.assertEqual(yaml.safe_load(SOURCE), yaml.safe_load(output.compiled_yaml))
        self.assertEqual(0, output.report['service_rule_count'])

    def test_service_rules_precede_source_without_mutating_unrelated_groups(self):
        self.records[2]['exit'] = {'kind': 'direct'}
        config = yaml.safe_load(self.compile().compiled_yaml)
        original = yaml.safe_load(SOURCE)
        self.assertEqual(['DOMAIN-SUFFIX,deepseek.com,DIRECT'] + original['rules'], config['rules'])
        self.assertEqual(original['proxy-groups'], config['proxy-groups'])
        self.assertEqual(original['proxies'], config['proxies'])

    def test_airport_rejects_direct_nested_and_managed_source_paths(self):
        catalog = inspect_subscription(SOURCE)
        self.assertIn('AI', airport_choices(catalog))
        self.assertNotIn('Default', airport_choices(catalog))
        self.assertNotIn('AI', airport_choices(catalog, [{'group': 'AI', 'enabled': True}]))
        catalog['groups'].append(dict(name='Nested', proxies=['Default']))
        self.assertNotIn('Nested', airport_choices(catalog))
        self.records[1]['exit'] = {'kind': 'airport', 'reference': 'Default'}
        with self.assertRaises(ServicePolicyError): self.compile()

    def test_airport_rule_uses_exact_source_name(self):
        self.records[1]['exit'] = {'kind': 'airport', 'reference': 'AI'}
        config = yaml.safe_load(self.compile().compiled_yaml)
        self.assertIn('DOMAIN-SUFFIX,chatgpt.com,AI', config['rules'])
        self.assertEqual(yaml.safe_load(SOURCE)['proxy-groups'], config['proxy-groups'])

    def test_airport_rejects_dynamic_and_provider_groups_even_with_explicit_nodes(self):
        original = yaml.safe_load(SOURCE)
        original['proxy-groups'][0]['include-all'] = True
        original['proxy-providers'] = {'upstream': {'type': 'inline', 'payload': [original['proxies'][0]]}}
        original['proxy-groups'].append(dict(name='Provider', type='select', proxies=['HK-01'], use=['upstream']))
        source = yaml.safe_dump(original)
        catalog = inspect_subscription(source)
        self.assertNotIn('AI', airport_choices(catalog))
        self.assertNotIn('Provider', airport_choices(catalog))
        self.assertIn('HK-01', airport_choices(catalog))
        self.records[1]['exit'] = {'kind': 'airport', 'reference': 'AI'}
        with self.assertRaises(ServicePolicyError): self.compile(source)

    def test_isp_wraps_a_single_terminal_and_rejects_udp_before_fallback(self):
        self.records[0]['exit'] = {'kind': 'isp', 'reference': LANDING['id']}
        before = deepcopy(self.records)
        result = self.compile()
        config = yaml.safe_load(result.compiled_yaml)
        groups = {g['name']: g for g in config['proxy-groups']}
        wrapper = groups['EA-Service-claude']
        self.assertEqual(1, len(wrapper['proxies']))
        terminal = next(p for p in config['proxies'] if p['name'] == wrapper['proxies'][0])
        self.assertEqual(LANDING['server'], terminal['server'])
        front = groups[terminal['dialer-proxy']]
        self.assertEqual('url-test', front['type'])
        self.assertNotIn('DIRECT', front['proxies'])
        self.assertIn('AND,((NETWORK,UDP),(DOMAIN-SUFFIX,claude.ai)),REJECT', config['rules'])
        self.assertLess(config['rules'].index('AND,((NETWORK,UDP),(DOMAIN-SUFFIX,claude.ai)),REJECT'), config['rules'].index('DOMAIN-SUFFIX,claude.ai,EA-Service-claude'))
        self.assertEqual(yaml.safe_load(SOURCE)['rules'], config['rules'][-2:])
        self.assertEqual(before, self.records)
        self.assertNotIn('fixture-pass', repr(result.report))

    def test_exclusions_only_remove_current_service_claim(self):
        self.records[0]['exit'] = {'kind': 'isp', 'reference': LANDING['id']}
        self.records[0]['exclude_domains'] = ['claude.com', 'docs.claude.ai']
        rules = yaml.safe_load(self.compile().compiled_yaml)['rules']
        self.assertFalse(any(',claude.com' in rule for rule in rules))
        self.assertIn('AND,((DOMAIN-SUFFIX,claude.ai),(NOT,((DOMAIN-SUFFIX,docs.claude.ai)))),EA-Service-claude', rules)
        self.assertFalse(any('docs.claude.ai' in rule and rule.endswith(',DIRECT') for rule in rules))

    def test_missing_reference_and_generated_name_collision_are_rejected(self):
        self.records[0]['exit'] = {'kind': 'isp', 'reference': 'missing'}
        with self.assertRaises(ServicePolicyError): self.compile()
        self.records[0]['exit']['reference'] = LANDING['id']
        original = yaml.safe_load(SOURCE)
        original['proxy-groups'].append(dict(name='EA-Service-claude', type='select', proxies=['HK-01']))
        with self.assertRaises(CompileError): self.compile(yaml.safe_dump(original))

    def test_legacy_binding_is_retained_with_new_service_rules(self):
        legacy = {'version': 1, 'managed_groups': {'AI': {'landing': {k: v for k, v in LANDING.items() if k not in {'id', 'name'}}, 'front_hop': {'mode': 'source'}}}}
        self.records[2]['exit'] = {'kind': 'direct'}
        config = yaml.safe_load(self.compile(legacy=legacy).compiled_yaml)
        group = next(g for g in config['proxy-groups'] if g['name'] == 'AI')
        self.assertEqual(1, len(group['proxies']))
        self.assertIn('DOMAIN-SUFFIX,deepseek.com,DIRECT', config['rules'])
