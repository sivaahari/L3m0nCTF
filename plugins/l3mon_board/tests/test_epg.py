"""The programme grid (SP3 part 3.5): `GET /api/v1/l3mon/guide/epg`, a TV programme guide as markup. A row for each channel, a column for each
difficulty, a block for each programme the viewer may see. A programme the crew has not released (or a studio has not unlocked by its
prerequisite) is not in it at all: the row says only how many are still to come.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board
"""
import datetime
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from CTFd.models import Challenges, Teams, db
from CTFd.utils import set_config
from board_world import T_END, T_LIVE, T_START, clock, make_app, on_air, solve, started, team_client, user_id, withhold, world
from tests.helpers import destroy_ctfd, login_as_user, register_user

GUIDE = "/api/v1/l3mon/guide"
EPG = "/api/v1/l3mon/guide/epg"


def when(minutes):
    return datetime.datetime.utcfromtimestamp(T_LIVE) + datetime.timedelta(minutes=minutes)


@pytest.fixture()
def play():
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            ids = world(app, admin)
            on_air(admin, "lantern_walk", "hush_alley", "noodle_ledger", "steam_tunnel", "coffee_break")
            alice = team_client(app, "alice", "studio-a")
            bob = team_client(app, "bob", "studio-b")
            yield SimpleNamespace(app=app, admin=admin, ids=ids, alice=alice, bob=bob, visitor=app.test_client())
    destroy_ctfd(app)


def give(team, challenge, minutes, who):
    return solve(Teams.query.filter_by(name=team).one().id, user_id(who), challenge, at=when(minutes))


def fragment(client):
    r = client.get(EPG)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_data(as_text=True)


def grid(markup):
    """[{no, id, name, meta, soon, ad, tiers: {1..5: [(cls, slug, label, name, value)]}}] from the markup."""
    rows = []
    for chunk in re.findall(r'<div class="epg-row.*?(?=<div class="epg-row|\Z)', markup, re.S):
        head = re.search(
            r'<div class="epg-row ch-(\d+)" role="group" aria-labelledby="epg-ch-(\d+)">\s*<h3 class="epg-ch" id="epg-ch-\2"><b class="epg-no">CH (\d+)</b>'
            r'<span class="epg-name">(.*?)</span><span class="epg-meta">(.*?)</span><span class="epg-soon">(.*?)</span><span class="epg-ad">(.*?)</span></h3>', chunk, re.S)
        assert head, chunk
        tiers = {}
        for tier, body in re.findall(r'<div class="epg-cell t-(\d)">(.*?)</div>', chunk, re.S):
            tiers[int(tier)] = re.findall(
                r'<a class="epg-b ([^"]*)" href="/board/([^"]*)" aria-label="([^"]*)"><span class="epg-n">(.*?)</span><span class="epg-v" aria-hidden="true">(\d+)</span></a>', body)
        rows.append({"class_no": int(head.group(1)), "id": int(head.group(2)), "no": head.group(3), "name": head.group(4), "meta": head.group(5), "soon": head.group(6),
                     "ad": head.group(7), "tiers": tiers})
    return rows


def slugs(row):
    return {tier: [b[1] for b in blocks] for tier, blocks in row["tiers"].items() if blocks}


# ---- the gate ----------------------------------------------------------------------------------------------------------------------------

def test_a_visitor_is_asked_to_sign_in(play):
    r = play.visitor.get(EPG)
    assert r.status_code == 401 and r.get_json()["error"] == "auth_required"


def test_a_player_with_no_studio_is_told_to_make_one(play):
    register_user(play.app, name="loner", email="loner@example.com")
    r = login_as_user(play.app, "loner").get(EPG)
    assert r.status_code == 403 and r.get_json()["error"] == "no_team"


# ---- the grid ----------------------------------------------------------------------------------------------------------------------------

def test_a_row_for_each_channel_in_the_order_players_know_them_with_the_programmes_in_their_difficulty_column(play):
    rows = grid(fragment(play.alice))
    assert [(r["class_no"], r["no"], r["name"]) for r in rows] == [(1, "01", "Mighty Street"), (2, "02", "Snack Square"), (7, "07", "Sponsored Break")]
    assert [slugs(r) for r in rows] == [{1: ["lantern_walk"], 4: ["hush_alley"]}, {3: ["noodle_ledger"], 5: ["steam_tunnel"]}, {2: ["coffee_break"]}]
    assert [sorted(r["tiers"]) for r in rows] == [[1, 2, 3, 4, 5]] * 3, "all five columns are always there, empty or not"


