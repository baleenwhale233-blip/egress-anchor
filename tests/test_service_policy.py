from copy import deepcopy
from dataclasses import FrozenInstanceError
import unittest

from egress_anchor.service_policy import ServicePolicyError, parse_service_policies


def service(service_id="claude", domain="claude.example", kind="isp", reference="landing-1", **fields):
    exit_choice = {"kind": kind}
    if kind in {"airport", "isp"}:
        exit_choice["reference"] = reference
    return {"id": service_id, "name": service_id, "domains": [domain], "exit": exit_choice, **fields}


class ServicePolicyTests(unittest.TestCase):
    def assert_policy_error(self, code, policies):
        with self.assertRaises(ServicePolicyError) as caught:
            parse_service_policies(policies)
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def test_four_exits_do_not_conflate_direct_service_and_landing(self):
        policies = parse_service_policies([
            service("inherited", "inherited.example", "inherit"),
            service("deepseek", "deepseek.example", "direct"),
            service("chatgpt", "chatgpt.example", "airport", "原 AI 组"),
            service("claude", "claude.example", "isp", "landing-1"),
        ])
        self.assertEqual(["inherit", "direct", "airport", "isp"], [p.exit.kind for p in policies])
        self.assertEqual([None, None, "原 AI 组", "landing-1"], [p.exit.reference for p in policies])
        self.assertTrue(all(not policy.strict for policy in policies))

    def test_empty_list_and_inherited_placeholder_need_no_landing_or_domains(self):
        self.assertEqual((), parse_service_policies([]))
        policy, = parse_service_policies([
            {"id": "claude", "name": "Claude", "exit": {"kind": "inherit"}},
        ])
        self.assertEqual((), policy.domains)
        self.assertIsNone(policy.exit.reference)

    def test_explicit_services_require_domains(self):
        for kind in ("direct", "airport", "isp"):
            with self.subTest(kind=kind):
                self.assert_policy_error("SERVICE_DOMAINS_REQUIRED", [service(kind=kind, domains=[])])

    def test_only_airport_and_isp_require_references(self):
        for kind in ("airport", "isp"):
            for reference in (None, "", 1):
                with self.subTest(kind=kind, reference=reference):
                    self.assert_policy_error("SERVICE_POLICY_INVALID", [service(kind=kind, reference=reference)])
        for kind in ("inherit", "direct"):
            raw = service(kind=kind)
            raw["exit"]["reference"] = "landing-1"
            self.assert_policy_error("SERVICE_POLICY_INVALID", [raw])

    def test_input_is_not_mutated_and_result_does_not_alias_input(self):
        raw = [service(domains=[" CLAUDE.EXAMPLE. ", "api.claude.example"])]
        before = deepcopy(raw)
        policy, = parse_service_policies(raw)
        self.assertEqual(before, raw)
        raw[0]["domains"].append("other.example")
        raw[0]["exit"]["reference"] = "landing-2"
        self.assertFalse(policy.matches_domain("other.example"))
        self.assertEqual("landing-1", policy.exit.reference)
        with self.assertRaises(FrozenInstanceError):
            policy.strict = True

    def test_airport_reference_preserves_the_exact_source_name(self):
        policy, = parse_service_policies([service(kind="airport", reference=" AI group ")])
        self.assertEqual(" AI group ", policy.exit.reference)

    def test_renaming_preserves_identity_and_isp_reference(self):
        before, = parse_service_policies([service(name="Claude")])
        after, = parse_service_policies([service(name="工作用 Claude")])
        self.assertEqual((before.id, before.exit), (after.id, after.exit))

    def test_strict_state_is_independent_per_service(self):
        claude, chatgpt = parse_service_policies([
            service(strict=True), service("chatgpt", "chatgpt.example", strict=False),
        ])
        self.assertTrue(claude.strict)
        self.assertFalse(chatgpt.strict)
        self.assert_policy_error("SERVICE_STRICT_REQUIRES_OVERRIDE", [service(kind="inherit", strict=True)])
        for value in (0, 1, "true", None):
            self.assert_policy_error("SERVICE_POLICY_INVALID", [service(strict=value)])

    def test_domains_are_canonical_and_match_only_label_boundaries(self):
        policy, = parse_service_policies([service(domains=[" CLAUDE.EXAMPLE. ", "claude.example"])])
        self.assertEqual(("claude.example",), policy.domains)
        for hostname in ("claude.example", "api.claude.example", "API.CLAUDE.EXAMPLE."):
            self.assertTrue(policy.matches_domain(hostname))
        for hostname in ("notclaude.example", "claude.example.evil", "127.0.0.1", "https://claude.example"):
            self.assertFalse(policy.matches_domain(hostname))

    def test_idna_normalization_and_deduplication(self):
        policy, = parse_service_policies([service(domains=["例子.example", "xn--fsqu00a.example"])])
        self.assertEqual(("xn--fsqu00a.example",), policy.domains)
        self.assertTrue(policy.matches_domain("api.例子.example"))

    def test_invalid_domains_are_not_treated_as_rule_or_url_input(self):
        for domain in ("https://user:secret@example.com/path", "*.example.com", "example.com:443",
                       "example.com,DIRECT", "127.0.0.1", "::1", "example..com", "example.com..",
                       "-bad.example", "bad_.example", "com", "a" * 64 + ".example", "", None):
            with self.subTest(domain=domain):
                error = self.assert_policy_error("SERVICE_DOMAIN_INVALID", [service(domains=[domain])])
                self.assertEqual("services[0].domains[0]", error.field)
                self.assertNotIn("secret", str(error))

    def test_exclusions_remove_only_this_services_domain_claim(self):
        broad = service(domains=["example.com"], exclude_domains=["login.example.com", "old.example"])
        login = service("login", "login.example.com", "airport", "普通机场")
        policies = parse_service_policies([broad, login])
        self.assertTrue(policies[0].matches_domain("api.example.com"))
        self.assertFalse(policies[0].matches_domain("sub.login.example.com"))
        self.assertTrue(policies[1].matches_domain("sub.login.example.com"))
        self.assertIn("old.example", policies[0].exclude_domains)

    def test_parent_exclusion_covers_nested_include(self):
        first = service(domains=["api.example.com"], exclude_domains=["example.com"])
        second = service("other", "api.example.com", "direct")
        policies = parse_service_policies([first, second])
        self.assertFalse(policies[0].matches_domain("api.example.com"))
        self.assertTrue(policies[1].matches_domain("api.example.com"))

    def test_overlaps_are_rejected_independently_of_list_order(self):
        for second_domain in ("example.com", "api.example.com"):
            first = service("first", "example.com", "direct")
            second = service("second", second_domain)
            for ordered in ([first, second], [second, first]):
                error = self.assert_policy_error("SERVICE_DOMAIN_CONFLICT", ordered)
                self.assertEqual(("first", "second"), error.service_ids)

    def test_partial_exclusion_does_not_hide_remaining_overlap(self):
        first = service("first", "example.com", exclude_domains=["login.example.com"])
        second = service("second", "example.com", "direct")
        self.assert_policy_error("SERVICE_DOMAIN_CONFLICT", [first, second])

    def test_inherited_services_do_not_claim_domains(self):
        policies = parse_service_policies([
            service("inherited", "example.com", "inherit"), service("other", "example.com"),
        ])
        self.assertEqual(2, len(policies))

    def test_redundant_domains_within_one_service_are_not_cross_service_conflicts(self):
        policy, = parse_service_policies([service(domains=["example.com", "api.example.com"])])
        self.assertTrue(policy.matches_domain("api.example.com"))

    def test_duplicate_ids_fail_before_ambiguous_edits(self):
        error = self.assert_policy_error("SERVICE_ID_DUPLICATE", [service(), service()])
        self.assertEqual(("claude",), error.service_ids)

    def test_unknown_and_malformed_fields_fail_without_echoing_input(self):
        for patch in ({"id": "raw-secret/invalid"}, {"name": "bad\nname"}, {"domains": "example.com"},
                      {"exclude_domains": None}, {"password": "raw-secret"},
                      {"exit": {"kind": "unknown", "password": "raw-secret"}},
                      {"exit": {"kind": []}}, {"exit": None}):
            error = self.assert_policy_error("SERVICE_POLICY_INVALID", [service(**patch)])
            self.assertNotIn("raw-secret", repr(error))
        for value in (None, {}, "services", [None]):
            self.assert_policy_error("SERVICE_POLICY_INVALID", value)


if __name__ == "__main__":
    unittest.main()
