"""The notifications page through a whole CTFd (SP6, stream A): /notifications is the theme's page for a player who may read the news, shows the
viewer's own list (never another studio's), and opens nothing the board plugin closes (the stock page for everyone else, the event stream, a
notification by its number).

Run through tools/run-ctfd-tests.sh (see docs/deploy/theme-slices.md):
    env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme/test_notifications_page.py
"""
import datetime
import html as htmllib
import re

import pytest

from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_core.models import Note
from CTFd.utils import set_config
from theme_a_world import (  # noqa: F401  (the fixture is used by name)
    PASSWORD, T_END, T_LIVE, add_notification, clear_news, clock, play, register_user, statements_of, text,
)
from tests.helpers import login_as_user

NOON = datetime.datetime(2026, 11, 28, 6, 0, 0)


def lines_of(html):
    """[(title, text)] in the order the page lists them."""
    return [(htmllib.unescape(t), htmllib.unescape(c)) for t, c in re.findall(r'<li class="nt-item"><b class="nt-t">(.*?)</b> <span class="nt-c">(.*?)</span></li>', html, re.S)]


def team_id(name):
    return Teams.query.filter_by(name=name).first().id


def test_putting_programmes_on_air_writes_a_line_every_player_can_read(play):
    with clock(T_LIVE):
        listed = lines_of(text(play.alice.get("/notifications")))
    assert [t for t, _ in listed] == ["New on air"] and "Mighty Street has 2 new programmes" in listed[0][1]


def test_a_player_with_no_news_gets_the_themes_quiet_page(play):
    with clock(T_LIVE):
        clear_news()
        r = play.alice.get("/notifications")
        html = text(r)
    assert r.status_code == 200
    assert 'id="nt-h"' in html and "Notifications" in html and "All quiet on the airwaves." in html
    assert 'class="nt-list"' not in html
    assert not re.search(r"prototype|mock\b|stand-in|DIRECTOR", html, re.I)
    assert not re.search(r"<script(?![^>]*\ssrc=)|\sstyle\s*=|\son[a-z]+\s*=", html, re.I)
    assert re.search(r'src="/themes/l3mon/static/shell\.[0-9a-f]{10}\.js"', html)


def test_a_player_reads_the_public_lines_and_the_ones_for_the_own_studio_newest_first_and_nobody_elses(play):
    with clock(T_LIVE):
        clear_news()
        add_notification("Welcome", "The broadcast is on.", when=NOON)
        add_notification("For studio A", "Only alice's studio.", team_id=team_id("studio-a"), when=NOON + datetime.timedelta(minutes=1))
        add_notification("For studio B", "Only bob's studio.", team_id=team_id("studio-b"), when=NOON + datetime.timedelta(minutes=2))
        db.session.add(Note(team_id=team_id("studio-a"), title="Bonus +50 TRP", text="Well played.", created_at=NOON + datetime.timedelta(minutes=3)))
        db.session.add(Note(team_id=team_id("studio-b"), title="Solve voided", text="Studio B's private line.", created_at=NOON + datetime.timedelta(minutes=4)))
        db.session.commit()
        alice, abe, bob = (lines_of(text(c.get("/notifications"))) for c in (play.alice, play.abe, play.bob))
    assert [t for t, _ in alice] == ["Bonus +50 TRP", "For studio A", "Welcome"]
    assert alice == abe, "a studio's players read the same news"
    assert [t for t, _ in bob] == ["Solve voided", "For studio B", "Welcome"]
    assert alice[0] == ("Bonus +50 TRP", "Well played."), "the text of a line is shown"


def test_the_page_lists_only_the_newest_fifty(play):
    with clock(T_LIVE):
        for i in range(1, 61):
            add_notification(f"News {i}", f"Line {i}.", when=NOON + datetime.timedelta(seconds=i))
        listed = lines_of(text(play.alice.get("/notifications")))
    assert len(listed) == 50
    assert listed[0] == ("News 60", "Line 60.") and listed[-1] == ("News 11", "Line 11.")


