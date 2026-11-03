"""Integration tests for Piece E: story pieces, automatic time, and rate-limit slate.

Tests the full stack through nginx:
- E1: programme detail API carries l3mon.story, withheld programme is 404, noscript page shows story with escaping,
  admin plan call accepts/refuses the right lines, and board/guide/list never carry the story line.
- E2: Stories page (/stories) access gating (visitors, players without a studio), cards list unlocked channel
  cold opens, empty state when nothing on air, link back to Guide, and clean CSP.
- E3: Finale automatic time (l3mon_finale_at) opens the finale story and reflects on /stories.
- E4: Rate-limited login request with real browser Accept header gets themed HTML slate; application/json gets raw JSON 429.
"""
from __future__ import annotations

import json
import re
import secrets as pysecrets
import shutil
import time
from types import SimpleNamespace

import pytest

import test_release as r
import test_stack as t

pytestmark = pytest.mark.integration

FOLDER = t.ROOT / "tests" / "integration" / "fixtures" / "story-pieces-run"
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 90"><rect width="160" height="90" fill="#223"/><circle cx="80" cy="45" r="20" fill="#fc3"/></svg>'
FIRST = {"name": "Quokka Piece", "flag": "quokka-answer-piece", "value": 150}
SECOND = {"name": "Numbat Piece", "flag": "numbat-answer-piece", "value": 250}
FORBIDDEN = re.compile(r"prototype|mock\b|stand-in|DIRECTOR", re.I)
ACCEPT_BROWSER = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def bundle(slug: str, title: str, panels: int = 3, kicker: str | None = None) -> dict:
    return {
        "v": 1,
        "slug": slug,
        "title": title,
        "kicker": kicker or f"CH 01 · {slug.title()}",
        "lang": "en",
        "panels": [
            {
                "id": f"p{i + 1}",
                "ms": 4000,
                "enter": "static",
                "alt": f"Panel {i + 1}: a round sun.",
                "layers": [{"art": "sun", "x": 0, "y": 0, "w": 100, "h": 100}],
                "bubbles": [
                    {
                        "kind": "say",
                        "who": "Host",
                        "text": f"Line {i + 1}.",
                        "x": 6,
                        "y": 6,
                        "w": 36,
                        "tail": "bl",
                        "at": 300,
                    }
                ],
            }
            for i in range(panels)
        ],
        "art": {"sun": SVG},
    }


def clean(html: str) -> None:
    assert not re.search(r"<script(?![^>]*\ssrc=)", html, re.I), "an inline script"
    assert not re.search(r"\sstyle\s*=|\son[a-z]+\s*=", html, re.I), "a style or event attribute"
    assert "javascript:" not in html.lower()
    assert not FORBIDDEN.search(html), "prototype wording"


def ask(session: t.Session, path: str, headers: dict | None = None):
    response = session.open(path, headers=headers)
    raw = response.read()
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None
    return response.status, dict(response.headers.items()), body, raw


def page(session: t.Session, path: str, headers: dict | None = None):
    response = session.open(path, headers=headers)
    return response.status, dict(response.headers.items()), response.read().decode("utf-8", "replace")


def all_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from all_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_keys(item)


def make_user_without_team(name: str):
    password = pysecrets.token_urlsafe(18)
    status, out = r.admin(
        "POST",
        "/api/v1/users",
        {"name": name, "email": f"{name}@example.com", "password": password, "type": "user", "verified": True},
    )
    assert status == 200, out
    user_id = out["data"]["id"]
    session = t.Session()
    answer = session.open(
        "/login",
        {"name": name, "password": password, "_submit": "Submit", "nonce": session.nonce("/login")},
    )
    assert answer.status == 302, f"sign-in answered {answer.status}"
    return session, user_id


def purge():
    for name in ("pieceplayer", "piecesolo", "pieceplayer-team"):
        for path in ("/api/v1/teams", "/api/v1/users"):
            status, out = r.admin("GET", f"{path}?view=admin&field=name&q={name}")
            for row in (out["data"] if status == 200 else []):
                if row["name"] == name:
                    r.admin("DELETE", f"{path}/{row['id']}")
    for spec in (FIRST, SECOND):
        status, out = r.admin("GET", f"/api/v1/challenges?view=admin&field=name&q={spec['name'].split()[0]}")
        for row in (out["data"] if status == 200 else []):
            if row["name"] == spec["name"]:
                r.admin("DELETE", f"/api/v1/challenges/{row['id']}")