def test_each_row_says_how_many_are_on_air_and_how_many_are_coming_and_who_sponsors_it(play):
    rows = grid(fragment(play.alice))
    assert [(r["meta"], r["soon"], r["ad"]) for r in rows] == [
        ("2 on air", "1 coming up", ""),
        ("2 on air", "1 coming up", ""),
        ("1 on air", "", "Sponsored by Acme"),
    ]


def test_a_channel_with_nothing_on_air_says_so_and_still_counts_what_is_coming(play):
    withhold(play.admin, "coffee_break")
    rows = grid(fragment(play.alice))
    assert (rows[2]["meta"], rows[2]["soon"], slugs(rows[2])) == ("Off air for now", "1 coming up", {})


def test_a_block_has_the_name_the_value_the_link_and_the_words_a_screen_reader_says(play):
    row = grid(fragment(play.alice))[0]
    assert row["tiers"][1] == [("is-open", "lantern_walk", "Lantern Walk, Web, warm-up, 100 TRP, not solved", "Lantern Walk", "100")]
    assert row["tiers"][4] == [("is-open", "hush_alley", "Hush Alley, Forensics, hard, 300 TRP, not solved", "Hush Alley", "300")]


def test_a_solved_programme_is_filled_in_for_the_studio_that_solved_it_only(play):
    give("studio-a", play.ids.lantern, 0, "alice")
    mine, theirs = grid(fragment(play.alice))[0]["tiers"][1][0], grid(fragment(play.bob))[0]["tiers"][1][0]
    assert mine[0] == "is-solved" and mine[2].endswith("100 TRP, solved") and theirs[0] == "is-open" and theirs[2].endswith("not solved")


def test_nothing_about_a_programme_off_air_is_in_the_markup_only_a_count(play):
    text = fragment(play.alice).lower()
    for secret in ("moth", "cipher", "dumpling", "gate", "careful", "102", "203"):
        assert secret not in text, secret


def test_a_programme_whose_prerequisite_is_not_solved_is_coming_not_shown_until_it_is_solved(play):
    on_air(play.admin, "dumpling_gate")
    rows = grid(fragment(play.alice))
    assert "dumpling_gate" not in str(rows) and rows[1]["soon"] == "1 coming up" and rows[1]["meta"] == "2 on air"
    give("studio-a", play.ids.lantern, 0, "alice")
    snack = grid(fragment(play.alice))[1]
    assert slugs(snack) == {3: ["noodle_ledger", "dumpling_gate"], 5: ["steam_tunnel"]} and snack["soon"] == ""
    assert "dumpling_gate" not in str(grid(fragment(play.bob))), "another studio still waits"


def test_a_pulled_back_programme_leaves_the_grid_and_becomes_one_more_coming(play):
    withhold(play.admin, "hush_alley")
    row = grid(fragment(play.alice))[0]
    assert slugs(row) == {1: ["lantern_walk"]} and (row["meta"], row["soon"]) == ("1 on air", "2 coming up")


def test_a_value_is_the_programmes_current_value(play):
    on_air(play.admin, "moth_cipher")
    row = grid(fragment(play.alice))[0]
    shown = row["tiers"][2][0]
    assert shown[1] == "moth_cipher" and shown[4] == str(Challenges.query.get(play.ids.moth).value)


def test_the_coming_count_follows_the_crews_switch(play):
    set_config("l3mon_show_coming_count", "off")
    rows = grid(fragment(play.alice))
    assert [r["soon"] for r in rows] == ["", "", ""] and [r["meta"] for r in rows] == ["2 on air", "2 on air", "1 on air"]
    set_config("l3mon_show_coming_count", "on")
    assert [r["soon"] for r in grid(fragment(play.alice))] == ["1 coming up", "1 coming up", ""]


def test_the_crew_sees_the_grid_as_a_player_with_nothing_solved_sees_it(play):
    give("studio-a", play.ids.lantern, 0, "alice")
    assert grid(fragment(play.admin))[0]["tiers"][1][0][0] == "is-open"
    assert fragment(play.admin) == fragment(play.bob)


