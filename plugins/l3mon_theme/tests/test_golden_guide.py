"""Golden tests for the Guide view-model builder (SP6 Stream C).

Verifies that the Python port in `plugins/l3mon_theme/pages/guide.py:build_guide_view` produces
the exact dictionary expected by the prototype contracts for all 16 canonical scenarios.
"""
import json
import os

from CTFd.plugins.l3mon_theme.pages.guide import build_guide_view


def test_golden_guide_scenarios():
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "golden_guide.json")
    with open(fixture_path, "r", encoding="utf-8") as f:
        scenarios = json.load(f)

    assert len(scenarios) == 16, "exactly 16 golden guide scenarios are tested"

    for name, item in scenarios.items():
        inp = item["input"]
        expected = item["expected"]
        actual = build_guide_view(inp["data"], inp["me"], inp["epg_html"])
        assert actual == expected, f"Scenario '{name}' failed view-model comparison:\nActual: {actual}\nExpected: {expected}"


def test_break_on_purpose_standard_round_fails_on_halves(monkeypatch):
    """Proves that Python's standard round() fails while js_round() passes on 1/8 and 5/8."""
    from CTFd.plugins.l3mon_theme.pages import guide

    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "golden_guide.json")
    with open(fixture_path, "r", encoding="utf-8") as f:
        scenarios = json.load(f)

    # Monkeypatch js_round to standard round (half to even)
    monkeypatch.setattr(guide, "js_round", round)

    failed = []
    for name in ("live_half_1_of_8", "live_half_5_of_8"):
        item = scenarios[name]
        inp = item["input"]
        expected = item["expected"]
        actual = guide.build_guide_view(inp["data"], inp["me"], inp["epg_html"])
        if actual != expected:
            failed.append(name)

    assert failed == ["live_half_1_of_8", "live_half_5_of_8"], "Standard round must fail on half-rounding scenarios"