def recreate(env: dict | None = None):
    t.compose("up", "-d", "--wait", "--force-recreate", "--no-deps", "ctfd", timeout=400, env=env)
    t.wait_healthy("ctfd", 180)


@pytest.fixture(scope="module")
def world():
    original = r.PRESET.read_bytes()
    window = json.loads(original)
    now = int(time.time())
    window["start"], window["end"] = now - 3600, now + 7200
    r.PRESET.write_bytes((json.dumps(window, separators=(",", ":")) + "\n").encode("utf-8"))
    shutil.rmtree(FOLDER, ignore_errors=True)
    FOLDER.mkdir(parents=True)
    (FOLDER / "lagoon.json").write_text(json.dumps(bundle("lagoon", "Lagoon Marker Title", 3)), encoding="utf-8")
    (FOLDER / "reef.json").write_text(json.dumps(bundle("reef", "Reef Secret Title", 2)), encoding="utf-8")
    (FOLDER / "finale.json").write_text(json.dumps(bundle("finale", "Finale Secret Title", 2, kicker="LAST CALL")), encoding="utf-8")
    made = SimpleNamespace()
    try:
        recreate({"L3MON_STORY_SRC": FOLDER.as_posix()})
        purge()
        first = r.make_challenge(FIRST)
        second = r.make_challenge(SECOND)
        made.challenges = [first, second]
        status, out = r.admin(
            "PUT",
            r.PROGRAMMES,
            {
                "channels": [
                    {"slug": "lagoon", "name": "Lagoon", "position": 1},
                    {"slug": "reef", "name": "Reef", "position": 2},
                ],
                "programmes": [
                    {
                        "challenge_id": first,
                        "channel": "lagoon",
                        "cell": 0,
                        "number": 101,
                        "slug": "quokka_piece",
                        "story": "A curious quokka wanders the stream.",
                    },
                    {
                        "challenge_id": second,
                        "channel": "reef",
                        "cell": 0,
                        "number": 201,
                        "slug": "numbat_piece",
                        "story": "A hidden numbat watches from afar.",
                    },
                ],
            },
        )
        assert status == 200, out
        status, view = r.admin("GET", r.RELEASE)
        by_slug = {c["slug"]: c for c in view["data"]["channels"]}
        lagoon = by_slug["lagoon"]
        r.change(
            {"kind": "channel", "id": lagoon["id"], "mode": "release"},
            {"kind": "programme", "id": lagoon["programmes"][0]["id"], "mode": "release"},
        )
        made.player, made.player_id, made.team_id = r.make_player("pieceplayer")
        made.solo_player, made.solo_id = make_user_without_team("piecesolo")
        yield made
    finally:
        try:
            r.admin("PATCH", "/api/v1/configs", {"l3mon_finale_at": "", "l3mon_finale_open": False})
            purge()
            r.in_ctfd(r.CLEANUP)
        finally:
            r.PRESET.write_bytes(original)
            shutil.rmtree(FOLDER, ignore_errors=True)
            recreate()


