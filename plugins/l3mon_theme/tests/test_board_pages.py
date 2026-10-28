"""The channels board through a whole CTFd (SP6, stream A): /challenges, /board and a programme's own page /board/<slug> are the theme's,
show what the viewer may see and nothing else, and meet the gates CTFd gives.

The world is the board plugin's made-up one (three channels, seven programmes; one of the street's is held back, one of the snack square's needs
a solve first). Run through tools/run-ctfd-tests.sh (see docs/deploy/theme-slices.md):
    env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme/test_board_pages.py
"""
import html as htmllib
import re
from unittest import mock

import pytest

from CTFd.models import Users, db
from CTFd.utils import set_config
from CTFd.plugins.l3mon_theme.pages import board as page
from theme_a_world import (  # noqa: F401  (the fixture is used by name)
    PASSWORD, T_END, T_LIVE, T_START, clock, main_of, on_air, play, register_user, statements_of, text, withhold,
)
from tests.helpers import login_as_user

BOARD_PATHS = ("/challenges", "/board")


def forbidden_words(html):
    """What no page of the real platform says or does (strict content security policy; TRP, never points; no prototype wording)."""
    assert not re.search(r"<script(?![^>]*\ssrc=)", html, re.I), "an inline script"
    assert not re.search(r"\sstyle\s*=", html, re.I), "a style attribute"
    assert not re.search(r"\son[a-z]+\s*=", html, re.I), "an event attribute"
    assert "javascript:" not in html.lower()
    assert not re.search(r"prototype|mock\b|stand-in|DIRECTOR", html, re.I), "prototype wording"
    assert not re.search(r"\bpoints?\b", main_of(html), re.I), "the unit is TRP"


# ---- the page --------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", BOARD_PATHS)
def test_a_player_gets_the_themes_board_with_the_first_channel_tuned(play, path):
    with clock(T_LIVE):
        r = play.alice.get(path, follow_redirects=True)
        html = text(r)
    assert r.status_code == 200
    assert 'data-board' in html and 'id="board-h"' in html and "Channels" in html
    assert 'class="board ch-1"' in html and 'data-ch="1"' in html, "the channel is known by the number the crew gave it (1), not by its row"
    assert "CH 01" in html and "Mighty Street" in html
    assert "2 programmes · 1 coming up" in html, "two of the street's three programmes are on air; the held-back one is only counted"
    for name in ("Snack Square", "Sponsored Break"):
        assert name in html
    assert "<title>Mighty Street | Channels | " in html
    forbidden_words(html)


def test_the_board_names_the_bundles_of_the_theme_and_the_sessions_token(play):
    with clock(T_LIVE):
        html = text(play.alice.get("/challenges", follow_redirects=True))
        with play.alice.session_transaction() as sess:
            nonce = sess["nonce"]
    assert f'<meta name="csrf-token" content="{nonce}">' in html
    assert re.search(r'src="/themes/l3mon/static/board\.[0-9a-f]{10}\.js"', html)
    assert re.search(r'href="/themes/l3mon/static/app\.[0-9a-f]{10}\.css"', html)
    assert 'id="tpl-tile"' in html and "data-panel" in html, "the tile template and the programme panel are on the page"


def test_the_board_and_the_challenge_address_draw_the_same_page(play):
    with clock(T_LIVE):
        a, b = text(play.alice.get("/challenges", follow_redirects=True)), text(play.alice.get("/board"))
    assert main_of(a) == main_of(b)
    assert re.sub(r"\s+", " ", a) == re.sub(r"\s+", " ", b)


def test_a_channel_is_chosen_by_its_number_and_nothing_else_selects_one(play):
    with clock(T_LIVE):
        seven = text(play.alice.get("/board?ch=7"))
        two = text(play.alice.get("/board?ch=2"))
        by_row = [text(play.alice.get(f"/board?ch={n}")) for n in ("3", "4", "99", "abc", "1;2", "-1", "01x", "2" * 40, "%00")]
    assert "CH 07" in seven and "Sponsored Break" in seven and "Sponsored by Acme. These programmes count toward your TRP like any other." in seven
    assert "CH 02" in two and 'class="board ch-2"' in two
    for html in by_row:
        assert 'class="board ch-1"' in html, "an unknown or malformed channel falls back to the first"


