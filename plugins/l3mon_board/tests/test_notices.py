"""The bell (SP3 part 3.5): `GET /api/v1/notifications?since_id=` for a signed-in studio, and the news numbers the board and the tick carry.

A studio reads the public news and the lines written for its own studio (and anything CTFd addresses to its account or studio), in one
numbering that grows with time. Nobody else's line is ever in it, no date or team id is sent, and CTFd's own open routes (the list for a
visitor, the detail of one notification, its page) are closed for everyone but the crew.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
import datetime
from types import SimpleNamespace

import pytest

from CTFd.models import Notifications, Teams, Users, db
from CTFd.plugins.l3mon_core.models import Note
from board_world import BOARD, T_LIVE, TICKS, clock, join_client, make_app, started, team_client, world
from tests.helpers import destroy_ctfd, login_as_user, register_user

LIST = "/api/v1/notifications"
T0 = datetime.datetime(2026, 11, 28, 5, 0, 0)


def at(minutes=0, micro=0):
    return T0 + datetime.timedelta(minutes=minutes, microseconds=micro)


def news(title, content, when, team=None, user=None):
    row = Notifications(title=title, content=content, date=when, team_id=Teams.query.filter_by(name=team).one().id if team else None, user_id=Users.query.filter_by(name=user).one().id if user else None)
    db.session.add(row)
    db.session.commit()
    return row


def line(team, title, text, when):
    row = Note(team_id=Teams.query.filter_by(name=team).one().id, title=title, text=text, created_at=when)
    db.session.add(row)
    db.session.commit()
    return row


@pytest.fixture()
def play():
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            world(app, admin)
            alice = team_client(app, "alice", "studio-a")
            abe = join_client(app, "abe", "studio-a")
            bob = team_client(app, "bob", "studio-b")
            yield SimpleNamespace(app=app, admin=admin, alice=alice, abe=abe, bob=bob, visitor=app.test_client())
    destroy_ctfd(app)


def items(client, path=LIST):
    r = client.get(path)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["data"]


def titles(client, path=LIST):
    return [item["title"] for item in items(client, path)]


# ---- the gate -----------------------------------------------------------------------------------------------------------------------

def test_a_visitor_is_asked_to_sign_in_for_the_list_and_for_a_head_request(play):
    r = play.visitor.get(LIST)
    body = r.get_json()
    assert r.status_code == 401 and body["success"] is False and body["error"] == "auth_required" and r.headers["X-Request-Id"] == body["request_id"]
    assert play.visitor.head(LIST).status_code == 401


def test_a_suspended_account_is_told_so_and_a_player_with_no_studio_is_told_to_make_one(play):
    register_user(play.app, name="loner", email="loner@example.com")
    loner = login_as_user(play.app, "loner")
    r = loner.get(LIST)
    assert r.status_code == 403 and r.get_json()["error"] == "no_team"
    Users.query.filter_by(name="bob").one().banned = True
    db.session.commit()
    r = play.bob.get(LIST)
    assert r.status_code == 403 and r.get_json()["error"] == "banned"


# ---- what a studio reads ------------------------------------------------------------------------------------------------------------

def test_a_studio_reads_the_public_news_and_its_own_lines_oldest_first_with_three_keys_and_no_dates(play):
    news("New on air", "CH 1 has 2 new programmes.", at(0))
    line("studio-a", "Bonus +50 TRP", "found a bug", at(5))
    news("Reminder", "Hints cost TRP.", at(10))
    got = items(play.alice)
    assert [(i["title"], i["content"]) for i in got] == [("New on air", "CH 1 has 2 new programmes."), ("Bonus +50 TRP", "found a bug"), ("Reminder", "Hints cost TRP.")]
    assert all(sorted(i) == ["content", "id", "title"] for i in got), "an id, a title and a text: no date, no team, no html"
    assert [i["id"] for i in got] == sorted(i["id"] for i in got) and len({i["id"] for i in got}) == 3


def test_a_teammate_reads_the_same_list_and_another_studio_reads_none_of_the_private_lines(play):
    news("New on air", "x", at(0))
    line("studio-a", "Solve voided", "the checker broke", at(5))
    line("studio-b", "Bonus +10 TRP", "for studio b only", at(6))
    assert titles(play.alice) == titles(play.abe) == ["New on air", "Solve voided"]
    assert titles(play.bob) == ["New on air", "Bonus +10 TRP"]
    assert "the checker broke" not in play.bob.get(LIST).get_data(as_text=True) and "for studio b only" not in play.alice.get(LIST).get_data(as_text=True)


def test_a_notification_that_ctfd_addresses_to_one_studio_or_one_account_is_read_only_by_them(play):
    news("For studio b", "addressed to the studio", at(1), team="studio-b")
    news("For abe", "addressed to one account", at(2), user="abe")
    news("Public", "all", at(3))
    assert titles(play.bob) == ["For studio b", "Public"]
    assert titles(play.abe) == ["For abe", "Public"] and titles(play.alice) == ["Public"], "an account's own line is its own, not its studio's"


def test_an_empty_list_is_an_empty_list(play):
    assert items(play.alice) == []


def test_the_query_filters_of_the_stock_route_are_not_honoured_for_a_player(play):
    other = Teams.query.filter_by(name="studio-b").one().id
    news("Public", "all", at(0))
    news("For studio b", "addressed", at(1), team="studio-b")
    for query in (f"?team_id={other}", "?q=studio&field=title", "?title=For studio b", f"?user_id={Users.query.filter_by(name='bob').one().id}"):
        assert titles(play.alice, LIST + query) == ["Public"], query


# ---- the ids ------------------------------------------------------------------------------------------------------------------------

def test_since_id_gives_only_the_newer_lines_and_none_or_zero_or_a_negative_gives_all(play):
    news("one", "1", at(0))
    line("studio-a", "two", "2", at(1))
    news("three", "3", at(2))
    ids = [i["id"] for i in items(play.alice)]
    assert titles(play.alice, LIST + f"?since_id={ids[0]}") == ["two", "three"]
    assert titles(play.alice, LIST + f"?since_id={ids[2]}") == []
    for query in ("", "?since_id=0", "?since_id=-7", "?since_id="):
        assert titles(play.alice, LIST + query) == ["one", "two", "three"], query


@pytest.mark.parametrize("bad", ["abc", "1.5", "1e3", "12 34", "--1"])
def test_a_since_id_that_is_not_a_whole_number_is_the_contracts_400(play, bad):
    r = play.alice.get(LIST + f"?since_id={bad}")
    body = r.get_json()
    assert r.status_code == 400 and body["error"] == "invalid" and body["errors"] == {"since_id": ["must be a whole number"]}
    assert body["success"] is False and r.headers["X-Request-Id"] == body["request_id"]


def test_ids_are_whole_numbers_that_a_browser_keeps_exactly_and_grow_with_time(play):
    news("a", "1", at(0))
    line("studio-a", "b", "2", at(1))
    got = [i["id"] for i in items(play.alice)]
    assert all(isinstance(i, int) and 0 < i < 2**53 for i in got) and got == sorted(got)
    assert got[1] - got[0] >= 60_000 * 1000 - 1000, "a minute apart is a minute of milliseconds apart (times a thousand)"


def test_a_public_line_and_a_private_line_made_in_the_same_millisecond_get_different_ids_and_a_fixed_order(play):
    news("public", "p", at(0, 5000))
    line("studio-a", "private", "q", at(0, 5000))
    got = items(play.alice)
    assert len({i["id"] for i in got}) == 2 and [i["title"] for i in got] == ["public", "private"]


def test_two_public_lines_made_in_the_same_millisecond_still_differ(play):
    first = news("first", "1", at(0))
    second = news("second", "2", at(0))
    assert len({i["id"] for i in items(play.alice)}) == 2 and second.id > first.id


def test_an_id_does_not_move_when_an_older_line_is_removed_and_a_new_line_is_always_newer_than_what_was_seen(play):
    old = news("old", "o", at(0))
    news("kept", "k", at(1))
    seen = max(i["id"] for i in items(play.alice))
    kept_id = [i["id"] for i in items(play.alice) if i["title"] == "kept"][0]
    db.session.delete(old)
    db.session.commit()
    assert [i["id"] for i in items(play.alice)] == [kept_id], "removing an older line changes no id"
    news("later", "l", at(2))
    assert titles(play.alice, LIST + f"?since_id={seen}") == ["later"]


# ---- the numbers the board and the tick carry -----------------------------------------------------------------------------------------

def numbers(client):
    board = client.get(BOARD).get_json()["data"]
    tick = client.get(TICKS).get_json()["data"]
    assert board["notif_ver"] == tick["notif_ver"], "the board and the tick agree"
    return board["notif_id"], board["notif_ver"]


def test_notif_id_is_the_newest_id_the_viewer_may_read_and_0_for_none(play):
    assert numbers(play.alice)[0] == 0
    news("a", "1", at(0))
    line("studio-a", "b", "2", at(1))
    assert numbers(play.alice)[0] == items(play.alice)[-1]["id"]
    assert numbers(play.bob)[0] == items(play.bob)[-1]["id"] != numbers(play.alice)[0]


def test_notif_ver_moves_for_every_change_of_the_viewers_list_and_for_nothing_else(play):
    news("public", "p", at(0))
    base = numbers(play.alice)[1]
    assert base == numbers(play.alice)[1], "unchanged, unchanged"
    # someone else's private line, someone else's addressed notification: nothing moves for alice (and nothing is learned about them)
    line("studio-b", "Bonus", "b's", at(1))
    news("for b", "x", at(2), team="studio-b")
    assert numbers(play.alice)[1] == base
    # her own line, a public line, an edit, a removal: each moves it
    seen = {base}
    mine = line("studio-a", "Solve voided", "m", at(3))
    seen.add(numbers(play.alice)[1])
    public = news("second", "s", at(4))
    seen.add(numbers(play.alice)[1])
    public.content = "s, edited"
    db.session.commit()
    seen.add(numbers(play.alice)[1])
    db.session.delete(mine)
    db.session.commit()
    seen.add(numbers(play.alice)[1])
    assert len(seen) == 5, "five different states, five different numbers"
    assert all(isinstance(v, int) and 0 <= v < 2**53 for v in seen)


def test_notif_ver_is_0_for_an_empty_list_and_the_ticks_say_the_same(play):
    assert numbers(play.alice) == (0, 0)


# ---- the crew, and CTFd's own open routes ---------------------------------------------------------------------------------------------

def test_the_crew_keeps_ctfds_own_list_with_every_row_its_dates_and_its_addressed_lines(play):
    news("public", "p", at(0))
    news("for b", "x", at(1), team="studio-b")
    r = play.admin.get(LIST)
    got = r.get_json()["data"]
    assert r.status_code == 200 and {n["title"] for n in got} == {"public", "for b"} and all("date" in n and "team_id" in n for n in got)


def test_the_detail_of_one_notification_answers_a_player_as_a_missing_id_does_and_the_crew_as_ctfd_does(play):
    row = news("for b", "addressed", at(0), team="studio-b")
    public = news("public", "p", at(1))
    gone = play.alice.get(f"{LIST}/99999")
    for n in (row.id, public.id):
        r = play.alice.get(f"{LIST}/{n}")
        assert r.status_code == 404 and r.get_data() != b"" and gone.status_code == 404
    assert play.bob.get(f"{LIST}/{row.id}").status_code == 404, "not even its own studio reads it this way"
    assert play.visitor.get(f"{LIST}/{row.id}").status_code in (401, 404)
    assert play.admin.get(f"{LIST}/{row.id}").get_json()["data"]["content"] == "addressed"


def test_head_for_a_player_is_the_same_gate_and_no_count_of_other_peoples_lines(play):
    news("for b", "x", at(0), team="studio-b")
    r = play.alice.head(LIST)
    assert r.status_code == 200 and "Result-Count" not in r.headers


def test_the_stock_page_is_closed_to_players_and_open_to_the_crew(play):
    news("for b", "addressed", at(0), team="studio-b")
    assert play.alice.get("/notifications").status_code == 404 and play.visitor.get("/notifications").status_code == 404
    page = play.admin.get("/notifications")
    assert page.status_code == 200


def test_writing_stays_with_the_crew(play):
    for client in (play.alice, play.visitor):
        r = client.post(LIST, json={"title": "x", "content": "y"})
        assert r.status_code in (302, 401, 403)
    assert Notifications.query.count() == 0
    r = play.admin.post(LIST, json={"title": "Crew news", "content": "hello"})
    assert r.status_code == 200 and titles(play.alice) == ["Crew news"]


# ---- found by the independent review of 2026-10-10 ---------------------------------------------------------------------------------------

ODD_IDS = ["+1", "1.0", "1e0", "%201", "1%20", "1%09", "1abc", "1x", "1-", "0001", "١", "1/", "1//"]


@pytest.mark.parametrize("odd", ODD_IDS)
def test_no_spelling_of_an_id_opens_the_detail_of_one_notification_for_a_player_or_a_visitor(play, odd):
    """CTFd reads the text after the last slash as a number leniently (a plus sign, a decimal point, trailing letters on MariaDB); the guard refuses the whole address space."""
    row = news("for b", "SECRET-ADDRESSED", at(0), team="studio-b")
    path = f"{LIST}/{odd.replace('1', str(row.id)) if odd != '١' else row.id}"
    for client in (play.alice, play.bob, play.visitor):
        r = client.get(path)
        assert r.status_code in (401, 404), (path, r.status_code)
        assert "SECRET-ADDRESSED" not in r.get_data(as_text=True) and "team_id" not in r.get_data(as_text=True), path
        assert client.head(path).status_code in (401, 404), path


def test_a_refusal_of_the_guard_is_the_envelope_with_a_request_id(play):
    row = news("for b", "x", at(0), team="studio-b")
    r = play.alice.get(f"{LIST}/+{row.id}")
    body = r.get_json()
    assert r.status_code == 404 and body["success"] is False and body["error"] == "not_found" and r.headers["X-Request-Id"] == body["request_id"]
    r = play.visitor.get(f"{LIST}/{row.id}")
    assert r.status_code == 401 and r.get_json()["error"] == "auth_required" and r.headers["X-Request-Id"]


def test_the_crew_still_reads_one_notification_whatever_the_spelling_ctfd_accepts(play):
    row = news("for b", "addressed", at(0), team="studio-b")
    assert play.admin.get(f"{LIST}/{row.id}").get_json()["data"]["content"] == "addressed"


def test_the_event_stream_that_pushes_every_notification_is_closed_to_players_and_visitors(play):
    """CTFd's /events pushes each notification made through the API, an addressed one included, to every signed-in account."""
    for client in (play.alice, play.bob, play.visitor):
        r = client.get("/events")
        assert r.status_code == 404, r.status_code
        assert client.get("/events/").status_code == 404


