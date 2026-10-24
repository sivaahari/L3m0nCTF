"""The channels board and the notifications page on the real stack (SP6, stream A): nginx, gunicorn, MariaDB, Redis.

The plugin tests prove the rules through a whole CTFd on SQLite. This proves the same pages arrive over HTTP through nginx, with the stack's own
settings, for a made-up studio on a live event window: the theme's board and programme page, a held-back programme that is a missing page, the
bell's page, the files the pages name, and the gates for a visitor.

Same prerequisites and runner as test_board.py, whose world (an event window around the present, one channel, three programmes, two studios) it
uses as it is. Needs the image rebuilt after the theme was exported (docs/deploy/theme-slices.md).

Run:  L3MON_TEST_PORT=8080 python -m pytest tests/integration/test_theme_a.py -q -p no:randomly
"""
from __future__ import annotations

import re

import pytest

import test_stack as t
from test_board import world  # noqa: F401  (the module-scoped fixture is used by name)

pytestmark = pytest.mark.integration

FORBIDDEN = re.compile(r"prototype|mock\b|stand-in|DIRECTOR", re.I)


def page(session, path):
    response = session.open(path)
    return response.status, dict(response.headers.items()), response.read().decode("utf-8", "replace")


def clean(html):
    assert not re.search(r"<script(?![^>]*\ssrc=)", html, re.I), "an inline script"
    assert not re.search(r"\sstyle\s*=|\son[a-z]+\s*=", html, re.I), "a style or event attribute"
    assert "javascript:" not in html.lower()
    assert not FORBIDDEN.search(html), "prototype wording"


def test_01_a_player_gets_the_themes_board_through_nginx(world):
    status, headers, html = page(world.players[0], "/board")
    assert status == 200
    assert 'data-board' in html and "Marsh Channel" in html and "CH 03" in html and 'class="board ch-3"' in html
    assert "2 programmes · 1 coming up" in html, "two programmes are on air; the held-back one is only counted"
    assert "Egret" not in html and "egret_secret" not in html
    clean(html)
    for name in ("X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy", "Permissions-Policy", "Cross-Origin-Resource-Policy"):
        assert len([k for k in headers if k.lower() == name.lower()]) == 1, f"{name} is sent once"
    assert headers.get("Cache-Control", "").lower().find("public") < 0, "a page about one studio is never public"


def test_02_the_challenge_address_draws_the_same_board(world):
    _, _, a = page(world.players[0], "/board")
    status, _, b = page(world.players[0], "/challenges")
    assert status == 200 and re.sub(r"\s+", " ", a) == re.sub(r"\s+", " ", b)


def test_03_the_files_the_board_names_are_served(world):
    _, _, html = page(world.players[0], "/board")
    links = sorted(set(re.findall(r'(?:href|src)="(/themes/l3mon/static/[^"]+)"', html)))
    assert any("/board." in link for link in links) and any("/app." in link for link in links) and any("/boot." in link for link in links), links
    for link in links:
        status, _, _ = t.request(link)
        assert status == 200, link
    status, _, css = t.request([l for l in links if "/app." in l][0])
    art = sorted(set(re.findall(r'url\("(/art/ch3\.[a-z]+\.[0-9a-f]{10}\.svg)"\)', css.decode("utf-8", "replace"))))
    assert len(art) == 2, art  # the clear and the soft sheet of channel 3
    for link in art:
        status, headers, _ = t.request(link)
        assert status == 200 and t.header_values(headers, "Content-Type")[0].startswith("image/svg+xml"), link


def test_04_a_programme_has_its_own_page_and_a_held_back_one_is_missing_like_an_unknown_one(world):
    status, _, html = page(world.players[0], "/board/heron_ledger")
    assert status == 200 and 'data-slug="heron_ledger"' in html and "<title>Heron Ledger | Marsh Channel | Channels | " in html
    clean(html)
    held, unknown = page(world.players[0], "/board/egret_secret"), page(world.players[0], "/board/never_existed")
    assert held[0] == unknown[0] == 404
    squash = lambda body, slug: re.sub(r"\s+", " ", body.replace(slug, "SLUG"))  # noqa: E731
    assert squash(held[2], "egret_secret") == squash(unknown[2], "never_existed")
    assert "Egret" not in held[2]