def test_a_programme_the_viewer_may_open_has_its_own_page_on_its_channel(play):
    with clock(T_LIVE):
        r = play.alice.get("/board/moth_cipher")
        html = text(r)
    assert r.status_code == 200
    assert 'data-slug="moth_cipher"' in html and 'class="board ch-1"' in html
    assert "<title>Moth Cipher | Mighty Street | Channels | " in html
    forbidden_words(html)
    with clock(T_LIVE):
        other = text(play.alice.get("/board/coffee_break"))
    assert 'data-slug="coffee_break"' in other and 'class="board ch-7"' in other and "Sponsored by Acme" in other


def test_a_programme_that_is_held_back_or_locked_or_unknown_is_a_missing_page_alike(play):
    """The same status, the same page and the same number of database statements: nothing tells the three apart."""
    def ask(client, slug):
        def go():
            return client.get(f"/board/{slug}")

        client.get(f"/board/{slug}")  # the first request fills the caches
        return statements_of(go)

    with clock(T_LIVE):
        held, locked, unknown = ask(play.bob, "hush_alley"), ask(play.bob, "dumpling_gate"), ask(play.bob, "never_existed")
    for (response, _), slug in ((held, "hush_alley"), (locked, "dumpling_gate"), (unknown, "never_existed")):
        assert response.status_code == 404, slug
    normal = lambda r: re.sub(r"(hush_alley|dumpling_gate|never_existed)", "SLUG", re.sub(r"\s+", " ", text(r[0])))  # noqa: E731
    assert normal(held) == normal(locked) == normal(unknown)
    assert len(held[1]) == len(locked[1]) == len(unknown[1])
    for r in (held, locked, unknown):
        assert "Hush Alley" not in text(r[0]) and "Dumpling Gate" not in text(r[0])
    # and the board itself never names what is held back
    with clock(T_LIVE):
        board = text(play.bob.get("/board"))
    assert "Hush Alley" not in board and "hush_alley" not in board and "Dumpling Gate" not in board


def test_a_locked_programme_opens_for_the_studio_that_solved_its_prerequisite_and_only_for_it(play):
    with clock(T_LIVE):
        assert play.alice.get("/board/dumpling_gate").status_code == 404
        r = play.alice.post("/api/v1/challenges/attempt", json={"challenge_id": play.ids.lantern, "submission": "lantern-answer"})
        assert r.get_json()["data"]["status"] == "correct"
        assert play.alice.get("/board/dumpling_gate").status_code == 200
        assert play.abe.get("/board/dumpling_gate").status_code == 200, "the studio's other player"
        assert play.bob.get("/board/dumpling_gate").status_code == 404, "another studio"


@pytest.mark.parametrize("slug", ["", "A", "x" * 49, "a b", "a;b", "..%2f", "a\\b", "%00", "ünï"])
def test_a_slug_that_is_not_plain_is_a_missing_page(play, slug):
    with clock(T_LIVE):
        r = play.alice.get(f"/board/{slug}")
    assert r.status_code == 404


def test_what_a_name_or_a_story_says_never_reaches_the_page_as_markup(play):
    from CTFd.plugins.l3mon_core.models import Channel

    with clock(T_LIVE):
        street = Channel.query.filter_by(slug="street").first()
        street.name = '"><img src=x onerror=alert(1)>'
        street.synopsis = "<script>alert(2)</script>"
        db.session.commit()
        html = text(play.alice.get("/board"))
    assert "<img src=x" not in html and "<script>alert" not in html
    assert "&lt;img src=x" in html or "&#34;&gt;&lt;img" in html


# ---- the gates: CTFd's, in every state --------------------------------------------------------------------------------------------


def seen(response):
    """Status and destination, with the `next` of a redirect left out (it names the address that was asked, which differs by design)."""
    return (response.status_code, re.sub(r"next=[^&]*", "next=X", response.headers.get("Location") or "") or None)