def test_01_e1_programme_detail_and_isolation(world):
    # Programme detail carries story
    status, _, body, _ = ask(world.player, f"/api/v1/challenges/{world.challenges[0]}")
    assert status == 200
    assert body["data"]["l3mon"]["story"] == "A curious quokka wanders the stream."

    # Withheld programme is 404
    status, _, _, _ = ask(world.player, f"/api/v1/challenges/{world.challenges[1]}")
    assert status == 404

    # Noscript page shows story line and escapes markup
    status, _, html = page(world.player, "/board/quokka_piece")
    assert status == 200
    assert "A curious quokka wanders the stream." in html
    clean(html)

    # Withheld noscript page is 404
    status, _, _ = page(world.player, "/board/numbat_piece")
    assert status == 404

    # Isolation: story line never in board, guide, or story list
    status, _, board, raw_board = ask(world.player, "/api/v1/l3mon/board")
    assert status == 200
    assert "story" not in set(all_keys(board))
    assert b"A curious quokka" not in raw_board

    status, _, guide_html = page(world.player, "/guide")
    assert "A curious quokka" not in guide_html

    status, _, _, raw_story = ask(world.player, "/api/v1/l3mon/story")
    assert b"A curious quokka" not in raw_story

    # Admin plan updates: field update, omit keeps, empty clears, validation
    # 1. Update
    status, out = r.admin(
        "PUT",
        r.PROGRAMMES,
        {
            "channels": [{"slug": "lagoon", "name": "Lagoon", "position": 1}],
            "programmes": [
                {
                    "challenge_id": world.challenges[0],
                    "channel": "lagoon",
                    "cell": 0,
                    "number": 101,
                    "slug": "quokka_piece",
                    "story": "Quokka update line.",
                },
            ],
        },
    )
    assert status == 200, out
    status, _, body, _ = ask(world.player, f"/api/v1/challenges/{world.challenges[0]}")
    assert body["data"]["l3mon"]["story"] == "Quokka update line."

    # 2. Field not sent leaves line alone
    status, out = r.admin(
        "PUT",
        r.PROGRAMMES,
        {
            "channels": [{"slug": "lagoon", "name": "Lagoon", "position": 1}],
            "programmes": [
                {
                    "challenge_id": world.challenges[0],
                    "channel": "lagoon",
                    "cell": 0,
                    "number": 101,
                    "slug": "quokka_piece",
                },
            ],
        },
    )
    assert status == 200, out
    status, _, body, _ = ask(world.player, f"/api/v1/challenges/{world.challenges[0]}")
    assert body["data"]["l3mon"]["story"] == "Quokka update line."

    # 3. Empty string clears it
    status, out = r.admin(
        "PUT",
        r.PROGRAMMES,
        {
            "channels": [{"slug": "lagoon", "name": "Lagoon", "position": 1}],
            "programmes": [
                {
                    "challenge_id": world.challenges[0],
                    "channel": "lagoon",
                    "cell": 0,
                    "number": 101,
                    "slug": "quokka_piece",
                    "story": "",
                },
            ],
        },
    )
    assert status == 200, out
    status, _, body, _ = ask(world.player, f"/api/v1/challenges/{world.challenges[0]}")
    assert body["data"]["l3mon"]["story"] == ""

    # 4. Refuses markup and overlong
    status, _ = r.admin(
        "PUT",
        r.PROGRAMMES,
        {
            "channels": [{"slug": "lagoon", "name": "Lagoon", "position": 1}],
            "programmes": [
                {
                    "challenge_id": world.challenges[0],
                    "channel": "lagoon",
                    "cell": 0,
                    "number": 101,
                    "slug": "quokka_piece",
                    "story": "<script>alert(1)</script>",
                },
            ],
        },
    )
    assert status == 400

    status, _ = r.admin(
        "PUT",
        r.PROGRAMMES,
        {
            "channels": [{"slug": "lagoon", "name": "Lagoon", "position": 1}],
            "programmes": [
                {
                    "challenge_id": world.challenges[0],
                    "channel": "lagoon",
                    "cell": 0,
                    "number": 101,
                    "slug": "quokka_piece",
                    "story": "x" * 281,
                },
            ],
        },
    )
    assert status == 400

    # Restore original story line
    r.admin(
        "PUT",
        r.PROGRAMMES,
        {
            "channels": [{"slug": "lagoon", "name": "Lagoon", "position": 1}],
            "programmes": [
                {
                    "challenge_id": world.challenges[0],
                    "channel": "lagoon",
                    "cell": 0,
                    "number": 101,
                    "slug": "quokka_piece",
                    "story": "A curious quokka wanders the stream.",
                },
            ],
        },
    )


