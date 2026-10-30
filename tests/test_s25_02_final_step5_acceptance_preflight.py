"""Step 5 CLI preflight: default profile must be tested without M5 opt-in overrides."""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("key,value", [
    ("ROUTE_ADMISSION_MODE", "guarded"),
    ("UNDERSTANDING_STRATEGY", "structured"),
    ("M5_UNIFIED_ROUTE_ENABLED", "true"),
    ("M5_FIRST_ROUTE_ENABLED", "false"),
    ("ROUTING_PROFILE", "legacy"),
    ("ROUTING_PROFILE", "unified"),
])
def test_auto_default_acceptance_rejects_explicit_routing_override(
        tmp_path, key, value):
    env = os.environ.copy()
    for name in ("ROUTE_ADMISSION_MODE", "UNDERSTANDING_STRATEGY",
                 "M5_UNIFIED_ROUTE_ENABLED", "M5_FIRST_ROUTE_ENABLED",
                 "ROUTING_PROFILE"):
        env.pop(name, None)
    env["RUN_S25_02_M5_UNIFIED_LIVE"] = "1"
    env[key] = value
    result = subprocess.run([
        sys.executable, "-m", "evals.s25_02_m5_unified_live",
        "--require-default-profile", "--endpoint", "http://127.0.0.1:1",
        "--output", str(tmp_path / "should-not-exist.json"),
        "--timeout", "1",
    ], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    assert ("must be unset" in result.stderr
            or "must be auto or unset" in result.stderr)
    assert not (tmp_path / "should-not-exist.json").exists()