def same_gate(client):
    """/board, /board/<a real slug> and /challenges answer a request the same way (status and where they send it)."""
    return {seen(client.get(p)) for p in ("/challenges", "/board", "/board/lantern_walk")}


def test_a_visitor_is_sent_to_sign_in_from_every_address(play):
    with clock(T_LIVE):
        visitor = play.app.test_client()
        gates = [seen(visitor.get(p)) for p in ("/challenges", "/board", "/board/lantern_walk", "/board/nothing_like_it")]
    assert all(status == 302 and location.startswith("/login") for status, location in gates), gates


def test_a_player_without_a_studio_is_sent_to_make_one(play):
    with clock(T_LIVE):
        register_user(play.app, name="loner", email="loner@example.com", password=PASSWORD)
        loner = login_as_user(play.app, "loner", PASSWORD)
        # /challenges sends a signed-in player to /board, which sends them to /team
        r_ch = loner.get("/challenges")
        assert r_ch.status_code == 302 and r_ch.headers["Location"].endswith("/board")
        r_board_from_ch = loner.get(r_ch.headers["Location"])
        assert r_board_from_ch.status_code == 302 and "/team" in r_board_from_ch.headers["Location"]

        # /board and deep links send them directly to /team
        gates = [seen(loner.get(p)) for p in ("/board", "/board/lantern_walk", "/board/nothing_like_it")]
    assert all(status == 302 and location.startswith("/team") for status, location in gates), gates


def test_an_unverified_account_is_sent_to_confirm_its_address(play):
    set_config("verify_emails", True)
    with clock(T_LIVE):
        user = Users.query.filter_by(name="bob").first()
        user.verified = False
        db.session.commit()
        # /challenges sends a signed-in player to /board, which sends them to /confirm
        r_ch = play.bob.get("/challenges")
        assert r_ch.status_code == 302 and r_ch.headers["Location"].endswith("/board")
        r_board_from_ch = play.bob.get(r_ch.headers["Location"])
        assert r_board_from_ch.status_code == 302 and "confirm" in r_board_from_ch.headers["Location"]

        gates = [seen(play.bob.get(p)) for p in ("/board", "/board/lantern_walk")]
    assert all(status == 302 and "confirm" in location for status, location in gates), gates
    with clock(T_LIVE):  # the other studio's player is verified
        assert play.alice.get("/board").status_code in (200, 302)


def test_a_suspended_account_is_turned_away_from_every_address(play):
    with clock(T_LIVE):
        user = Users.query.filter_by(name="bob").first()
        user.banned = True
        db.session.commit()
        codes = [play.bob.get(p).status_code for p in ("/challenges", "/board", "/board/lantern_walk")]
    assert codes == [403, 403, 403]


def test_before_the_start_the_board_shows_its_own_not_on_air_page_and_the_stock_address_keeps_ctfds_gate(play):
    set_config("start", T_LIVE + 7200)
    set_config("end", T_END)
    with clock(T_LIVE):
        for who in (play.alice, play.admin):
            for path in ("/board", "/board/lantern_walk", "/board/never_existed"):
                r = who.get(path)
                html = text(r)
                assert r.status_code == 200, (path, r.status_code)
                assert 'data-slate="before"' in html and "NOT ON AIR YET" in html and "data-board" not in html
                assert re.search(r"It starts at \d\d:\d\d IST on \d{1,2} [A-Z][a-z]+ \d{4}\.", html)
                assert "lantern_walk" not in html and "Lantern Walk" not in html
        forbidden_words(text(play.alice.get("/board")))
        assert play.alice.get("/challenges").status_code == 302 and play.alice.get("/challenges").headers["Location"].endswith("/board")
        with mock.patch.dict(page.HOOKS, {"redirect_challenges": False}):
            assert play.alice.get("/challenges").status_code == 403, "CTFd's own address keeps CTFd's gate"
        assert seen(play.app.test_client().get("/board"))[0] == 302, "a visitor is still sent to sign in"
        register_user(play.app, name="loner", email="loner@example.com", password=PASSWORD)
        loner = login_as_user(play.app, "loner", PASSWORD)
        assert seen(loner.get("/board"))[0] == 302 and "/team" in seen(loner.get("/board"))[1]