def test_02_e2_stories_page_gating(world):
    # Signed out visitor redirected to login
    status, headers, _ = t.request("/stories")
    assert status in (302, 303)
    loc = t.header_values(headers, "Location")[0]
    assert "/login" in loc and "next=" in loc

    # Signed in without team redirected to team creation
    status, headers, _ = page(world.solo_player, "/stories")
    assert status in (302, 303)
    loc = headers.get("Location", "")
    assert "/team" in loc

    # Signed in with team gets 200 with clean CSP and link to Guide
    status, _, html = page(world.player, "/stories")
    assert status == 200
    clean(html)
    assert 'href="/guide"' in html, "Stories page must contain link back to Guide"


def test_03_e2_stories_page_content_and_empty_state(world):
    status, _, html = page(world.player, "/stories")
    assert status == 200
    assert "Lagoon Marker Title" in html
    assert "CH 01" in html
    assert "Watch the cold open" in html
    assert "/story/lagoon" in html
    assert "Reef Secret Title" not in html
    assert "Finale Secret Title" not in html

    # Withholding channel shows empty state
    status, view = r.admin("GET", r.RELEASE)
    lagoon = next(c for c in view["data"]["channels"] if c["slug"] == "lagoon")
    r.change({"kind": "channel", "id": lagoon["id"], "mode": "withhold"})
    try:
        status, _, empty_html = page(world.player, "/stories")
        assert status == 200
        assert "Nothing to watch yet. The comics open as the channels go on air." in empty_html
    finally:
        r.change({"kind": "channel", "id": lagoon["id"], "mode": "release"})


def test_04_e3_finale_automatic_time(world):
    # Initially closed
    assert ask(world.player, "/api/v1/l3mon/story/finale")[0] == 404
    assert "Finale Secret Title" not in page(world.player, "/stories")[2]

    # Future timestamp: still closed
    future = int(time.time()) + 3600
    status, out = r.admin("PATCH", "/api/v1/configs", {"l3mon_finale_at": future})
    assert status == 200, out
    assert ask(world.player, "/api/v1/l3mon/story/finale")[0] == 404
    assert "Finale Secret Title" not in page(world.player, "/stories")[2]

    # Past timestamp: opens finale
    past = int(time.time()) - 60
    status, out = r.admin("PATCH", "/api/v1/configs", {"l3mon_finale_at": past})
    assert status == 200, out
    status, _, body, _ = ask(world.player, "/api/v1/l3mon/story/finale")
    assert status == 200 and body["data"]["title"] == "Finale Secret Title"

    status, _, html = page(world.player, "/stories")
    assert status == 200
    assert "Finale Secret Title" in html
    assert "Watch the finale" in html
    clean(html)

    # Reset
    r.admin("PATCH", "/api/v1/configs", {"l3mon_finale_at": "", "l3mon_finale_open": False})
    assert ask(world.player, "/api/v1/l3mon/story/finale")[0] == 404


def test_05_e4_themed_rate_limit_slate_and_json(world):
    session = t.Session()
    nonce = session.nonce("/login")
    hit = None
    for _ in range(15):
        ans = session.open(
            "/login",
            {"name": "nonexistent_user", "password": "wrong_password", "_submit": "Submit", "nonce": nonce},
            headers={"Accept": ACCEPT_BROWSER},
        )
        if ans.status == 429:
            hit = ans
            break
    assert hit is not None, "rate limit 429 was never triggered on /login"
    assert hit.status == 429
    assert any(k.lower() == "retry-after" for k in hit.headers)
    html = hit.read().decode("utf-8", "replace")
    assert "Too many requests" in html or "Wait a moment before trying again" in html
    clean(html)

    # Accept: application/json returns raw CTFd JSON
    json_ans = session.open(
        "/login",
        {"name": "nonexistent_user", "password": "wrong_password", "_submit": "Submit", "nonce": nonce},
        headers={"Accept": "application/json"},
    )
    assert json_ans.status == 429
    raw_json = json_ans.read().decode("utf-8", "replace")
    parsed = json.loads(raw_json)
    assert parsed.get("code") == 429
    assert "Too many requests" in parsed.get("message", "")
    assert "<!DOCTYPE html>" not in raw_json

    # Restart nginx so rate-limit counters are reset for subsequent tests
    t.compose("restart", "nginx")
    t.wait_healthy("nginx", 60)
