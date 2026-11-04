"""Bounded execution of the one shared overlay generator in the disposable guest."""

import json
from pathlib import Path
import subprocess
import sys

from guest_overlay_common import managed_script


def transform(config, action, port):
    script = managed_script("function main(config) { return config; }", port)
    source = json.dumps(config, allow_nan=False)
    expression = f"main({source}, 'P0')" if action == "apply" else f"eaP0Clean({source})"
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("guest_script_eval.py"))],
                            input=script + "\nJSON.stringify(" + expression + ")",
                            text=True, capture_output=True, timeout=4)
    if result.returncode:
        raise ValueError("shared fixture generator did not complete")
    return json.loads(result.stdout)
