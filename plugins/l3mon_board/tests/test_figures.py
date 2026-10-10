"""The small rules the Guide and the scoreboard share: a percentage rounds as the demo's JavaScript does (half goes up), and the phase banner.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
import pytest

from CTFd.plugins.l3mon_board import banner, figures
from CTFd.plugins.l3mon_core.clock import Phase


@pytest.mark.parametrize("value,expected", [(0, 0), (0.4, 0), (0.5, 1), (1.5, 2), (2.5, 3), (12.5, 13), (49.999, 50), (99.5, 100), (100, 100)])
def test_a_half_rounds_up_as_javascript_does_not_to_the_even_number_as_python_does(value, expected):
    assert figures.js_round(value) == expected and isinstance(figures.js_round(value), int)


@pytest.mark.parametrize(
    "state,frozen,expected",
    [
        ("before", False, None),
        ("before", True, None),
        ("live", False, None),
        ("live", True, "frozen"),
        ("paused", False, "paused"),
        ("paused", True, "paused"),
        ("ended", False, "wrap"),
        ("ended", True, "wrap"),
    ],
)
def test_the_banner_follows_the_phase_and_a_freeze_is_told_only_while_live(state, frozen, expected):
    found = banner.for_phase(Phase(state, frozen))
    assert (found["id"] if found else None) == expected


def test_the_banners_are_the_demos_words_in_the_contracts_key_order():
    assert list(banner.for_phase(Phase("ended", False))) == ["id", "cls", "lead", "text"]
    assert banner.for_phase(Phase("ended", False)) == {"id": "wrap", "cls": "banner-wrap", "lead": "THAT'S A WRAP.", "text": "The broadcast has ended. You can still read every programme."}
    assert banner.for_phase(Phase("paused", False)) == {"id": "paused", "cls": "banner-warn", "lead": "PAUSED.", "text": "Submissions are paused for a moment. Your progress is safe."}
    assert banner.for_phase(Phase("live", True)) == {"id": "frozen", "cls": "banner-info", "lead": "SCOREBOARD FROZEN.", "text": "Standings are frozen for the final hour. Your solves still count."}


def test_a_banner_given_out_is_a_copy_so_one_answer_cannot_change_the_next():
    first = banner.for_phase(Phase("ended", False))
    first["text"] = "changed"
    assert banner.for_phase(Phase("ended", False))["text"] != "changed"
