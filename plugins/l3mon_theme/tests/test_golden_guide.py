"""Golden tests for the Guide view-model builder (SP6 Stream C).

Verifies that the Python port in `plugins/l3mon_theme/pages/guide.py:build_guide_view` produces
the exact dictionary expected by the prototype contracts for all canonical scenarios.
"""
import json
import os

from CTFd.plugins.l3mon_theme.pages.guide import build_guide_view


def test_golden_guide_scenarios():
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "golden_guide.json")
    with open(fixture_path, "r", encoding="utf-8") as f:
        scenarios = json.load(f)

    assert len(scenarios) >= 5, "at least 5 golden scenarios are defined"

    for name, item in scenarios.items():
        inp = item["input"]
        expected = item["expected"]
        actual = build_guide_view(inp["data"], inp["me"], inp["epg_html"])
        assert actual == expected, f"Scenario '{name}' failed view-model comparison:\nActual: {actual}\nExpected: {expected}"
