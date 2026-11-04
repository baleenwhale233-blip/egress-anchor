import json
import shutil
import subprocess
import unittest

from guest_overlay_common import managed_script


@unittest.skipUnless(shutil.which("node"), "headless JS unit checks need Node; native VM checks remain required")
class RegistrationGuardTests(unittest.TestCase):
    def evaluate(self, stage, config, user="function main(c) { return c; }"):
        script = managed_script(user, 1080, stage=stage)
        script += "\nconsole.log(JSON.stringify(main(" + json.dumps(config) + ", 'test')));"
        result = subprocess.run(["node", "-e", script], text=True, capture_output=True, timeout=3, check=True)
        return json.loads(result.stdout)

    def config(self, registered=False):
        return {"proxies": [{"name": "airport"}], "proxy-groups": [
            {"name": "EA-P0-Deny", "type": "select", "proxies": ["REJECT"]}] if registered else [],
            "rules": (["DOMAIN,managed.p0.invalid,EA-P0-Deny"] if registered else []) + ["MATCH,DIRECT"]}

    def test_unregistered_profile_is_denied_before_unbounded_user_code(self):
        result = self.evaluate("global", self.config(), "function main(c) { while(true){} }")
        self.assertEqual("unregistered", result["x-ea-p0-state"])
        self.assertEqual("DOMAIN,managed.p0.invalid,EA-P0-Deny", result["rules"][0])
        self.assertFalse(any(p["name"] == "EA-P0-Landing" for p in result["proxies"]))

    def test_registered_profile_keeps_user_rules_and_runs_final_override(self):
        result = self.evaluate("global", self.config(True),
                               "function main(c) { c.rules.unshift('DOMAIN,user.invalid,DIRECT'); return c; }")
        result = self.evaluate("profile", result,
                               "function main(c) { c.rules.unshift('DOMAIN,managed.p0.invalid,DIRECT'); return c; }")
        self.assertEqual("active", result["x-ea-p0-state"])
        self.assertEqual("DOMAIN,managed.p0.invalid,EA-P0-Managed", result["rules"][1])
        self.assertIn("DOMAIN,user.invalid,DIRECT", result["rules"])

    def test_profile_script_cannot_reactivate_a_failed_global_hook(self):
        failed = self.evaluate("global", self.config(True), "function main(c) { throw Error('fixture'); }")
        result = self.evaluate("profile", failed)
        self.assertEqual("script-error", result["x-ea-p0-state"])
        self.assertFalse(any(p["name"] == "EA-P0-Landing" for p in result["proxies"]))

    def test_standalone_hot_activation_can_activate_a_registered_native_guard(self):
        blocked = self.evaluate("global", self.config())
        result = self.evaluate("standalone", blocked)
        self.assertEqual("active", result["x-ea-p0-state"])


if __name__ == "__main__":
    unittest.main()