def test_05_a_visitor_is_sent_to_sign_in(world):
    for path in ("/board", "/challenges", "/board/heron_ledger", "/notifications"):
        status, headers, _ = t.request(path)
        assert status == 302 and t.header_values(headers, "Location")[0].startswith("/login"), (path, status)


def test_06_the_bells_page_and_the_bells_feed(world):
    status, _, html = page(world.players[0], "/notifications")
    assert status == 200 and 'id="nt-h"' in html and "Marsh Channel has 2 new programmes" in html.replace("&middot;", "·")
    clean(html)
    status, headers, body = page(world.players[0], "/api/v1/notifications?since_id=0")
    assert status == 200 and '"success":true' in body.replace(" ", "")
    assert t.request("/events")[0] != 200, "the event stream is still closed to a visitor"


def test_07_the_second_studio_reads_its_own_news_and_not_the_first_ones(world):
    a = page(world.players[0], "/notifications")[2]
    b = page(world.players[1], "/notifications")[2]
    assert "boardplayer1" not in b and "boardplayer2" not in a


SENTENCE = "Flags and hints need JavaScript. Turn it on in your browser to send a flag or open a hint."


def test_08_a_programmes_page_without_script_carries_its_task_and_the_one_sentence_and_no_form(world):
    status, _, html = page(world.players[0], "/board/heron_ledger")
    assert status == 200
    ns = html[html.index('id="pn-ns"'):]
    ns = ns[: ns.index("</article>")]
    assert "plain words" in ns, "the crew's task text, as plain paragraphs"
    assert SENTENCE in ns and "<form" not in ns and 'type="hidden"' not in ns
    assert "In the story" not in ns and "Heron Ledger" in ns and "P301" in ns and "CH 3" in ns.replace("&middot;", "·")
    assert "Egret" not in html and "egret_secret" not in html
    clean(html)


def test_09_a_live_programme_without_the_instancer_says_it_is_not_running(world):
    status, _, html = page(world.players[0], "/board/ibis_lantern")
    assert status == 200
    ns = html[html.index('id="pn-ns"'):]
    assert "Your instance" in ns and "Not running." in ns and "Your studio has 0 of 3 instances running." in ns
    assert "need JavaScript" in ns
    clean(html)


def test_10_the_programme_panels_replies_have_what_the_script_reads(world):
    """The panel's script asks for a programme by its number, never its slug (CTFd answers only to the number)."""
    status, _, body = ask_json(world.players[0], "/api/v1/l3mon/board")
    programmes = {p["slug"]: p for p in body["data"]["programmes"]}
    heron = programmes["heron_ledger"]["id"]
    status, _, detail = ask_json(world.players[0], f"/api/v1/challenges/{heron}")
    assert status == 200 and detail["data"]["description"] == "plain words" and detail["data"]["l3mon"]["slug"] == "heron_ledger"
    for key in ("name", "value", "category", "solved_by_me", "solves", "hints", "files"):
        assert key in detail["data"], key
    assert ask_json(world.players[0], "/api/v1/challenges/heron_ledger")[0] == 404, "the slug is not an address of CTFd's"
    status, _, listing = ask_json(world.players[0], "/api/v1/l3mon/instances")
    assert status == 200 and listing["data"]["instances"] == [] and listing["data"]["limits"]["max"] == 3


def ask_json(session, path):
    import json

    response = session.open(path)
    raw = response.read()
    try:
        return response.status, dict(response.headers.items()), json.loads(raw)
    except ValueError:
        return response.status, dict(response.headers.items()), None
