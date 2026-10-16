"""The cold-open comic on the real stack (SP3's story part): nginx, gunicorn with four gevent workers, MariaDB and Redis.

The plugin tests prove the gate and the format. This proves it end to end over HTTP: the folder of story files is mounted read-only into the
platform, a visitor is told to sign in (API) or sent to registration (page), a signed-in player gets the story of a channel that is on air and
nothing about one that is not, the board carries the panel count, the player's own files are served with the right type, and nobody can write.
The channel on air (`lagoon`) is a painted story (format version 2: its pictures are WebP files in `pics/lagoon/`), the other one (`reef`) is a
drawn one (version 1), and there is a finale that opens only when the crew says so. The pictures are made by hand (a WebP header and some padding):
the platform checks size, checksum and header, it does not decode them.

Same prerequisites and runner as test_stack.py. Like test_release.py it renders an event window around the present moment into the generated
settings and restarts CTFd; it also points the platform at a folder of two made-up stories, and puts everything back at the end.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import time
from types import SimpleNamespace

import pytest

import test_release as r
import test_stack as t

pytestmark = pytest.mark.integration

STORY = "/api/v1/l3mon/story"
BOARD = "/api/v1/l3mon/board"
FOLDER = t.ROOT / "tests" / "integration" / "fixtures" / "story-run"
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 90"><rect width="160" height="90" fill="#223"/><circle cx="80" cy="45" r="20" fill="#fc3"/></svg>'
ON = {"name": "Egret Mile", "flag": "egret-answer-one", "value": 120}
OFF = {"name": "Teal Quay", "flag": "teal-answer-two", "value": 220}
MARKER = "Lagoon Marker Title"


def bundle(slug, title, panels=3):
    return {
        "v": 1, "slug": slug, "title": title, "kicker": f"CH 09 · {slug.title()}", "lang": "en",
        "panels": [{
            "id": f"p{i + 1}", "ms": 4000, "enter": "static", "alt": f"Panel {i + 1}: a round sun.",
            "layers": [{"art": "sun", "x": 0, "y": 0, "w": 100, "h": 100}],
            "bubbles": [{"kind": "say", "who": "Host", "text": f"Line {i + 1}.", "x": 6, "y": 6, "w": 36, "tail": "bl", "at": 300}],
        } for i in range(panels)],
        "art": {"sun": SVG},
    }


def webp(n):
    """A file that has the shape of a WebP (RIFF header, a lossless chunk) and a body that differs for each n. The platform does not decode it, but it
    walks the chunks, so the chunk has an even length like every real one (an odd one needs a pad byte)."""
    body = b"\x2f" + struct.pack("<I", (639) | (359 << 14)) + bytes([n]) * 201
    chunk = b"VP8L" + struct.pack("<I", len(body)) + body
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def painted(slug, title, files, kicker=None):
    return {
        "v": 2, "slug": slug, "title": title, "kicker": kicker or f"CH 09 · {slug.title()}", "lang": "en", "flip": True,
        "panels": [{
            "id": f"p{n}", "ms": 9000, "enter": "static", "alt": f"Panel {n}: a round sun.",
            "layers": [{"pic": f"p{n}", "x": 0, "y": 0, "w": 100, "h": 100}],
            "lines": [{"kind": "say", "who": "Host", "text": f"Line {n}."}],
        } for n in range(1, len(files) + 1)],
        "pics": {f"p{n}": {"w": 640, "h": 360, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for n, data in enumerate(files, start=1)},
    }


def put_painted(slug, title, count, **kw):
    files = [webp(n) for n in range(1, count + 1)]
    (FOLDER / f"{slug}.json").write_text(json.dumps(painted(slug, title, files, **kw)), encoding="utf-8")
    (FOLDER / "pics" / slug).mkdir(parents=True)
    for n, data in enumerate(files, start=1):
        (FOLDER / "pics" / slug / f"p{n}.webp").write_bytes(data)
    return files


def purge():
    for path, value in (("/api/v1/teams", "storyplayer-team"), ("/api/v1/users", "storyplayer")):
        status, out = r.admin("GET", f"{path}?field=name&q={value}")
        for row in (out["data"] if status == 200 else []):
            if row["name"] == value:
                r.admin("DELETE", f"{path}/{row['id']}")
    for spec in (ON, OFF):
        status, out = r.admin("GET", f"/api/v1/challenges?view=admin&field=name&q={spec['name'].split()[0]}")
        for row in (out["data"] if status == 200 else []):
            if row["name"] == spec["name"]:
                r.admin("DELETE", f"/api/v1/challenges/{row['id']}")


def recreate(env=None):
    t.compose("up", "-d", "--wait", "--force-recreate", "--no-deps", "ctfd", timeout=400, env=env)
    t.wait_healthy("ctfd", 180)


def ask(session, path, headers=None):
    response = session.open(path, headers=headers)
    raw = response.read()
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None
    return response.status, dict(response.headers.items()), body, raw


@pytest.fixture(scope="module")
def world():
    original = r.PRESET.read_bytes()
    window = json.loads(original)
    now = int(time.time())
    window["start"], window["end"] = now - 3600, now + 7200
    r.PRESET.write_bytes((json.dumps(window, separators=(",", ":")) + "\n").encode("utf-8"))
    shutil.rmtree(FOLDER, ignore_errors=True)
    FOLDER.mkdir(parents=True)
    made = SimpleNamespace(files=put_painted("lagoon", MARKER, 3), finale_files=put_painted("finale", "Finale Secret Title", 2, kicker="LAST CALL"))
    (FOLDER / "reef.json").write_text(json.dumps(bundle("reef", "Reef Secret Title", 2)), encoding="utf-8")
    try:
        recreate({"L3MON_STORY_SRC": FOLDER.as_posix()})
        purge()
        on, off = (r.make_challenge(spec) for spec in (ON, OFF))
        status, out = r.admin("PUT", r.PROGRAMMES, {
            "channels": [{"slug": "lagoon", "name": "Lagoon", "position": 9}, {"slug": "reef", "name": "Reef", "position": 10}],
            "programmes": [
                {"challenge_id": on, "channel": "lagoon", "cell": 0, "number": 901, "slug": "egret_mile"},
                {"challenge_id": off, "channel": "reef", "cell": 0, "number": 1001, "slug": "teal_quay"},
            ],
        })
        assert status == 200, out
        status, view = r.admin("GET", r.RELEASE)
        by_slug = {c["slug"]: c for c in view["data"]["channels"]}
        lagoon = by_slug["lagoon"]
        r.change({"kind": "channel", "id": lagoon["id"], "mode": "release"}, {"kind": "programme", "id": lagoon["programmes"][0]["id"], "mode": "release"})
        made.player, _, _ = r.make_player("storyplayer")
        yield made
    finally:
        try:
            r.admin("PATCH", "/api/v1/configs", {"l3mon_finale_open": False})
            purge()
            r.in_ctfd(r.CLEANUP)
        finally:
            r.PRESET.write_bytes(original)
            shutil.rmtree(FOLDER, ignore_errors=True)
            recreate()


def test_01_a_visitor_is_told_to_sign_in_on_the_api_and_sent_to_registration_on_the_page(world):
    for path in (STORY, f"{STORY}/lagoon", f"{STORY}/reef", f"{STORY}/nothing-here"):
        status, _, data = t.request(path)
        assert status == 401 and json.loads(data)["error"] == "auth_required", path
    status, headers, _ = t.request("/story/lagoon")
    assert status == 303 and t.header_values(headers, "Location") == ["/register?next=/story/lagoon"], (status, headers)
    status, headers, _ = t.request("/story/reef")
    assert status == 303 and t.header_values(headers, "Location") == ["/register?next=/story/reef"], "the same for every channel"


def test_02_a_player_gets_the_story_of_the_channel_on_air_with_an_etag_and_a_304(world):
    status, headers, body, _ = ask(world.player, f"{STORY}/lagoon")
    assert status == 200 and body["data"]["title"] == MARKER and len(body["data"]["panels"]) == 3
    assert headers["Cache-Control"] == "private, no-cache" and headers["ETag"].startswith('"s') and "Cookie" in headers.get("Vary", "")
    status, again, _, raw = ask(world.player, f"{STORY}/lagoon", {"If-None-Match": headers["ETag"]})
    assert status == 304 and raw == b"" and again.get("ETag") == headers["ETag"]


def test_03_a_channel_that_is_not_on_air_an_unknown_one_and_a_bad_name_give_the_same_404(world):
    answers = []
    for slug in ("reef", "nothing-here", "Lagoon"):
        status, _, body, _ = ask(world.player, f"{STORY}/{slug}")
        assert status == 404, slug
        body.pop("request_id")
        answers.append(json.dumps(body, sort_keys=True))
    assert len(set(answers)) == 1 and "Reef Secret" not in "".join(answers)


def test_04_the_list_and_the_board_agree_and_carry_nothing_else(world):
    status, _, body, raw = ask(world.player, STORY)
    assert status == 200 and [s["slug"] for s in body["data"]["stories"]] == ["lagoon"] and b"Reef Secret" not in raw
    status, _, board, raw = ask(world.player, BOARD)
    assert status == 200
    channels = {c["slug"]: c for c in board["data"]["channels"]}
    assert channels["lagoon"]["cold_open"] == {"panels": 3} and channels["reef"]["cold_open"] is None
    assert b"Reef Secret" not in raw and b"Lagoon Marker" not in raw

    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    assert "story" not in set(keys(board)), "the board never carries a field called story (the contract); a studio may be named anything, so the keys are what is checked"


def test_05_the_page_for_a_player_holds_the_words_as_text_and_loads_only_its_own_files(world):
    status, headers, _, raw = ask(world.player, "/story/lagoon")
    html = raw.decode("utf-8")
    assert status == 200 and MARKER in html and "Panel 1: a round sun." in html and 'src="/plugins/l3mon_story/assets/comic.js"' in html
    assert "nosniff" in headers.get("X-Content-Type-Options", "") and headers.get("X-Frame-Options") == "DENY"
    status, _, _, _ = ask(world.player, "/story/reef")
    assert status == 404


def test_06_the_players_files_are_public_engine_code_with_the_right_types_and_no_story(world):
    for name, mime in (("comic.js", "javascript"), ("timeline.js", "javascript"), ("sounds.js", "javascript"), ("comic.css", "text/css")):
        status, headers, raw = t.request(f"/plugins/l3mon_story/assets/{name}")
        assert status == 200 and mime in t.header_values(headers, "Content-Type")[0] and b"Lagoon Marker" not in raw, name
        assert t.header_values(headers, "X-Content-Type-Options") == ["nosniff"]
    status, _, _ = t.request("/plugins/l3mon_story/assets/../store.py")
    assert status in (400, 404)


def test_07_nothing_here_writes_and_the_folder_is_read_only_inside_the_platform(world):
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        status, _, _ = t.request(f"{STORY}/lagoon", method=method, headers={"Content-Type": "application/json"}, body=b"{}")
        assert status in (401, 403, 404, 405), (method, status)
    out = t.compose("exec", "-T", "ctfd", "sh", "-c", "touch /var/story/x 2>&1; echo $?", check=False).stdout
    assert out.strip().splitlines()[-1] != "0", "the story folder is mounted read-only"


# ---- the painted pictures and the finale ---------------------------------------------------------------------------------------------------

def test_08_a_visitor_is_told_to_sign_in_for_every_picture_and_a_player_gets_only_the_pictures_of_a_channel_on_air(world):
    for path in ("lagoon/pic/p1", "reef/pic/p1", "finale/pic/p1", "nothing-here/pic/p1", "lagoon/pic/p9"):
        status, _, data = t.request(f"{STORY}/{path}")
        assert status == 401 and json.loads(data)["error"] == "auth_required", path
    answers = []
    for path in ("reef/pic/p1", "nothing-here/pic/p1", "lagoon/pic/p9", "lagoon/pic/P1", "lagoon/pic/p1.webp", "finale/pic/p1", "Lagoon/pic/p1"):
        status, _, body, _ = ask(world.player, f"{STORY}/{path}")
        assert status == 404, path
        body.pop("request_id")
        answers.append(json.dumps(body, sort_keys=True))
    assert len(set(answers)) == 1 and "Reef Secret" not in "".join(answers) and "Finale Secret" not in "".join(answers), "the same words for every one"


def test_09_a_picture_comes_as_a_webp_with_a_strong_tag_the_right_headers_and_a_304(world):
    status, headers, _, raw = ask(world.player, f"{STORY}/lagoon/pic/p2")
    assert status == 200 and raw == world.files[1]
    assert headers["Content-Type"].startswith("image/webp")
    assert headers["Cache-Control"] == "private, max-age=300" and headers["ETag"].startswith('"i') and "Cookie" in headers.get("Vary", "")
    assert headers["X-Content-Type-Options"] == "nosniff" and headers["Cross-Origin-Resource-Policy"] == "same-origin"
    status, again, _, body = ask(world.player, f"{STORY}/lagoon/pic/p2", {"If-None-Match": headers["ETag"]})
    assert status == 304 and body == b"" and again.get("ETag") == headers["ETag"]
    for n, data in enumerate(world.files, start=1):
        assert ask(world.player, f"{STORY}/lagoon/pic/p{n}")[3] == data


def test_10_the_story_names_its_pictures_the_page_lists_them_without_script_and_the_board_counts_the_pages(world):
    status, _, body, raw = ask(world.player, f"{STORY}/lagoon")
    assert status == 200 and body["data"]["v"] == 2 and body["data"]["flip"] is True and sorted(body["data"]["pics"]) == ["p1", "p2", "p3"]
    assert b"/var/story" not in raw and b".webp" not in raw and b"pics/" not in raw, "the data never says where the files are kept"
    status, _, _, page = ask(world.player, "/story/lagoon")
    html = page.decode("utf-8")
    assert status == 200 and html.count('src="/api/v1/l3mon/story/lagoon/pic/p') == 3 and "<noscript>" in html and "Host:" in html
    status, _, board, _ = ask(world.player, BOARD)
    assert {c["slug"]: c["cold_open"] for c in board["data"]["channels"]}["lagoon"] == {"panels": 3}


def test_11_the_finale_is_absent_until_the_crew_opens_it_and_then_opens_for_every_registered_player(world):
    assert ask(world.player, f"{STORY}/finale")[0] == 404
    assert "finale" not in [s["slug"] for s in ask(world.player, STORY)[2]["data"]["stories"]]
    status, out = r.admin("PATCH", "/api/v1/configs", {"l3mon_finale_open": True})
    assert status == 200, out
    status, _, body, _ = ask(world.player, f"{STORY}/finale")
    assert status == 200 and body["data"]["title"] == "Finale Secret Title" and len(body["data"]["panels"]) == 2
    assert ask(world.player, f"{STORY}/finale/pic/p2")[3] == world.finale_files[1]
    assert "finale" in [s["slug"] for s in ask(world.player, STORY)[2]["data"]["stories"]]
    status, _, data = t.request(f"{STORY}/finale")
    assert status == 401, "a visitor is still told to sign in"
    status, out = r.admin("PATCH", "/api/v1/configs", {"l3mon_finale_open": False})
    assert status == 200, out
    assert ask(world.player, f"{STORY}/finale")[0] == 404, "and the crew can take it back"
