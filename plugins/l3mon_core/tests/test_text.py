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


@pytest.mark.parametrize(
    "char",
    ["\xad", "\u061c", "\u2060", "\u2064", "\U000e0020", "\U000e007f", "\x85", "\x80", "\x9f", "\u2028", "\u2029", "\u202e", "\u202a", "\u2066", "\u2069", "\u200e", "\u200f", "\u200b", "\ufeff", "\x7f"],
    ids=lambda c: f"U+{ord(c):04X}",
)
def test_crew_text_refuses_invisible_and_direction_changing_characters(char):
    """They can reorder or hide what a crew page or a studio's note shows (found by the independent audit)."""
    clean, problem = text.crew_text(f"fine {char} words", 200)
    assert clean is None and "control characters" in problem


@pytest.mark.parametrize(
    "sentence",
    ["family 👨\u200d👩\u200d👧 solved it", "ക്\u200dഷ (Malayalam, with a joiner)", "ರ್\u200cಕ (Kannada, with a non-joiner)", "café naïve", "இது தமிழ்"],
)
def test_crew_text_keeps_the_joiners_that_real_scripts_and_emoji_need(sentence):
    assert text.crew_text(sentence, 200) == (sentence, None)


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


def test_the_plan_lock_has_a_row_to_lock_even_before_any_channel_exists():
    """With no channel, `SELECT ... FOR UPDATE` on an empty table takes only a gap lock, and gap locks do not block each other: eight
    simultaneous bonuses all got through (found by the independent audit on MariaDB). The lock falls back to a settings row that is
    created once and always exists."""
    from CTFd.models import Configs, db
    from CTFd.plugins.l3mon_core.models import Channel
    from tests.helpers import create_ctfd, destroy_ctfd

    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        Configs.query.filter_by(key=locks.LOCK_KEY).delete()
        db.session.commit()
        assert Configs.query.filter_by(key=locks.LOCK_KEY).count() == 0
        locks.serialize()
        assert Configs.query.filter_by(key=locks.LOCK_KEY).count() == 1
        locks.serialize()
        locks.serialize()
        assert Configs.query.filter_by(key=locks.LOCK_KEY).count() == 1, "created once"
        db.session.add(Channel(slug="street", name="Street", position=1))
        db.session.commit()
        locks.serialize()  # with a channel the channel is the lock
        assert Configs.query.filter_by(key=locks.LOCK_KEY).count() == 1
    destroy_ctfd(app)
