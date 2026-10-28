"""`claim(key, seconds)`: one worker at a time, and a flag that lost its lifetime does not hold the job off for ever (found by the audit of 3.7).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core/test_claim.py
"""
import time
from unittest import mock

import pytest

from CTFd.cache import cache
from CTFd.plugins.l3mon_core.claim import claim
from tests.helpers import create_ctfd, destroy_ctfd

KEY = "l3mon:test:claim"


@pytest.fixture()
def app():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        cache.delete(KEY)
        yield app
        cache.delete(KEY)
    destroy_ctfd(app)


def test_the_first_caller_gets_it_and_the_next_does_not_while_it_is_held(app):
    assert claim(KEY, 30) is True
    assert claim(KEY, 30) is False
    assert claim(KEY, 30) is False


def test_it_is_given_for_the_time_asked_and_no_longer(app):
    with mock.patch.object(cache, "add", wraps=cache.add) as spy:
        assert claim(KEY, 7) is True
    assert spy.call_args.kwargs["timeout"] == 7 and spy.call_args.args[0] == KEY


@pytest.mark.parametrize("asked, given", [(10.0, 10), (1.5, 2), (0.2, 1), (7, 7)])
def test_the_cache_is_given_whole_seconds_whatever_the_caller_asked(app, asked, given):
    """Redis refuses `EXPIRE key 10.0` ("value is not an integer or out of range"): the e-mail normalisation backfill asked for 10.0 and was refused on
    every sign-up (found by the lead's audit of the integrated platform, 25 October 2026)."""
    with mock.patch.object(cache, "add", wraps=cache.add) as spy:
        assert claim(KEY, asked) is True
    assert spy.call_args.kwargs["timeout"] == given and isinstance(spy.call_args.kwargs["timeout"], int)


def test_taking_over_an_old_flag_also_gives_whole_seconds(app):
    cache.set(KEY, time.time() - 3600, timeout=0)
    with mock.patch.object(cache, "set", wraps=cache.set) as spy:
        assert claim(KEY, 10.0) is True
    assert spy.call_args.kwargs["timeout"] == 10 and isinstance(spy.call_args.kwargs["timeout"], int)


def test_a_flag_that_expired_between_the_look_and_the_take_is_taken_by_the_next_try(app):
    """The cache said it was held, then it was gone: the caller asks once more instead of giving up for a whole period."""
    cache.set(KEY, time.time(), timeout=30)
    real_get = cache.get
    answers = iter([None])

    def get_once_empty(key, *a, **kw):
        return next(answers, None) if key == KEY else real_get(key, *a, **kw)

    cache.delete(KEY)
    with mock.patch.object(cache, "get", side_effect=get_once_empty):
        assert claim(KEY, 30) is True


def test_a_flag_that_lost_its_lifetime_and_is_older_than_the_period_is_taken_over(app):
    """cachelib's Redis `add` is SETNX then EXPIRE; when the second command fails the key stays for ever. The time inside it says how old it is."""
    cache.set(KEY, time.time() - 3600, timeout=0)  # no lifetime at all
    assert claim(KEY, 30) is True, "an hour old: the holder is gone"
    assert claim(KEY, 30) is False, "and the new holder is the only one"
    with mock.patch.object(cache, "set", wraps=cache.set) as spy:
        cache.set(KEY, time.time() - 3600, timeout=0)
        spy.reset_mock()
        claim(KEY, 30)
    assert spy.call_args.kwargs["timeout"] == 30, "taking it over gives it a lifetime again"


def test_a_flag_taken_less_than_the_period_ago_is_still_held_even_without_a_lifetime(app):
    cache.set(KEY, time.time() - 10, timeout=0)
    assert claim(KEY, 30) is False


def test_a_time_from_the_future_is_a_clock_that_disagrees_and_counts_as_old(app):
    cache.set(KEY, time.time() + 3600, timeout=0)
    assert claim(KEY, 30) is True


@pytest.mark.parametrize("value", [1, True, "x", b"1", [1.0], {"at": 1.0}, 0])
def test_a_value_that_is_not_a_time_counts_as_held_and_is_left_to_expire_by_itself(app, value):
    cache.set(KEY, value, timeout=30)
    assert claim(KEY, 30) is False and cache.get(KEY) == value


def test_the_common_case_is_one_read_and_no_write(app):
    """Every request asks the minute check; while the flag is held that must be a single cheap read."""
    assert claim(KEY, 30) is True
    with mock.patch.object(cache, "add") as add, mock.patch.object(cache, "set") as put:
        assert claim(KEY, 30) is False
    assert add.call_count == 0 and put.call_count == 0