@pytest.mark.parametrize("state,banner", [("paused", "PAUSED."), ("ended", "THAT'S A WRAP.")])
def test_a_paused_or_ended_broadcast_shows_its_banner_on_the_board(play, state, banner):
    from CTFd.plugins.l3mon_core.clock import current_phase

    if state == "paused":
        set_config("paused", True)
    with clock(T_END + 60 if state == "ended" else T_LIVE):
        set_config("view_after_ctf", True)
        assert current_phase().state == state
        r = play.alice.get("/board")
        html = text(r)
    assert r.status_code == 200, html[:3000]
    assert f'data-banner="{"wrap" if state == "ended" else "paused"}"' in html and banner in htmllib.unescape(html)


def test_the_crew_sees_what_a_player_sees_with_nothing_solved_and_needs_no_studio(play):
    with clock(T_LIVE):
        r = play.admin.get("/board")
        html = text(r)
        deep = play.admin.get("/board/lantern_walk")
        held = play.admin.get("/board/hush_alley")
    assert r.status_code == 200 and "Mighty Street" in html and "solved by your studio" not in html
    assert deep.status_code == 200 and 'data-slug="lantern_walk"' in text(deep)
    assert held.status_code == 404, "the crew preview a withheld programme in the admin panel, not on the board"
    forbidden_words(html)


def test_the_gates_of_the_three_addresses_agree_in_every_state(play):
    """Whatever CTFd does with /challenges, /board and a programme's page do alike (same status, same destination)."""
    clients = {"visitor": play.app.test_client(), "alice": play.alice, "bob": play.bob}  # the crew are different on purpose: they need no studio (above)
    states = [("live", T_LIVE, {}), ("before", T_LIVE, {"start": T_LIVE + 7200}), ("ended-hidden", T_END + 60, {"view_after_ctf": False}), ("ended-open", T_END + 60, {"view_after_ctf": True})]
    for name, at, config in states:
        for key, value in {"start": T_START, "end": T_END, "view_after_ctf": False, **config}.items():
            set_config(key, value)
        with clock(at):
            for who, client in clients.items():
                one = seen(client.get("/challenges"))
                if name == "before":
                    if who != "visitor":
                        assert one == (302, "/board"), (name, who, "signed-in account on /challenges redirects 302 to /board")
                    continue  # the board shows its own page before the start (tested above); CTFd's address keeps its 403
                if who != "visitor":
                    assert one == (302, "/board"), (name, who, "signed-in account on /challenges redirects 302 to /board")
                    followed = seen(client.get("/challenges", follow_redirects=True))
                    assert followed == seen(client.get("/board")), (name, who, "/challenges followed agrees with /board")
                    continue
                assert seen(client.get("/board")) == one, (name, who, "/board")
                assert seen(client.get("/board/lantern_walk"))[0] in (one[0], 404), (name, who, "/board/<slug>")
                if one[0] != 200:
                    assert seen(client.get("/board/lantern_walk")) == one, (name, who, "/board/<slug>")


def test_a_withheld_everything_board_is_an_empty_frame_not_an_error(play):
    with clock(T_LIVE):
        withhold(play.admin, "lantern_walk", "moth_cipher", "noodle_ledger", "dumpling_gate", "coffee_break")
        r = play.alice.get("/board")
        html = text(r)
    assert r.status_code == 200 and "data-board" in html
    assert "0 programmes · 3 coming up" in html
    assert "Lantern Walk" not in html


def test_the_crawl_carries_public_news_only_and_is_off_when_there_is_none(play):
    from theme_a_world import add_notification, clear_news, team_client  # noqa: F401
    from CTFd.models import Teams

    with clock(T_LIVE):
        clear_news()
        assert 'data-crawl' not in text(play.alice.get("/board"))
        add_notification("Welcome", "The broadcast is on.")
        add_notification("Only studio B", "Private.", team_id=Teams.query.filter_by(name="studio-b").first().id)
        html = text(play.alice.get("/board"))
    assert 'data-crawl' in html and "Welcome: The broadcast is on." in html and "Only studio B" not in html