def test_what_a_crew_member_wrote_is_shown_as_text_never_as_markup(play):
    with clock(T_LIVE):
        clear_news()
        add_notification('<script>alert("x")</script>', '"><img src=x onerror=alert(1)> & <b>bold</b>', when=NOON)
        html = text(play.alice.get("/notifications"))
    assert "<script>alert" not in html and "<img src=x" not in html and "<b>bold</b>" not in html
    assert lines_of(html) == [('<script>alert("x")</script>', '"><img src=x onerror=alert(1)> & <b>bold</b>')]


def test_a_paused_broadcast_shows_its_banner_on_the_page(play):
    set_config("paused", True)
    with clock(T_LIVE):
        html = text(play.alice.get("/notifications"))
    assert 'data-banner="paused"' in html and "PAUSED." in html


def test_the_crew_read_every_notification_of_the_platform(play):
    with clock(T_LIVE):
        clear_news()
        add_notification("Public", "All.", when=NOON)
        add_notification("For studio A", "A.", team_id=team_id("studio-a"), when=NOON + datetime.timedelta(minutes=1))
        add_notification("For studio B", "B.", team_id=team_id("studio-b"), when=NOON + datetime.timedelta(minutes=2))
        r = play.admin.get("/notifications")
    assert r.status_code == 200
    assert {t for t, _ in lines_of(text(r))} == {"Public", "For studio A", "For studio B"}


def seen(response):
    return response.status_code, re.sub(r"next=[^&]*", "next=X", response.headers.get("Location") or "") or None


def test_every_gate_of_the_player_pages_applies_to_the_notifications_page(play):
    set_config("verify_emails", True)
    with clock(T_LIVE):
        assert seen(play.app.test_client().get("/notifications")) == (302, "/login?next=X")
        user = Users.query.filter_by(name="bob").first()
        user.verified = False
        db.session.commit()
        assert seen(play.bob.get("/notifications")) == (302, "/confirm")
        user.verified = True
        user.banned = True
        db.session.commit()
        assert play.bob.get("/notifications").status_code == 403


def test_a_player_with_no_studio_is_sent_to_make_one(play):
    with clock(T_LIVE):
        register_user(play.app, name="loner", email="loner@example.com", password=PASSWORD)
        loner = login_as_user(play.app, "loner", PASSWORD)
        status, where = seen(loner.get("/notifications"))
    assert status == 302 and where.startswith("/team")


def test_nothing_else_the_board_plugin_closes_is_opened(play):
    with clock(T_LIVE):
        clear_news()
        add_notification("One", "Line.", when=NOON)
        visitor = play.app.test_client()
        for who in (visitor, play.alice):
            assert who.get("/events").status_code in (404, 302, 403), "the event stream stays closed"
            assert who.get("/notifications/").status_code == 404
            assert who.get("/api/v1/notifications/1").status_code in (404, 401, 403)
        assert play.alice.post("/notifications", data={}).status_code in (404, 405, 400, 403)
        api = play.alice.get("/api/v1/notifications?since_id=0")
        assert api.status_code == 200 and [n["title"] for n in api.get_json()["data"]] == ["One"], "the bell's feed is as it was"
        assert set(api.get_json()["data"][0]) == {"id", "title", "content"}
        assert play.alice.head("/notifications").status_code == 200
        assert play.alice.get("/notifications?since_id=3&x=<script>").status_code == 200
        assert "<script>" not in text(play.alice.get("/notifications?x=<script>"))


def test_the_page_costs_a_handful_of_statements_whatever_the_number_of_lines(play):
    def ask():
        return play.alice.get("/notifications")

    with clock(T_LIVE):
        ask()
        _, few = statements_of(ask)
        for i in range(40):
            add_notification(f"News {i}", "Line.", when=NOON + datetime.timedelta(seconds=i))
        ask()
        _, many = statements_of(ask)
    assert len(many) <= len(few) + 2, (len(few), len(many))
    assert len(many) <= 22, len(many)