def test_the_crew_is_not_stopped_at_the_event_stream(play, monkeypatch):
    from CTFd.plugins.l3mon_board import notices

    monkeypatch.setattr(notices, "is_admin", lambda: True)
    with play.app.test_request_context("/events"):
        assert notices.guard() is None


def test_the_low_part_of_an_id_reveals_nothing_about_other_studios_lines(play):
    """The two tables count their rows across all studios; an id must not carry a row number, or a studio could count the lines written for the others."""
    line("studio-a", "first", "1", at(0))
    for n in range(3):
        line("studio-b", f"b{n}", "x", at(1 + n))
        news(f"for b {n}", "x", at(1 + n), team="studio-b")
    line("studio-a", "second", "2", at(10))
    news("public", "p", at(11))
    ids = {i["title"]: i["id"] for i in items(play.alice)}
    assert ids["first"] % 1000 == 500 and ids["second"] % 1000 == 500, "a private line made alone in its millisecond: just the private mark"
    assert ids["public"] % 1000 == 0, "a public line made alone in its millisecond: nothing added"


def test_lines_made_in_one_millisecond_are_told_apart_by_their_order_among_the_viewers_own(play):
    line("studio-a", "p1", "1", at(0, 3000))
    line("studio-a", "p2", "2", at(0, 3000))
    line("studio-b", "other", "x", at(0, 3000))
    news("n1", "1", at(0, 3000))
    news("for b", "x", at(0, 3000), team="studio-b")
    news("n2", "2", at(0, 3000))
    got = {i["title"]: i["id"] for i in items(play.alice)}
    assert [got["n1"] % 1000, got["n2"] % 1000, got["p1"] % 1000, got["p2"] % 1000] == [0, 1, 500, 501], "a count of the viewer's own lines, not a row number"
    assert len(set(got.values())) == 4


def test_the_crews_bell_numbers_are_ctfds_own_so_that_its_since_id_works(play):
    first = news("one", "1", at(0))
    board = play.admin.get(BOARD).get_json()["data"]
    assert board["notif_id"] == first.id and board["notif_ver"] != 0
    assert play.admin.get(TICKS).get_json()["data"]["notif_ver"] == board["notif_ver"]
    assert play.admin.get(f"{LIST}?since_id={board['notif_id']}").get_json()["data"] == []
    second = news("two", "2", at(1), team="studio-b")
    board_after = play.admin.get(BOARD).get_json()["data"]
    assert board_after["notif_id"] == second.id and board_after["notif_ver"] != board["notif_ver"]
    assert [n["title"] for n in play.admin.get(f"{LIST}?since_id={first.id}").get_json()["data"]] == ["two"]
