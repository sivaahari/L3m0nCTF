"""The plain-text rules shared by the plugins, and the lock they share.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import pytest

from CTFd.plugins.l3mon_core import locks, text


def test_the_strict_rule_is_the_one_release_control_uses():
    from CTFd.plugins.l3mon_release import plan

    assert plan.UNSAFE_TEXT is text.UNSAFE_TEXT  # one rule, moved, not copied
    assert plan.plain_text is text.plain_text
    assert text.plain_text("Hello [there]_ *you* {x} <b> // `a` \\ # | ~") == "Hello there you x b  a    "
    assert text.plain_text("ordinary words: it's fine, really (1/2)") == "ordinary words: it's fine, really (1/2)"


@pytest.mark.parametrize(
    "value",
    ["Found a bug in the login page", "Great catch: thanks!", "it's under_score and snake_case", "50/50", "naïve café 日本語", "a" * 200, "(brackets) are fine", "Q&A"],
)
def test_crew_text_accepts_ordinary_sentences(value):
    assert text.crew_text(value, 200) == (value.strip(), None)


@pytest.mark.parametrize(
    "value, why",
    [
        ("", "may not be empty"),
        ("   ", "may not be empty"),
        ("<script>alert(1)</script>", "may not contain < or >"),
        ("a < b", "may not contain < or >"),
        ("tab\there", "control characters"),
        ("line\nbreak", "control characters"),
        ("nul\x00byte", "control characters"),
        ("a" * 201, "at most 200"),
    ],
)
def test_crew_text_refuses_what_could_become_markup_or_is_not_a_sentence(value, why):
    clean, problem = text.crew_text(value, 200)
    assert clean is None
    assert why in problem


def test_crew_text_refuses_what_is_not_text_and_can_allow_empty():
    assert text.crew_text(None, 200)[1] == "must be text"
    assert text.crew_text(5, 200)[1] == "must be text"
    assert text.crew_text(True, 200)[1] == "must be text"
    assert text.crew_text("", 200, required=False) == ("", None)
    assert text.crew_text(None, 200, required=False) == ("", None)
    assert text.crew_text("  ok  ", 200) == ("ok", None)  # trimmed
    assert text.crew_text("x" * 200 + "  ", 200) == ("x" * 200, None)  # the limit counts the trimmed text


def test_the_release_lock_is_the_shared_lock(monkeypatch):
    from CTFd.plugins.l3mon_release import reconcile

    called = []
    monkeypatch.setattr(locks, "serialize", lambda: called.append("locks"))
    reconcile.serialize()
    assert called == ["locks"]  # the old name still works and does the shared thing