def test_before_the_start_there_is_no_grid_and_after_the_end_every_programme_of_the_plan_is_back(play):
    with clock(T_START - 3600):
        r = play.alice.get(EPG)
        assert r.status_code == 200 and r.get_data(as_text=True) == "" and r.headers["ETag"] == '"enone"'
    set_config("view_after_ctf", True)
    give("studio-a", play.ids.lantern, 0, "alice")  # Dumpling Gate asks for it, after the end as before it
    with clock(T_END + 60):
        rows = grid(fragment(play.alice))
        assert sum(len(b) for r in rows for b in r["tiers"].values()) == 7, "all seven programmes of the plan"
        assert [r["soon"] for r in rows] == ["", "", ""]


def test_a_name_with_markup_is_shown_as_text_never_as_elements(play):
    street = Challenges.query.get(play.ids.lantern)
    street.name = '<img src=x onerror="alert(1)">&'
    db.session.commit()
    text = fragment(play.alice)
    assert "<img" not in text and "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;&amp;" in text
    assert grid(text)[0]["tiers"][1][0][3] == "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;&amp;"


def test_a_sponsor_with_markup_is_shown_as_text(play):
    from CTFd.plugins.l3mon_core.models import Channel

    Channel.query.filter_by(slug="break").one().sponsor_name = "<b>Acme</b>"
    db.session.commit()
    text = fragment(play.alice)
    assert "<b>Acme" not in text and "Sponsored by &lt;b&gt;Acme&lt;/b&gt;" in text


# ---- the signature and the caches ------------------------------------------------------------------------------------------------------------------------

def test_the_signature_is_the_guides_epg_sig_and_the_fragments_etag(play):
    sig = play.alice.get(GUIDE).get_json()["data"]["epg_sig"]
    r = play.alice.get(EPG)
    assert re.fullmatch(r"[A-Za-z0-9_-]{16}", sig) and r.headers["ETag"] == f'"e{sig}"'
    assert r.mimetype == "text/html" and r.headers["Cache-Control"] == "private, no-cache" and r.headers["X-Request-Id"] and r.headers["Vary"] == "Cookie"
    again = play.alice.get(EPG, headers={"If-None-Match": r.headers["ETag"]})
    assert again.status_code == 304 and again.get_data() == b""


def sig_of(client):
    return client.get(GUIDE).get_json()["data"]["epg_sig"]


def test_the_signature_moves_when_the_picture_changes_and_not_when_it_does_not(play):
    first = sig_of(play.alice)
    from CTFd.plugins.l3mon_core.tick import tick

    tick.bump()
    assert sig_of(play.alice) == first, "the tick moves for everybody's news; the grid did not change"
    give("studio-b", play.ids.coffee, 0, "bob")
    assert sig_of(play.alice) == first, "another studio's solve of a fixed-value programme changes nothing in this studio's grid"
    give("studio-a", play.ids.lantern, 1, "alice")
    after_solve = sig_of(play.alice)
    assert after_solve != first, "its own solve fills a block"
    on_air(play.admin, "dumpling_gate")
    assert sig_of(play.alice) != after_solve, "a programme going on air that is only a count now still moves the count or the grid"
    before_pull = sig_of(play.alice)
    withhold(play.admin, "hush_alley")
    assert sig_of(play.alice) != before_pull
    renamed = sig_of(play.alice)
    Challenges.query.get(play.ids.lantern).name = "Lantern Run"
    db.session.commit()
    assert sig_of(play.alice) != renamed


def test_the_signature_follows_the_coming_switch_only_when_it_changes_the_markup(play):
    on = sig_of(play.alice)
    set_config("l3mon_show_coming_count", "off")
    assert sig_of(play.alice) != on, "a coming count was hidden"


def test_each_studio_has_its_own_signature_when_it_sees_something_different(play):
    assert sig_of(play.alice) == sig_of(play.bob)
    give("studio-a", play.ids.lantern, 0, "alice")
    assert sig_of(play.alice) != sig_of(play.bob)


# ---- the cost ---------------------------------------------------------------------------------------------------------------------------------------------

def test_the_queries_do_not_grow_with_the_programmes_on_air(play):
    def count():
        play.alice.get(EPG)  # warm CTFd's own caches first (the standings are kept for a minute), so only our queries are counted
        statements = []

        def before(conn, cursor, statement, params, context, executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", before)
        try:
            assert play.alice.get(EPG).status_code == 200
        finally:
            event.remove(db.engine, "before_cursor_execute", before)
        return len(statements)

    few = count()
    on_air(play.admin, "moth_cipher", "dumpling_gate")
    give("studio-a", play.ids.lantern, 0, "alice")
    give("studio-a", play.ids.hush, 1, "alice")
    after = count()
    assert after <= few + 1, (few, after)
