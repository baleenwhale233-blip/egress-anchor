from pathlib import Path
from copy import deepcopy
import re
import unittest

import yaml

from egress_anchor import CompileError, compile_subscription, inspect_subscription


FIXTURES = Path(__file__).parent / "fixtures"


def source_policy():
    return {
        "version": 1,
        "managed_groups": {
            "AI": {
                "landing": {
                    "type": "socks5",
                    "server": "landing.example.invalid",
                    "port": 1080,
                    "username": "fixture-user",
                    "password": "fixture-landing-password",
                    "udp": True,
                },
                "front_hop": {"mode": "source"},
            }
        },
    }


def mutate_compiled(result, mutate):
    header = "\n".join(result.compiled_yaml.splitlines()[:3])
    config = yaml.safe_load(result.compiled_yaml)
    mutate(config, result.report["managed_groups"][0])
    body = yaml.safe_dump(
        config,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )
    return f"{header}\n{body}"


class CompilerBehaviorTests(unittest.TestCase):
    def test_inspection_discovers_groups_without_exposing_proxy_details(self):
        source = (FIXTURES / "provider_flow.yaml").read_text(encoding="utf-8")

        catalog = inspect_subscription(source)

        groups = {group["name"]: group for group in catalog["groups"]}
        proxies = {proxy["name"]: proxy for proxy in catalog["proxies"]}
        self.assertEqual("select", groups["AI"]["type"])
        self.assertEqual(["HK-01"], groups["AI"]["proxies"])
        self.assertEqual(["airport"], groups["AI"]["use"])
        self.assertEqual(["airport"], catalog["providers"])
        self.assertEqual({"name": "HK-01", "type": "socks5"}, proxies["HK-01"])
        self.assertNotIn("server", repr(catalog))
        self.assertNotIn("fixture-token", repr(catalog))

    def test_managed_group_can_only_select_the_generated_landing(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")

        result = compile_subscription(source, source_policy())
        compiled = yaml.safe_load(result.compiled_yaml)
        groups = {group["name"]: group for group in compiled["proxy-groups"]}
        proxies = {proxy["name"]: proxy for proxy in compiled["proxies"]}

        managed = groups["AI"]
        self.assertEqual("select", managed["type"])
        self.assertEqual(1, len(managed["proxies"]))
        self.assertEqual("https://assets.example.invalid/ai.svg", managed["icon"])
        self.assertFalse(managed["hidden"])
        self.assertTrue(managed["disable-udp"])

        landing = proxies[managed["proxies"][0]]
        self.assertEqual("socks5", landing["type"])
        self.assertEqual("landing.example.invalid", landing["server"])
        self.assertEqual("fixture-landing-password", landing["password"])

        front = groups[landing["dialer-proxy"]]
        self.assertEqual(["HK-01", "US-01"], front["proxies"])
        self.assertTrue(front["hidden"])
        self.assertEqual("REJECT", front["empty-fallback"])

    def test_unmanaged_configuration_is_semantically_unchanged(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        original = yaml.safe_load(source)

        result = compile_subscription(source, source_policy())
        compiled = yaml.safe_load(result.compiled_yaml)

        original_proxies = {proxy["name"]: proxy for proxy in original["proxies"]}
        compiled_proxies = {proxy["name"]: proxy for proxy in compiled["proxies"]}
        original_default = next(
            group for group in original["proxy-groups"] if group["name"] == "Default"
        )
        compiled_default = next(
            group for group in compiled["proxy-groups"] if group["name"] == "Default"
        )

        self.assertEqual(
            original_proxies,
            {name: compiled_proxies[name] for name in original_proxies},
        )
        self.assertEqual(original_default, compiled_default)
        self.assertEqual(original["rules"], compiled["rules"])
        self.assertEqual(original["mixed-port"], compiled["mixed-port"])
        self.assertEqual(original["mode"], compiled["mode"])
        self.assertIn("GW·user-owned", compiled_proxies)
        self.assertIn("EA·user-owned", compiled_proxies)

    def test_compilation_is_deterministic_and_structurally_idempotent(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policy = source_policy()

        first = compile_subscription(source, policy)
        repeated = compile_subscription(source, policy)
        compiled_again = compile_subscription(first.compiled_yaml, policy)

        self.assertEqual(first.compiled_yaml, repeated.compiled_yaml)
        self.assertEqual(first.compiled_yaml, compiled_again.compiled_yaml)

    def test_every_front_mode_is_structurally_idempotent(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        modes = {
            "source": {"mode": "source"},
            "fixed": {"mode": "fixed", "proxy": "US-01"},
            "automatic": {"mode": "automatic"},
            "direct": {"mode": "direct"},
        }

        for name, front_hop in modes.items():
            with self.subTest(mode=name):
                policy = source_policy()
                policy["managed_groups"]["AI"]["front_hop"] = front_hop
                first = compile_subscription(source, policy)
                compiled_again = compile_subscription(first.compiled_yaml, policy)
                self.assertEqual(first.compiled_yaml, compiled_again.compiled_yaml)

    def test_managed_policy_mapping_order_does_not_change_output(self):
        source = (FIXTURES / "nested_groups.yaml").read_text(encoding="utf-8")
        ai_binding = source_policy()["managed_groups"]["AI"]
        region_binding = deepcopy(ai_binding)
        region_binding["landing"]["server"] = "region-a-landing.example.invalid"
        first_policy = {
            "version": 1,
            "managed_groups": {"AI": ai_binding, "Region A": region_binding},
        }
        second_policy = {
            "version": 1,
            "managed_groups": {"Region A": region_binding, "AI": ai_binding},
        }

        first = compile_subscription(source, first_policy)
        second = compile_subscription(source, second_policy)

        self.assertEqual(first.compiled_yaml, second.compiled_yaml)
        self.assertEqual(first.report, second.report)

    def test_omitted_source_mode_is_canonicalized_to_explicit_source_mode(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        explicit = source_policy()
        implicit = source_policy()
        implicit["managed_groups"]["AI"]["front_hop"] = {}

        explicit_result = compile_subscription(source, explicit)
        implicit_result = compile_subscription(source, implicit)

        self.assertEqual(explicit_result.compiled_yaml, implicit_result.compiled_yaml)
        self.assertEqual(explicit_result.report, implicit_result.report)

    def test_automatic_defaults_are_canonicalized_in_policy_fingerprint(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        implicit = source_policy()
        implicit["managed_groups"]["AI"]["front_hop"] = {"mode": "automatic"}
        explicit = source_policy()
        explicit["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "url": "https://www.gstatic.com/generate_204",
            "interval": 300,
        }

        implicit_result = compile_subscription(source, implicit)
        explicit_result = compile_subscription(source, explicit)

        self.assertEqual(implicit_result.compiled_yaml, explicit_result.compiled_yaml)

    def test_changed_policy_cannot_reuse_an_already_compiled_body(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        original_policy = source_policy()
        compiled = compile_subscription(source, original_policy)
        changed_policy = source_policy()
        changed_policy["managed_groups"]["AI"]["landing"]["password"] = "rotated-secret"

        with self.assertRaises(CompileError) as raised:
            compile_subscription(compiled.compiled_yaml, changed_policy)

        self.assertEqual("ALREADY_COMPILED_MISMATCH", raised.exception.code)
        self.assertNotIn("rotated-secret", str(raised.exception))

    def test_removed_binding_cannot_leave_old_generated_credentials_in_output(self):
        source = (FIXTURES / "nested_groups.yaml").read_text(encoding="utf-8")
        full_policy = source_policy()
        region_binding = deepcopy(full_policy["managed_groups"]["AI"])
        region_binding["landing"]["password"] = "removed-binding-secret"
        full_policy["managed_groups"]["Region A"] = region_binding
        compiled = compile_subscription(source, full_policy)
        reduced_policy = source_policy()

        with self.assertRaises(CompileError) as raised:
            compile_subscription(compiled.compiled_yaml, reduced_policy)

        self.assertEqual("ALREADY_COMPILED_MISMATCH", raised.exception.code)
        self.assertNotIn("removed-binding-secret", str(raised.exception))

    def test_changed_automatic_candidates_cannot_reuse_compiled_input(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        first_policy = source_policy()
        first_policy["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "proxies": ["HK-01"],
        }
        compiled = compile_subscription(source, first_policy)
        changed_policy = source_policy()
        changed_policy["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "proxies": ["US-01"],
        }

        with self.assertRaises(CompileError) as raised:
            compile_subscription(compiled.compiled_yaml, changed_policy)

        self.assertEqual("ALREADY_COMPILED_MISMATCH", raised.exception.code)

    def test_changed_source_front_candidates_cannot_reuse_compiled_input(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        compiled = compile_subscription(source, policy)

        def replace_source_candidates(config, report):
            front = next(
                group
                for group in config["proxy-groups"]
                if group["name"] == report["front"]
            )
            front["proxies"] = ["DIRECT"]

        tampered = mutate_compiled(compiled, replace_source_candidates)
        with self.assertRaises(CompileError) as raised:
            compile_subscription(tampered, policy)

        self.assertEqual("ALREADY_COMPILED_MISMATCH", raised.exception.code)

    def test_tampered_compiled_topology_is_never_treated_as_idempotent(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        base_policy = source_policy()
        source_result = compile_subscription(source, base_policy)

        def wrapper_bypass(config, _report):
            group = next(item for item in config["proxy-groups"] if item["name"] == "AI")
            group["proxies"] = ["US-01"]

        def changed_landing(config, report):
            proxy = next(item for item in config["proxies"] if item["name"] == report["landing"])
            proxy["server"] = "tampered.example.invalid"

        def visible_front(config, report):
            group = next(item for item in config["proxy-groups"] if item["name"] == report["front"])
            group["hidden"] = False

        def unsafe_empty_fallback(config, report):
            group = next(item for item in config["proxy-groups"] if item["name"] == report["front"])
            group["empty-fallback"] = "DIRECT"

        def removed_public_wrapper(config, _report):
            group = next(item for item in config["proxy-groups"] if item["name"] == "AI")
            group["name"] = "Renamed AI"

        source_cases = {
            "wrapper bypass": wrapper_bypass,
            "landing changed": changed_landing,
            "front visibility changed": visible_front,
            "empty fallback changed": unsafe_empty_fallback,
            "public wrapper removed": removed_public_wrapper,
        }
        for label, mutation in source_cases.items():
            with self.subTest(case=label):
                tampered = mutate_compiled(source_result, mutation)
                with self.assertRaises(CompileError) as raised:
                    compile_subscription(tampered, base_policy)
                self.assertEqual("ALREADY_COMPILED_MISMATCH", raised.exception.code)

        fixed_policy = source_policy()
        fixed_policy["managed_groups"]["AI"]["front_hop"] = {
            "mode": "fixed",
            "proxy": "US-01",
        }
        fixed_result = compile_subscription(source, fixed_policy)

        def changed_fixed_choice(config, report):
            group = next(item for item in config["proxy-groups"] if item["name"] == report["front"])
            group["proxies"] = ["HK-01"]

        def fixed_gained_provider_use(config, report):
            group = next(item for item in config["proxy-groups"] if item["name"] == report["front"])
            group["use"] = []

        for label, mutation in {
            "fixed choice": changed_fixed_choice,
            "fixed gained use": fixed_gained_provider_use,
        }.items():
            with self.subTest(case=label):
                tampered_fixed = mutate_compiled(fixed_result, mutation)
                with self.assertRaises(CompileError) as fixed_error:
                    compile_subscription(tampered_fixed, fixed_policy)
                self.assertEqual("ALREADY_COMPILED_MISMATCH", fixed_error.exception.code)

        automatic_policy = source_policy()
        automatic_policy["managed_groups"]["AI"]["front_hop"] = {"mode": "automatic"}
        automatic_result = compile_subscription(source, automatic_policy)

        def changed_automatic_type(config, report):
            group = next(item for item in config["proxy-groups"] if item["name"] == report["front"])
            group["type"] = "select"

        def changed_automatic_url(config, report):
            group = next(item for item in config["proxy-groups"] if item["name"] == report["front"])
            group["url"] = "https://probe.example.invalid/generate_204"

        def changed_automatic_interval(config, report):
            group = next(item for item in config["proxy-groups"] if item["name"] == report["front"])
            group["interval"] = 30

        for label, mutation in {
            "automatic type": changed_automatic_type,
            "automatic url": changed_automatic_url,
            "automatic interval": changed_automatic_interval,
        }.items():
            with self.subTest(case=label):
                tampered_automatic = mutate_compiled(automatic_result, mutation)
                with self.assertRaises(CompileError) as automatic_error:
                    compile_subscription(tampered_automatic, automatic_policy)
                self.assertEqual(
                    "ALREADY_COMPILED_MISMATCH",
                    automatic_error.exception.code,
                )

        include_source = (FIXTURES / "include_all.yaml").read_text(encoding="utf-8")
        include_result = compile_subscription(include_source, base_policy)

        def removed_generated_exclusion(config, report):
            group = next(item for item in config["proxy-groups"] if item["name"] == report["front"])
            group["exclude-filter"] = "(?i)expired"

        def missing_generated_exclusion(config, report):
            group = next(item for item in config["proxy-groups"] if item["name"] == report["front"])
            del group["exclude-filter"]

        for label, mutation in {
            "generated exclusion changed": removed_generated_exclusion,
            "generated exclusion missing": missing_generated_exclusion,
        }.items():
            with self.subTest(case=label):
                tampered_include = mutate_compiled(include_result, mutation)
                with self.assertRaises(CompileError) as include_error:
                    compile_subscription(tampered_include, base_policy)
                self.assertEqual("ALREADY_COMPILED_MISMATCH", include_error.exception.code)

    def test_missing_managed_group_fails_with_a_safe_code(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        binding = source_policy()["managed_groups"]["AI"]
        policy = {"version": 1, "managed_groups": {"Removed upstream": binding}}

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, policy)

        self.assertEqual("MANAGED_GROUP_MISSING", raised.exception.code)
        self.assertNotIn("fixture-landing-password", str(raised.exception))

    def test_multiple_bindings_compile_all_or_return_no_generation(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        valid_policy = source_policy()
        baseline = compile_subscription(source, valid_policy)
        invalid_policy = source_policy()
        invalid_policy["managed_groups"]["Removed upstream"] = deepcopy(
            invalid_policy["managed_groups"]["AI"]
        )
        policy_snapshot = deepcopy(invalid_policy)

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, invalid_policy)

        self.assertEqual("MANAGED_GROUP_MISSING", raised.exception.code)
        self.assertEqual(policy_snapshot, invalid_policy)
        self.assertEqual(
            baseline.compiled_yaml,
            compile_subscription(source, valid_policy).compiled_yaml,
        )

    def test_exact_generated_name_collision_fails_without_prefix_stripping(self):
        source = (FIXTURES / "name_collision.yaml").read_text(encoding="utf-8")

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, source_policy())

        self.assertEqual("GENERATED_NAME_COLLISION", raised.exception.code)
        self.assertNotIn("192.0.2.32", str(raised.exception))

    def test_malformed_yaml_returns_location_without_source_or_secrets(self):
        source = (FIXTURES / "malformed.yaml").read_text(encoding="utf-8")

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, source_policy())

        error = raised.exception
        self.assertEqual("YAML_INVALID", error.code)
        self.assertIsInstance(error.line, int)
        self.assertIsInstance(error.column, int)
        self.assertNotIn("fixture-do-not-leak", str(error))
        self.assertNotIn("unterminated", str(error))

    # Keep this last: provoking RecursionError disables stdlib trace collection
    # for subsequent tests in the same process.
    def test_zz_excessive_yaml_nesting_is_reported_as_safe_invalid_input(self):
        source = (
            "x-nested: "
            + ("[" * 2000)
            + "0"
            + ("]" * 2000)
            + "\nproxy-groups: []\n"
        )

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, source_policy())

        self.assertEqual("YAML_INVALID", raised.exception.code)

    def test_duplicate_yaml_mapping_key_is_rejected(self):
        source = (FIXTURES / "duplicate_key.yaml").read_text(encoding="utf-8")

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, source_policy())

        self.assertEqual("YAML_DUPLICATE_KEY", raised.exception.code)
        self.assertEqual(2, raised.exception.line)

    def test_unsupported_custom_yaml_tag_is_safely_rejected(self):
        source = (FIXTURES / "custom_tag.yaml").read_text(encoding="utf-8")

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, source_policy())

        self.assertEqual("YAML_INVALID", raised.exception.code)
        self.assertNotIn("fixture-do-not-leak", str(raised.exception))
        self.assertNotIn("fixture-secret", str(raised.exception))

    def test_proxy_and_group_names_share_one_unique_runtime_namespace(self):
        source = (FIXTURES / "duplicate_runtime_name.yaml").read_text(encoding="utf-8")

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, source_policy())

        self.assertEqual("DUPLICATE_RUNTIME_NAME", raised.exception.code)

    def test_node_only_subscription_is_explicitly_out_of_scope(self):
        source = (FIXTURES / "node_only.yaml").read_text(encoding="utf-8")

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, source_policy())

        self.assertEqual("FULL_CONFIG_REQUIRED", raised.exception.code)

    def test_empty_source_is_rejected_before_parsing(self):
        with self.assertRaises(CompileError) as raised:
            compile_subscription("  \n", source_policy())

        self.assertEqual("SOURCE_EMPTY", raised.exception.code)

    def test_source_larger_than_eight_mib_is_rejected_before_parsing(self):
        source = "#" + ("x" * (8 * 1024 * 1024))

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, source_policy())

        self.assertEqual("SOURCE_TOO_LARGE", raised.exception.code)

    def test_source_boundary_errors_have_stable_safe_codes(self):
        cases = {
            "non-text": (None, "SOURCE_NOT_MAPPING"),
            "scalar root": ("true\n", "SOURCE_NOT_MAPPING"),
            "missing groups": ("proxies: []\n", "FULL_CONFIG_REQUIRED"),
            "invalid unicode": ("\ud800", "YAML_INVALID"),
            "unhashable mapping key": (
                "? [a, b]\n: value\nproxy-groups: []\n",
                "YAML_INVALID",
            ),
        }

        for label, (source, expected_code) in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(CompileError) as raised:
                    compile_subscription(source, source_policy())
                self.assertEqual(expected_code, raised.exception.code)

    def test_malformed_source_objects_never_escape_internal_errors(self):
        cases = {
            "providers list": """
proxy-providers: []
proxies: []
proxy-groups: [{name: AI, type: select, proxies: []}]
""",
            "empty provider name": """
proxy-providers: {'': {type: http}}
proxies: []
proxy-groups: [{name: AI, type: select, proxies: []}]
""",
            "provider scalar": """
proxy-providers: {airport: broken}
proxies: []
proxy-groups: [{name: AI, type: select, proxies: []}]
""",
            "proxy scalar": """
proxies: [broken]
proxy-groups: [{name: AI, type: select, proxies: []}]
""",
            "proxy missing name": """
proxies: [{type: socks5}]
proxy-groups: [{name: AI, type: select, proxies: []}]
""",
            "proxy missing type": """
proxies: [{name: HK-01}]
proxy-groups: [{name: AI, type: select, proxies: []}]
""",
            "dialer wrong type": """
proxies: [{name: HK-01, type: socks5, dialer-proxy: 7}]
proxy-groups: [{name: AI, type: select, proxies: [HK-01]}]
""",
            "dialer missing reference": """
proxies: [{name: HK-01, type: socks5, dialer-proxy: Removed}]
proxy-groups: [{name: AI, type: select, proxies: [HK-01]}]
""",
            "group scalar": """
proxies: []
proxy-groups: [broken]
""",
            "group missing name": """
proxies: []
proxy-groups: [{type: select, proxies: []}]
""",
            "group missing type": """
proxies: []
proxy-groups: [{name: AI, proxies: []}]
""",
            "group proxies wrong type": """
proxies: []
proxy-groups: [{name: AI, type: select, proxies: broken}]
""",
            "group use item wrong type": """
proxies: []
proxy-groups: [{name: AI, type: select, use: [7]}]
""",
            "group hidden wrong type": """
proxies: []
proxy-groups: [{name: AI, type: select, proxies: [], hidden: yes-please}]
""",
            "group filter wrong type": """
proxies: []
proxy-groups: [{name: AI, type: select, proxies: [], filter: [US]}]
""",
        }

        for label, source in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(CompileError) as raised:
                    compile_subscription(source, source_policy())
                self.assertEqual("SOURCE_SCHEMA_INVALID", raised.exception.code)

    def test_wrong_source_field_shapes_return_a_safe_schema_error(self):
        source = (FIXTURES / "invalid_schema.yaml").read_text(encoding="utf-8")

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, source_policy())

        self.assertEqual("SOURCE_SCHEMA_INVALID", raised.exception.code)

    def test_source_include_flags_require_real_booleans(self):
        source = (FIXTURES / "include_all.yaml").read_text(encoding="utf-8")
        source = source.replace(
            "include-all-proxies: true",
            "include-all-proxies: not-a-bool",
        )

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, source_policy())

        self.assertEqual("SOURCE_SCHEMA_INVALID", raised.exception.code)

    def test_stale_explicit_reference_makes_the_upstream_update_invalid(self):
        source = (FIXTURES / "stale_source_reference.yaml").read_text(encoding="utf-8")

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, source_policy())

        self.assertEqual("SOURCE_SCHEMA_INVALID", raised.exception.code)

    def test_stale_source_provider_reference_makes_upstream_invalid(self):
        source = (FIXTURES / "provider_flow.yaml").read_text(encoding="utf-8")
        source = source.replace("use: [airport]", "use: [missing-provider]")

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, source_policy())

        self.assertEqual("SOURCE_SCHEMA_INVALID", raised.exception.code)

    def test_invalid_landing_port_is_rejected_before_output(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        policy["managed_groups"]["AI"]["landing"]["port"] = 70000

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, policy)

        self.assertEqual("POLICY_INVALID", raised.exception.code)
        self.assertNotIn("landing.example.invalid", str(raised.exception))

    def test_policy_requires_at_least_one_managed_group(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, {"version": 1, "managed_groups": {}})

        self.assertEqual("POLICY_INVALID", raised.exception.code)

    def test_unknown_policy_fields_are_rejected_instead_of_silently_ignored(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        policy["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "intervl": 30,
        }

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, policy)

        self.assertEqual("POLICY_INVALID", raised.exception.code)

    def test_automatic_include_flags_require_real_booleans(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        policy["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "include-all-proxies": "yes",
        }

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, policy)

        self.assertEqual("POLICY_INVALID", raised.exception.code)

    def test_malformed_policy_values_never_escape_internal_type_errors(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policies = []
        bad_type = source_policy()
        bad_type["managed_groups"]["AI"]["landing"]["type"] = ["socks5"]
        policies.append(bad_type)
        bad_mode = source_policy()
        bad_mode["managed_groups"]["AI"]["front_hop"]["mode"] = ["source"]
        policies.append(bad_mode)

        for index, policy in enumerate(policies):
            with self.subTest(case=index):
                with self.assertRaises(CompileError) as raised:
                    compile_subscription(source, policy)
                self.assertEqual("POLICY_INVALID", raised.exception.code)

    def test_policy_schema_rejects_every_invalid_public_field_shape(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policies = []

        policies.append(("not a mapping", None))
        bool_version = source_policy()
        bool_version["version"] = True
        policies.append(("boolean version", bool_version))
        extra_root = source_policy()
        extra_root["unknown"] = True
        policies.append(("unknown root field", extra_root))
        policies.append(("managed groups list", {"version": 1, "managed_groups": []}))
        policies.append(
            (
                "non-string group",
                {"version": 1, "managed_groups": {7: source_policy()["managed_groups"]["AI"]}},
            )
        )
        policies.append(("binding scalar", {"version": 1, "managed_groups": {"AI": "bad"}}))

        binding_extra = source_policy()
        binding_extra["managed_groups"]["AI"]["unknown"] = True
        policies.append(("unknown binding field", binding_extra))
        no_landing = source_policy()
        del no_landing["managed_groups"]["AI"]["landing"]
        policies.append(("missing landing", no_landing))
        landing_extra = source_policy()
        landing_extra["managed_groups"]["AI"]["landing"]["tls"] = True
        policies.append(("unknown landing field", landing_extra))
        unsupported_type = source_policy()
        unsupported_type["managed_groups"]["AI"]["landing"]["type"] = "https"
        policies.append(("unsupported landing type", unsupported_type))
        blank_server = source_policy()
        blank_server["managed_groups"]["AI"]["landing"]["server"] = "   "
        policies.append(("blank server", blank_server))
        numeric_username = source_policy()
        numeric_username["managed_groups"]["AI"]["landing"]["username"] = 7
        policies.append(("numeric username", numeric_username))
        string_udp = source_policy()
        string_udp["managed_groups"]["AI"]["landing"]["udp"] = "yes"
        policies.append(("string udp", string_udp))
        front_scalar = source_policy()
        front_scalar["managed_groups"]["AI"]["front_hop"] = "source"
        policies.append(("front scalar", front_scalar))
        unknown_mode = source_policy()
        unknown_mode["managed_groups"]["AI"]["front_hop"] = {"mode": "random"}
        policies.append(("unknown mode", unknown_mode))
        fixed_missing = source_policy()
        fixed_missing["managed_groups"]["AI"]["front_hop"] = {"mode": "fixed"}
        policies.append(("fixed missing proxy", fixed_missing))
        automatic_url = source_policy()
        automatic_url["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "url": "",
        }
        policies.append(("empty automatic url", automatic_url))
        automatic_interval = source_policy()
        automatic_interval["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "interval": 0,
        }
        policies.append(("zero automatic interval", automatic_interval))
        automatic_proxies = source_policy()
        automatic_proxies["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "proxies": "HK-01",
        }
        policies.append(("automatic proxies scalar", automatic_proxies))
        automatic_use = source_policy()
        automatic_use["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "use": [7],
        }
        policies.append(("automatic provider wrong type", automatic_use))
        automatic_filter = source_policy()
        automatic_filter["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "filter": 7,
        }
        policies.append(("automatic filter wrong type", automatic_filter))

        for label, policy in policies:
            with self.subTest(case=label):
                with self.assertRaises(CompileError) as raised:
                    compile_subscription(source, policy)
                self.assertEqual("POLICY_INVALID", raised.exception.code)

    def test_flow_style_provider_use_and_yaml_aliases_keep_their_semantics(self):
        source = (FIXTURES / "provider_flow.yaml").read_text(encoding="utf-8")
        original = yaml.safe_load(source)

        result = compile_subscription(source, source_policy())
        compiled = yaml.safe_load(result.compiled_yaml)
        groups = {group["name"]: group for group in compiled["proxy-groups"]}
        landing = next(
            proxy
            for proxy in compiled["proxies"]
            if proxy["name"] == groups["AI"]["proxies"][0]
        )
        front = groups[landing["dialer-proxy"]]

        self.assertEqual(["airport"], front["use"])
        self.assertEqual(["HK-01"], front["proxies"])
        self.assertEqual("(?i)us|美国", front["filter"])
        self.assertEqual(original["proxy-providers"], compiled["proxy-providers"])
        self.assertEqual(original["x-fixture-health"], compiled["x-fixture-health"])

    def test_provider_only_source_gets_a_landing_without_inventing_upstream_nodes(self):
        source = (FIXTURES / "provider_only.yaml").read_text(encoding="utf-8")

        result = compile_subscription(source, source_policy())
        compiled = yaml.safe_load(result.compiled_yaml)
        front_name = result.report["managed_groups"][0]["front"]
        front = next(group for group in compiled["proxy-groups"] if group["name"] == front_name)

        self.assertEqual(["airport"], front["use"])
        self.assertNotIn("proxies", front)
        self.assertEqual(1, len(compiled["proxies"]))
        self.assertEqual(
            result.report["managed_groups"][0]["landing"],
            compiled["proxies"][0]["name"],
        )

    def test_yaml_merge_anchor_is_resolved_by_the_strict_safe_loader(self):
        source = (FIXTURES / "merge_anchor.yaml").read_text(encoding="utf-8")

        result = compile_subscription(source, source_policy())
        compiled = yaml.safe_load(result.compiled_yaml)
        upstream = next(proxy for proxy in compiled["proxies"] if proxy["name"] == "HK-01")

        self.assertEqual("socks5", upstream["type"])
        self.assertEqual("192.0.2.140", upstream["server"])
        self.assertEqual(1080, upstream["port"])

    def test_source_front_mode_preserves_fallback_health_policy(self):
        source = (FIXTURES / "nested_groups.yaml").read_text(encoding="utf-8")
        binding = source_policy()["managed_groups"]["AI"]
        policy = {"version": 1, "managed_groups": {"Region B": binding}}

        result = compile_subscription(source, policy)
        compiled = yaml.safe_load(result.compiled_yaml)
        groups = {group["name"]: group for group in compiled["proxy-groups"]}
        front = groups[result.report["managed_groups"][0]["front"]]

        self.assertEqual("fallback", front["type"])
        self.assertEqual(["US-01"], front["proxies"])
        self.assertEqual("https://www.gstatic.com/generate_204", front["url"])
        self.assertEqual(300, front["interval"])

    def test_fixed_front_hop_contains_only_the_requested_upstream(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        policy["managed_groups"]["AI"]["front_hop"] = {
            "mode": "fixed",
            "proxy": "US-01",
        }

        result = compile_subscription(source, policy)
        compiled = yaml.safe_load(result.compiled_yaml)
        groups = {group["name"]: group for group in compiled["proxy-groups"]}
        proxies = {proxy["name"]: proxy for proxy in compiled["proxies"]}
        landing = proxies[groups["AI"]["proxies"][0]]
        front = groups[landing["dialer-proxy"]]

        self.assertEqual("select", front["type"])
        self.assertEqual(["US-01"], front["proxies"])
        self.assertNotIn("use", front)
        self.assertEqual("fixed", result.report["managed_groups"][0]["front_mode"])

    def test_stale_fixed_front_hop_rejects_the_entire_compilation(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        policy["managed_groups"]["AI"]["front_hop"] = {
            "mode": "fixed",
            "proxy": "Removed upstream",
        }

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, policy)

        self.assertEqual("FRONT_REFERENCE_MISSING", raised.exception.code)

    def test_automatic_front_hop_uses_source_candidates_without_bypass(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        policy["managed_groups"]["AI"]["front_hop"] = {"mode": "automatic"}

        result = compile_subscription(source, policy)
        compiled = yaml.safe_load(result.compiled_yaml)
        groups = {group["name"]: group for group in compiled["proxy-groups"]}
        proxies = {proxy["name"]: proxy for proxy in compiled["proxies"]}
        landing = proxies[groups["AI"]["proxies"][0]]
        front = groups[landing["dialer-proxy"]]

        self.assertEqual("url-test", front["type"])
        self.assertEqual(["HK-01", "US-01"], front["proxies"])
        self.assertEqual("https://www.gstatic.com/generate_204", front["url"])
        self.assertEqual(300, front["interval"])
        self.assertEqual("REJECT", front["empty-fallback"])
        self.assertEqual(1, len(groups["AI"]["proxies"]))

    def test_automatic_front_hop_rejects_stale_explicit_candidates(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        policy["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "proxies": ["Removed upstream"],
        }

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, policy)

        self.assertEqual("FRONT_REFERENCE_MISSING", raised.exception.code)

    def test_automatic_front_hop_rejects_stale_provider_reference(self):
        source = (FIXTURES / "provider_flow.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        policy["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "use": ["removed-provider"],
        }

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, policy)

        self.assertEqual("FRONT_REFERENCE_MISSING", raised.exception.code)

    def test_automatic_front_hop_preserves_provider_use(self):
        source = (FIXTURES / "provider_flow.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        policy["managed_groups"]["AI"]["front_hop"] = {"mode": "automatic"}

        result = compile_subscription(source, policy)
        compiled = yaml.safe_load(result.compiled_yaml)
        groups = {group["name"]: group for group in compiled["proxy-groups"]}
        front_name = result.report["managed_groups"][0]["front"]

        self.assertEqual("url-test", groups[front_name]["type"])
        self.assertEqual(["airport"], groups[front_name]["use"])
        self.assertEqual("(?i)us|美国", groups[front_name]["filter"])

    def test_automatic_front_hop_supports_custom_and_empty_candidate_sets(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        custom_policy = source_policy()
        custom_policy["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "proxies": ["US-01"],
            "url": "https://probe.example.invalid/generate_204",
            "interval": 42,
        }

        custom = compile_subscription(source, custom_policy)
        custom_config = yaml.safe_load(custom.compiled_yaml)
        custom_report = custom.report["managed_groups"][0]
        custom_front = next(
            group
            for group in custom_config["proxy-groups"]
            if group["name"] == custom_report["front"]
        )
        self.assertEqual(["US-01"], custom_front["proxies"])
        self.assertEqual("https://probe.example.invalid/generate_204", custom_front["url"])
        self.assertEqual(42, custom_front["interval"])

        empty_policy = source_policy()
        empty_policy["managed_groups"]["AI"]["front_hop"] = {
            "mode": "automatic",
            "proxies": [],
        }
        empty = compile_subscription(source, empty_policy)
        empty_config = yaml.safe_load(empty.compiled_yaml)
        empty_report = empty.report["managed_groups"][0]
        empty_front = next(
            group
            for group in empty_config["proxy-groups"]
            if group["name"] == empty_report["front"]
        )
        self.assertEqual([], empty_front["proxies"])
        self.assertEqual("REJECT", empty_front["empty-fallback"])

    def test_direct_front_mode_keeps_the_landing_final_without_a_front_group(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        policy["managed_groups"]["AI"]["front_hop"] = {"mode": "direct"}

        result = compile_subscription(source, policy)
        compiled = yaml.safe_load(result.compiled_yaml)
        groups = {group["name"]: group for group in compiled["proxy-groups"]}
        proxies = {proxy["name"]: proxy for proxy in compiled["proxies"]}
        landing = proxies[groups["AI"]["proxies"][0]]

        self.assertNotIn("dialer-proxy", landing)
        self.assertFalse(any(name.endswith("·front") for name in groups))
        self.assertIsNone(result.report["managed_groups"][0]["front"])

    def test_http_landing_uses_the_same_fail_closed_wrapper(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        policy["managed_groups"]["AI"]["landing"]["type"] = "http"
        policy["managed_groups"]["AI"]["landing"]["port"] = 8080

        result = compile_subscription(source, policy)
        compiled = yaml.safe_load(result.compiled_yaml)
        groups = {group["name"]: group for group in compiled["proxy-groups"]}
        proxies = {proxy["name"]: proxy for proxy in compiled["proxies"]}
        landing = proxies[groups["AI"]["proxies"][0]]

        self.assertEqual("http", landing["type"])
        self.assertEqual(8080, landing["port"])
        self.assertEqual(1, len(groups["AI"]["proxies"]))

    def test_landing_server_whitespace_is_normalized_without_mutating_policy(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        policy["managed_groups"]["AI"]["landing"]["server"] = (
            "  landing.example.invalid  "
        )
        snapshot = deepcopy(policy)

        result = compile_subscription(source, policy)
        compiled = yaml.safe_load(result.compiled_yaml)
        managed = next(group for group in compiled["proxy-groups"] if group["name"] == "AI")
        landing = next(
            proxy for proxy in compiled["proxies"] if proxy["name"] == managed["proxies"][0]
        )

        self.assertEqual("landing.example.invalid", landing["server"])
        self.assertEqual(snapshot, policy)

    def test_nested_managed_groups_reference_each_others_front_layer(self):
        source = (FIXTURES / "nested_groups.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        region_binding = deepcopy(policy["managed_groups"]["AI"])
        region_binding["landing"]["server"] = "region-a-landing.example.invalid"
        policy["managed_groups"]["Region A"] = region_binding

        result = compile_subscription(source, policy)
        compiled = yaml.safe_load(result.compiled_yaml)
        groups = {group["name"]: group for group in compiled["proxy-groups"]}
        report = {item["group"]: item for item in result.report["managed_groups"]}
        ai_front = groups[report["AI"]["front"]]

        self.assertEqual(
            [report["Region A"]["front"], "Region B"],
            ai_front["proxies"],
        )
        self.assertNotIn("Region A", ai_front["proxies"])
        self.assertEqual(["AI", "DIRECT"], groups["Nested Consumer"]["proxies"])

    def test_nested_reference_to_a_direct_managed_group_is_rejected(self):
        source = (FIXTURES / "nested_groups.yaml").read_text(encoding="utf-8")
        policy = source_policy()
        region_binding = deepcopy(policy["managed_groups"]["AI"])
        region_binding["front_hop"] = {"mode": "direct"}
        policy["managed_groups"]["Region A"] = region_binding

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, policy)

        self.assertEqual("POLICY_INVALID", raised.exception.code)

    def test_explicit_proxy_group_cycle_is_rejected(self):
        source = (FIXTURES / "cycle.yaml").read_text(encoding="utf-8")

        with self.assertRaises(CompileError) as raised:
            compile_subscription(source, source_policy())

        self.assertEqual("TOPOLOGY_CYCLE", raised.exception.code)

    def test_existing_dialer_chain_is_accepted_but_mixed_cycle_is_rejected(self):
        source = """
proxies:
  - {name: Hop, type: socks5, server: 192.0.2.150, port: 1080}
  - {name: Chained, type: socks5, server: 192.0.2.151, port: 1080, dialer-proxy: Front}
proxy-groups:
  - {name: Front, type: select, proxies: [Hop]}
  - {name: AI, type: select, proxies: [Chained]}
rules: [MATCH,AI]
"""

        result = compile_subscription(source, source_policy())
        self.assertIn("EA·", result.compiled_yaml)

        cyclic = source.replace(
            "port: 1080}\n  - {name: Chained",
            "port: 1080, dialer-proxy: AI}\n  - {name: Chained",
            1,
        )
        with self.assertRaises(CompileError) as raised:
            compile_subscription(cyclic, source_policy())
        self.assertEqual("TOPOLOGY_CYCLE", raised.exception.code)

    def test_include_all_front_excludes_only_generated_landing_names(self):
        source = (FIXTURES / "include_all.yaml").read_text(encoding="utf-8")

        result = compile_subscription(source, source_policy())
        compiled = yaml.safe_load(result.compiled_yaml)
        groups = {group["name"]: group for group in compiled["proxy-groups"]}
        report = result.report["managed_groups"][0]
        front = groups[report["front"]]
        exclusion = re.compile(front["exclude-filter"])

        self.assertTrue(front["include-all-proxies"])
        self.assertIsNotNone(exclusion.search(report["landing"]))
        self.assertIsNotNone(exclusion.search("EXPIRED node"))
        self.assertIsNone(exclusion.search("EA·user-owned"))
        self.assertEqual(
            result.compiled_yaml,
            compile_subscription(result.compiled_yaml, source_policy()).compiled_yaml,
        )

        source_without_existing_filter = source.replace(
            '    exclude-filter: "(?i)expired"\n',
            "",
        )
        no_filter_result = compile_subscription(
            source_without_existing_filter,
            source_policy(),
        )
        no_filter_config = yaml.safe_load(no_filter_result.compiled_yaml)
        no_filter_report = no_filter_result.report["managed_groups"][0]
        no_filter_front = next(
            group
            for group in no_filter_config["proxy-groups"]
            if group["name"] == no_filter_report["front"]
        )
        self.assertIsNotNone(
            re.search(no_filter_front["exclude-filter"], no_filter_report["landing"])
        )

    def test_multi_binding_include_all_excludes_every_generated_landing(self):
        config = yaml.safe_load(
            (FIXTURES / "include_all.yaml").read_text(encoding="utf-8")
        )
        config["proxy-groups"].append(
            {
                "name": "Automatic",
                "type": "select",
                "include-all": True,
            }
        )
        source = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        policy = source_policy()
        automatic_binding = deepcopy(policy["managed_groups"]["AI"])
        automatic_binding["front_hop"] = {"mode": "automatic"}
        policy["managed_groups"]["Automatic"] = automatic_binding

        result = compile_subscription(source, policy)
        compiled = yaml.safe_load(result.compiled_yaml)
        groups = {group["name"]: group for group in compiled["proxy-groups"]}
        report = {item["group"]: item for item in result.report["managed_groups"]}
        landing_names = {item["landing"] for item in report.values()}

        for group_name in ("AI", "Automatic"):
            front = groups[report[group_name]["front"]]
            exclusion = re.compile(front["exclude-filter"])
            self.assertTrue(all(exclusion.search(name) for name in landing_names))
            self.assertIsNone(exclusion.search("EA·user-owned"))

    def test_source_update_adds_and_removes_front_candidates_without_losing_anchor(self):
        before_source = (FIXTURES / "update_before.yaml").read_text(encoding="utf-8")
        after_source = (FIXTURES / "update_after.yaml").read_text(encoding="utf-8")
        policy = source_policy()

        before = compile_subscription(before_source, policy)
        after = compile_subscription(after_source, policy)
        before_config = yaml.safe_load(before.compiled_yaml)
        after_config = yaml.safe_load(after.compiled_yaml)

        before_report = before.report["managed_groups"][0]
        after_report = after.report["managed_groups"][0]
        before_groups = {group["name"]: group for group in before_config["proxy-groups"]}
        after_groups = {group["name"]: group for group in after_config["proxy-groups"]}

        self.assertEqual(before_report["landing"], after_report["landing"])
        self.assertEqual([before_report["landing"]], before_groups["AI"]["proxies"])
        self.assertEqual([after_report["landing"]], after_groups["AI"]["proxies"])
        self.assertEqual(
            ["Stable-01", "Removed-01"],
            before_groups[before_report["front"]]["proxies"],
        )
        self.assertEqual(
            ["Stable-01", "Added-01"],
            after_groups[after_report["front"]]["proxies"],
        )

    def test_result_repr_and_report_never_expose_subscription_or_landing_secrets(self):
        source = (FIXTURES / "provider_flow.yaml").read_text(encoding="utf-8")

        result = compile_subscription(source, source_policy())
        diagnostic_text = repr(result) + repr(result.report)

        self.assertIn("fixture-landing-password", result.compiled_yaml)
        self.assertIn("fixture-token", result.compiled_yaml)
        self.assertNotIn("fixture-landing-password", diagnostic_text)
        self.assertNotIn("landing.example.invalid", diagnostic_text)
        self.assertNotIn("fixture-token", diagnostic_text)

    def test_report_includes_sanitized_counts_and_warnings(self):
        source = (FIXTURES / "inline.yaml").read_text(encoding="utf-8")

        result = compile_subscription(source, source_policy())

        self.assertEqual(
            {
                "managed_groups": 1,
                "generated_proxies": 1,
                "generated_groups": 1,
            },
            result.report["counts"],
        )
        self.assertEqual([], result.report["warnings"])


if __name__ == "__main__":
    unittest.main()
