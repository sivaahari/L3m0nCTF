"""The one ranking the scoreboard and the CTFtime feed share: positions for the studios that score above zero, in CTFd's own order.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
from types import SimpleNamespace

from CTFd.plugins.l3mon_core.standings import ranked


def row(name, score, account_id=1):
    return SimpleNamespace(name=name, score=score, account_id=account_id)


def test_positions_count_the_rows_that_are_shown_so_a_studio_left_out_leaves_no_gap():
    rows = [row("a", 300, 1), row("b", 0, 2), row("c", None, 3), row("d", -5, 4), row("e", 120, 5), row("f", 120, 6)]
    assert [(pos, r.name) for pos, r in ranked(rows)] == [(1, "a"), (2, "e"), (3, "f")], "the order CTFd gave is kept, ties included"


def test_nothing_is_shown_for_an_empty_list_or_one_with_no_score():
    assert ranked([]) == [] and ranked([row("a", 0), row("b", -1)]) == []


def test_a_score_that_is_a_decimal_or_a_string_from_the_database_is_read_as_a_number():
    from decimal import Decimal

    assert [r.name for _, r in ranked([row("a", Decimal("0.5")), row("b", "0"), row("c", 1.5)])] == ["a", "c"]


def test_a_score_is_a_whole_number_when_it_is_one_and_two_places_otherwise():
    from decimal import Decimal

    from CTFd.plugins.l3mon_core.standings import number

    assert number(300) == 300 and isinstance(number(300), int)
    assert number(Decimal("300")) == 300 and isinstance(number(Decimal("300")), int)
    assert number(300.0) == 300 and isinstance(number(300.0), int)
    assert number("45") == 45
    assert number(Decimal("12.345")) == 12.35 and number(0.5) == 0.5 and number(-80) == -80
