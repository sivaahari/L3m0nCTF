"""The one switch the organisers can flip that CTFd does not already have: the "coming up" count (on).

The scoreboard freeze is deliberately NOT a second switch: CTFd freezes its own standings whenever its `freeze` setting holds a
time, so a separate flag could disagree with CTFd's own scoreboard. The freeze is that time (see clock.py): unset means no
freeze, which is the default until the core decides.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import pytest

from CTFd.plugins.l3mon_core import settings
from CTFd.plugins.l3mon_core.settings import AIR_TARGET_KEY, METER_KEY, SHOW_COMING_KEY, show_coming_count, story_air_target, story_meter
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd


def test_the_default_is_coming_count_on():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        assert show_coming_count() is True
    destroy_ctfd(app)


@pytest.mark.parametrize("spelling", ["1", "true", "True", "TRUE", "yes", "Yes", "on", "ON", " true "])
def test_every_accepted_spelling_switches_it_on(spelling):
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        set_config(SHOW_COMING_KEY, "false")
        set_config(SHOW_COMING_KEY, spelling)
        assert show_coming_count() is True
    destroy_ctfd(app)


@pytest.mark.parametrize("spelling", ["0", "false", "False", "no", "off", "maybe", "2", "enabled"])
def test_a_value_that_is_set_but_is_not_an_on_spelling_is_off_even_where_the_default_is_on(spelling):
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        set_config(SHOW_COMING_KEY, spelling)
        assert show_coming_count() is False
    destroy_ctfd(app)


def test_an_empty_value_is_the_same_as_not_set_because_ctfd_stores_nothing_for_it():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        set_config(SHOW_COMING_KEY, "")
        assert show_coming_count() is True
    destroy_ctfd(app)


def test_a_change_shows_at_once_because_ctfds_own_config_cache_is_cleared():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        assert show_coming_count() is True
        set_config(SHOW_COMING_KEY, "false")
        assert show_coming_count() is False
        set_config(SHOW_COMING_KEY, "true")
        assert show_coming_count() is True
    destroy_ctfd(app)


def test_the_key_is_the_documented_one_and_there_is_no_second_freeze_switch():
    assert SHOW_COMING_KEY == "l3mon_show_coming_count"
    assert not hasattr(settings, "freeze_enabled") and not hasattr(settings, "FREEZE_KEY"), "the freeze is CTFd's own `freeze` time, nothing else"


def test_the_story_meter_is_on_unless_switched_off_and_follows_the_same_spellings():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        assert story_meter() is True
        for off in ("0", "false", "no", "off", "maybe"):
            set_config(METER_KEY, off)
            assert story_meter() is False, off
        for on in ("1", "true", "yes", "on", " ON "):
            set_config(METER_KEY, on)
            assert story_meter() is True, on
    destroy_ctfd(app)


def test_the_air_target_is_a_whole_number_of_solves_and_0_when_unset_or_unusable():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        assert story_air_target() == 0
        for value, want in (("5500", 5500), (" 40 ", 40), (120, 120), ("0", 0), ("-5", 0), ("abc", 0), ("1.5", 0), ("", 0), ("99999999999", 0)):
            set_config(AIR_TARGET_KEY, value)
            assert story_air_target() == want, value
    destroy_ctfd(app)

