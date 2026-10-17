"""Safety rail and configuration tests for the k6 load testing suite."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import test_stack as t

pytestmark = [pytest.mark.integration]

LOAD_DIR = t.ROOT / "tests" / "load"
RUN_SCRIPT = LOAD_DIR / "run.sh"
CLEANUP_SCRIPT = LOAD_DIR / "cleanup.py"
SETUP_SCRIPT = LOAD_DIR / "setup.py"
ALLOWLIST_FILE = LOAD_DIR / "allowlist.json"


def test_allowlist_json_exists_and_contains_expected_hosts():
    assert ALLOWLIST_FILE.is_file(), "allowlist.json must exist in tests/load/"
    data = json.loads(ALLOWLIST_FILE.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert "localhost" in data
    assert "127.0.0.1" in data
    assert "ctfd:8000" in data
    assert "play.l3monctf.xyz" in data


def test_k6_runner_refuses_unauthorized_target():
    """run.sh must exit non-zero and refuse targets not in the allow-list."""
    bad_targets = [
        "http://evil.com",
        "http://8.8.8.8",
        "https://google.com",
        "http://unauthorized.internal:9000",
    ]
    for target in bad_targets:
        res = subprocess.run(
            [t.BASH, str(RUN_SCRIPT), f"--target={target}"],
            capture_output=True,
            text=True,
            cwd=t.ROOT,
        )
        assert res.returncode == 2, f"Target {target} should have been refused with exit code 2"
        assert "[SAFETY RAIL REFUSAL]" in res.stderr
        assert "NOT on the allow-list" in res.stderr


def test_cleanup_script_refuses_unauthorized_target():
    """cleanup.py must refuse targets not in the allow-list."""
    res = subprocess.run(
        [sys.executable, str(CLEANUP_SCRIPT), "--base", "http://attacker.example.com"],
        capture_output=True,
        text=True,
        cwd=t.ROOT,
    )
    assert res.returncode != 0
    assert "[SAFETY RAIL REFUSAL]" in res.stderr or "[SAFETY RAIL REFUSAL]" in res.stdout


def test_setup_script_refuses_unauthorized_target():
    """setup.py must refuse targets not in the allow-list."""
    res = subprocess.run(
        [sys.executable, str(SETUP_SCRIPT), "--base", "http://unauthorized.target.com"],
        capture_output=True,
        text=True,
        cwd=t.ROOT,
    )
    assert res.returncode != 0
    assert "[SAFETY RAIL REFUSAL]" in res.stderr or "[SAFETY RAIL REFUSAL]" in res.stdout


def test_k6_scenarios_syntax_and_safety():
    """Verify that scenarios.js imports safety rails and exports required scenarios."""
    scenarios_content = (LOAD_DIR / "scenarios.js").read_text(encoding="utf-8")
    assert "validateTarget(TARGET)" in scenarios_content
    assert "validateAccountPrefix" in scenarios_content
    assert "export function registrationBurst" in scenarios_content
    assert "export function tickPolling" in scenarios_content
    assert "export function flagSubmissions" in scenarios_content
    assert "export function scoreboardReads" in scenarios_content
    assert "export function handleSummary" in scenarios_content


def test_k6_script_execution_against_local_stack():
    """Run a brief local smoke execution using the k6 container."""
    res = subprocess.run(
        [t.BASH, str(RUN_SCRIPT), "--target=http://localhost:8080", "--profile=laptop"],
        capture_output=True,
        text=True,
        cwd=t.ROOT,
        timeout=60,
    )
    assert res.returncode == 0, f"k6 load test failed: {res.stdout}\n{res.stderr}"
    assert "L3m0nCTF 2026 k6 Load Test Summary" in res.stdout
