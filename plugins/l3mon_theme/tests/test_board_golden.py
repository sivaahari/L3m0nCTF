"""The board page's view model and the notifications list are the prototype's, to the character (SP6, stream A).

golden/board-pairs.json is written by the private repository's tools/golden-board.mjs: the prototype's own `boardView`, `phaseBanner` and
`noticeItems`, run on the made-up world of l3mon_board/tests/golden/scenario.json (three channels, seven programmes, solves, a pause, the end ...),
next to what the real board plugin would send for the same viewer in the same moment, in the REAL shape: each channel has a database id that
differs from its number (`no`), a programme points at its channel's database id. The builders must turn the second into the first.

Run through tools/run-ctfd-tests.sh (see docs/deploy/theme-slices.md); the builders need no database.
"""
import copy
import json
import os

import pytest

from CTFd.plugins.l3mon_theme.pages import board as page

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
with open(os.path.join(HERE, "board-pairs.json"), encoding="utf-8") as handle:
    PAIRS = json.load(handle)


def _name(case):
    moment = PAIRS["moments"][case["moment"]]
    ask = case["slug"] and f"deep link {case['slug']}" or (f"channel {case['ask']}" if case["ask"] is not None else "no channel asked")
    return f"{moment['name']} | {ask}"


@pytest.mark.parametrize("case", PAIRS["cases"], ids=_name)
def test_the_board_view_model_is_the_prototypes(case):
    data = PAIRS["moments"][case["moment"]]["input"]
    programme = page.find_programme(data, case["slug"]) if case["slug"] else None
    if case["slug"]:
        assert programme is not None, "the deep-linked programme is on air for this viewer"
    assert page.board_view(data, case["ask"], programme) == case["expected"]["board"]


@pytest.mark.parametrize("moment", range(len(PAIRS["moments"])))
def test_the_phase_banner_is_the_prototypes(moment):
    data = PAIRS["moments"][moment]["input"]
    expected = next(c["expected"]["banner"] for c in PAIRS["cases"] if c["moment"] == moment)
    from types import SimpleNamespace

    from CTFd.plugins.l3mon_board import banner

    assert banner.for_phase(SimpleNamespace(state=data["phase"]["state"], frozen=data["phase"]["frozen"])) == expected


@pytest.mark.parametrize("pair", PAIRS["notices"], ids=lambda p: p["name"])
def test_the_notifications_list_is_the_prototypes(pair):
    assert page.notice_lines(pair["lines"]) == pair["expected"]


def test_the_fixtures_exercise_what_they_claim_to():
    """The pairs are not vacuous: channel rows differ from numbers, there are sponsored channels, solved programmes, every phase and a long list."""
    inputs = [m["input"] for m in PAIRS["moments"]]
    assert all(c["id"] != c["no"] for m in inputs for c in m["channels"]), "a database id is never the channel number in these pairs"
    assert PAIRS["row_offset"] == 11
    assert any(c["sponsor"] for m in inputs for c in m["channels"])
    assert any(p["solved_by_me"] for m in inputs for p in m["programmes"])
    assert {m["phase"]["state"] for m in inputs} >= {"live", "paused", "ended"}
    assert any(len(p["lines"]) > 50 for p in PAIRS["notices"])
    assert len(PAIRS["cases"]) >= 200 and len(PAIRS["moments"]) >= 50


# ---- what the pairs cannot show: the real platform's differences from the prototype ----------------------------------------------


def _world(**extra):
    data = {
        "ver": 1, "phase": {"state": "live", "frozen": False}, "team": None, "notif_id": 0, "notif_ver": 0, "show_coming": True,
        "channels": [
            {"id": 31, "no": 3, "slug": "a", "name": "Alpha", "synopsis": "S.", "accent": "ch3", "picture": "a", "total": 2, "solved": 0, "signal": 0, "on_air": 1, "coming": 1, "sponsor": None},
            {"id": 7, "no": 1, "slug": "b", "name": "Bravo", "synopsis": "T.", "accent": "ch1", "picture": "b", "total": 1, "solved": 0, "signal": 0, "on_air": 1, "coming": 0, "sponsor": {"name": "Acme", "logo": None}},
        ],
        "programmes": [
            {"id": 100, "slug": "one_a", "number": 5, "channel": 31, "order": 0, "cell": 0, "name": "One A", "category": "web", "difficulty": "easy", "value": 100, "solves": 0, "solved_by_me": False, "first_blood_open": True, "live": False, "instance": None},
            {"id": 101, "slug": "one_b", "number": 6, "channel": 7, "order": 0, "cell": 0, "name": "One B", "category": "no-such-track", "difficulty": "hard", "value": 300, "solves": 2, "solved_by_me": True, "first_blood_open": False, "live": False, "instance": None},
        ],
    }
    data.update(extra)
    return data


def test_a_channel_is_known_by_the_number_the_crew_gave_not_by_its_row():
    keys = page.channel_keys(_world()["channels"])
    assert keys == {31: 3, 7: 1}
    view = page.board_view(_world(), "3")
    assert (view["ch"], view["chNo"], view["chName"]) == ("3", "CH 03", "Alpha")
    assert [c["id"] for c in view["channels"]] == ["3", "1"], "the strip keeps the board plugin's order and shows the numbers"
    assert page.board_view(_world(), "31")["ch"] == "3", "a database row asked for in the address is not a channel: the first channel is shown"


def test_numbers_that_cannot_be_told_apart_fall_back_to_the_rows_for_every_channel():
    twins = _world()
    twins["channels"][1]["no"] = 3
    assert page.channel_keys(twins["channels"]) == {31: 31, 7: 7}
    outside = _world()
    outside["channels"][0]["no"] = 100
    assert page.channel_keys(outside["channels"]) == {31: 31, 7: 7}
    assert page.channel_keys([{"id": 1, "no": True}]) == {1: 1}, "a boolean is no number"
    assert page.board_view(twins, "7")["ch"] == "7"


def test_the_coming_count_follows_the_crews_setting():
    assert page.board_view(_world(), "3")["countText"] == "1 programme · 1 coming up"
    assert page.board_view(_world(show_coming=False), "3")["countText"] == "1 programme"
    assert page.board_view(_world(), "3", show_coming=False)["countText"] == "1 programme"


def test_a_track_the_chips_do_not_know_is_kept_in_the_list_and_left_out_of_the_chips():
    view = page.board_view(_world(), "1")
    assert view["entries"][0]["cat"] == "no-such-track" and view["entries"][0]["solved"] == " · solved by your studio"
    assert [c["id"] for c in view["cats"]] == ["web"]


def test_the_picture_keeps_its_size_while_programmes_arrive():
    view = page.board_view(_world(), "3")
    assert (len(view["skeleton"]), view["rows"]) == (6, "1")
    big = _world()
    big["channels"][0]["total"] = 200
    view = page.board_view(big, "3")
    assert view["rows"] == "12", "the picture's rows are capped at 12 (a bigger cell count is the crew's mistake)"
    assert len(view["skeleton"]) == 34 * 6 and view["skeleton"][-1] == {"c": "5", "r": "11"}, "the skeleton keeps one cell for every slot"


def test_javascripts_rounding_not_pythons():
    assert page.js_round(0.5) == 1 and page.js_round(1.5) == 2 and page.js_round(2.5) == 3 and page.js_round(0.49) == 0
    data = _world()
    data["channels"][0].update(total=8)
    data["programmes"] = [dict(copy.deepcopy(data["programmes"][0]), id=200 + i, slug=f"p_{i}", order=i, solved_by_me=i == 0, channel=31) for i in range(8)]
    view = page.board_view(data, "3")
    assert view["sigText"] == "13% · 1 of 8 clear" and view["channels"][0]["sig"] == "1"  # 12.5 goes up


def test_the_frozen_and_paused_banners_are_the_prototypes_words():
    from types import SimpleNamespace

    from CTFd.plugins.l3mon_board import banner

    frozen = banner.for_phase(SimpleNamespace(state="live", frozen=True))
    assert (frozen["id"], frozen["lead"]) == ("frozen", "SCOREBOARD FROZEN.")
    assert banner.for_phase(SimpleNamespace(state="live", frozen=False)) is None
    assert banner.for_phase(SimpleNamespace(state="before", frozen=False)) is None


def test_a_platform_without_channels_still_has_a_frame():
    view = page.board_view({"channels": [], "programmes": [], "phase": {"state": "live", "frozen": False}})
    assert view["channels"] == [] and view["entries"] == [] and len(view["skeleton"]) == 6


def test_a_slug_is_found_only_when_it_is_plain_and_on_air_for_this_viewer():
    data = _world()
    assert page.find_programme(data, "one_a")["id"] == 100
    for bad in (None, "", "ONE_A", "one a", "one_a/", "../x", "one_a\n", "x" * 49, "-one", "unknown_slug", 5):
        assert page.find_programme(data, bad) is None, repr(bad)


def test_the_page_lists_the_newest_fifty_with_a_title_and_a_text():
    lines = [{"id": i, "title": f"T{i}" if i % 7 else "", "content": f"C{i}" if i % 3 else None} for i in range(1, 61)]
    shown = page.notice_lines(lines)
    assert len(shown) == 50
    assert shown[0] == {"title": "T60", "content": ""}, "the newest first; a line with no text has an empty text"
    assert [n["title"] for n in shown[:3]] == ["T60", "T59", "T58"] and shown[-1]["title"] == "T11"
    assert shown[60 - 56] == {"title": "News", "content": "C56"}, "a line with no title is called News"
    assert page.notice_lines([]) == []
    assert page.notice_lines([{"id": 1, "title": "<b>x</b>", "content": "<script>"}]) == [{"title": "<b>x</b>", "content": "<script>"}], "the page escapes; the builder keeps the text as it is"


def test_the_crawl_shows_the_newest_public_lines_title_and_text():
    lines = [{"id": i, "title": f"T{i}", "content": f"C{i}" if i % 2 else ""} for i in range(1, 13)]
    shown = page.crawl_text(lines)
    assert len(shown) == 8 and shown[0] == "T12" and shown[1] == "T11: C11" and shown[-1] == "T5: C5"
    assert page.crawl_text([]) == []


# ---- a programme's own page without script (`pn`) ----------------------------------------------------------------------------------------------

PROGRAMME_CASES = [c for c in PAIRS["cases"] if c.get("detail")]
PLATFORM_KEYS = ("canSend", "flagAction", "needJs")  # the platform sends no form: a flag and a hint go by script only (see programme_view)


def _line(data, programme):
    keys = page.channel_keys(data["channels"])
    channel = next(c for c in data["channels"] if c["id"] == programme["channel"])
    return f"CH {keys[channel['id']]} · {channel['name']}"


def _pn(case, **detail):
    data = PAIRS["moments"][case["moment"]]["input"]
    programme = page.find_programme(data, case["slug"])
    return page.programme_view(programme, _line(data, programme), {**case["detail"], **detail})


def _as_the_platform_says_it(pn):
    """The prototype's pn with its one allowed difference in the instance lines: the note does not say "prototype"."""
    pn = copy.deepcopy(pn)
    if pn["inst"]:
        assert pn["inst"]["note"] == "Starting, renewing and stopping an instance need script in this prototype."
        pn["inst"]["note"] = page.INSTANCE_NOTE
    return pn


def test_there_are_programme_pages_in_the_golden_pairs_for_every_kind_of_viewer():
    assert len(PROGRAMME_CASES) >= 40
    pns = [c["expected"]["pn"] for c in PROGRAMME_CASES]
    assert any(p["inst"] for p in pns), "a live programme with its instance lines"
    assert any(p["solved"] for p in pns) and any(p["decay"] for p in pns)
    assert any(p["paused"] for p in pns) and any(p["ended"] for p in pns)


@pytest.mark.parametrize("case", PROGRAMME_CASES, ids=_name)
def test_the_programme_page_model_is_the_prototypes(case):
    """With the forms asked for (the golden pairs say so), the builder gives the prototype's pn to the character, from the facts the database holds."""
    assert _pn(case) == _as_the_platform_says_it(case["expected"]["pn"])


@pytest.mark.parametrize("case", PROGRAMME_CASES, ids=_name)
def test_the_platforms_programme_page_is_the_prototypes_without_the_forms(case):
    want = _as_the_platform_says_it(case["expected"]["pn"])
    got = _pn(case, forms=False)
    for key in PLATFORM_KEYS:
        want[key] = got[key]
    for slot in ("h0", "h1"):
        if want[slot]:
            want[slot] = {**want[slot], "action": "", "affordable": False, "need": page.NEEDS_SCRIPT}
    assert got == want
    assert got["canSend"] is False and got["flagAction"] == ""
    waiting = not got["solved"] and not got["ended"]
    assert got["needJs"] == (page.NEEDS_SCRIPT if waiting else ""), "the sentence stands where the form would be, unless there is nothing to send"
    assert all(h["action"] == "" for h in (got["h0"], got["h1"]) if h), "no hint form: the platform has no route for it"


def _bare(**extra):
    programme = {"slug": "heron_ledger", "name": "Heron Ledger", "number": 301, "category": "Web", "difficulty": "easy", "value": 150, "solves": 0, "solved_by_me": False}
    detail = {"description": "<p>One.</p>", "author": "A. Author", "phase": {"state": "live", "frozen": False}, "score": 100, "files": [], "hints": [], "start": None, "floor": None}
    detail.update(extra)
    return page.programme_view(programme, "CH 3 · Marsh Channel", detail)


def test_the_task_text_is_plain_paragraphs_whatever_markup_the_crew_wrote():
    html = ('<p>Find the <strong>heron</strong>.</p><ul><li>First &amp; second.</li><li>Third</li></ul><script>alert(1)</script>'
            '<img src=x onerror=alert(2)><a href="javascript:alert(3)">link</a><style>p{x:y}</style><p>Last<br>line</p>')
    paragraphs = page.task_paragraphs(html)
    assert paragraphs == ["Find the heron.", "First & second.", "Third", "link", "Last", "line"]
    assert not any(c in "".join(paragraphs) for c in "<>")
    assert page.task_paragraphs("") == [] and page.task_paragraphs(None) == []
    assert page.task_paragraphs("<p>unclosed <b>bold") == ["unclosed bold"]
    assert page.task_paragraphs("<script>never</script>") == []
    assert page.task_paragraphs("   \n  ") == []
    view = _bare(description=html)["task"]
    assert view == {"lead": "Find the heron.", "where": "First & second.", "fmt": "Third link Last line"}
    assert _bare(description="")["task"] == {"lead": "", "where": "", "fmt": ""}


def test_the_platform_has_no_story_line_and_no_file_sizes_and_the_page_leaves_those_parts_out():
    view = _bare(files=[{"name": "ledger.txt", "url": "/files/ab/ledger.txt?token=T"}])
    assert view["story"] == ""
    assert view["f0"] == {"name": "ledger.txt", "size": "", "sha": "", "url": "/files/ab/ledger.txt?token=T"} and view["f1"] is None and view["hasFiles"]
    assert _bare()["f0"] is None and _bare()["hasFiles"] is False and _bare()["hasHints"] is False


def test_only_the_first_two_files_and_hints_have_a_place_on_the_page():
    files = [{"name": f"f{i}.txt", "url": f"/files/x/f{i}.txt"} for i in range(5)]
    hints = [{"id": i, "cost": 10 * i, "content": None} for i in range(1, 6)]
    view = _bare(files=files, hints=hints)
    assert [view["f0"]["name"], view["f1"]["name"]] == ["f0.txt", "f1.txt"]
    assert [view["h0"]["n"], view["h1"]["n"]] == ["1", "2"]


def test_a_hint_the_studio_opened_shows_its_text_and_a_locked_one_shows_the_sentence():
    view = _bare(hints=[{"id": 7, "cost": 40, "content": "Look at the reeds first."}, {"id": 8, "cost": 80, "content": None}])
    assert view["h0"]["unlocked"] is True and view["h0"]["text"] == "Look at the reeds first."
    assert view["h1"]["unlocked"] is False and view["h1"]["text"] == "" and view["h1"]["need"] == page.NEEDS_SCRIPT and view["h1"]["affordable"] is False


def test_the_sentence_is_the_leads_words():
    assert page.NEEDS_SCRIPT == "Flags and hints need JavaScript. Turn it on in your browser to send a flag or open a hint."
    assert _bare()["needJs"] == page.NEEDS_SCRIPT
    paused = _bare(phase={"state": "paused", "frozen": False})
    assert paused["needJs"] == page.NEEDS_SCRIPT and paused["paused"]
    ended = _bare(phase={"state": "ended", "frozen": False})
    assert ended["needJs"] == "" and ended["ended"]


def test_the_crew_has_no_score_and_nothing_to_send():
    view = _bare(score=None)
    assert view["canSend"] is False


def test_a_dynamic_value_says_where_it_started_and_where_it_stops():
    view = _bare(decay=True, start=500, floor=100)  # worth 150 now, so it has fallen from 500
    assert view["decay"] and view["note"] == "Started at 500 TRP. Falls as more studios solve it, down to 100."
    fresh = page.programme_view({"slug": "x", "name": "X", "number": 1, "category": "web", "difficulty": "easy", "value": 500, "solves": 0, "solved_by_me": False}, "CH 1 · X",
                                {"description": "", "phase": {"state": "live", "frozen": False}, "decay": True, "start": 500, "floor": 100, "files": [], "hints": []})
    assert fresh["note"] == "Starts at 500 TRP. Falls as more studios solve it, down to 100."
    programme = {"slug": "x", "name": "X", "number": 1, "category": "web", "difficulty": "easy", "value": 300, "solves": 2, "solved_by_me": True}
    detail = {"description": "", "phase": {"state": "live", "frozen": False}, "decay": True, "start": 500, "floor": 100, "files": [], "hints": []}
    got = page.programme_view(programme, "CH 1 · X", detail)
    assert got["note"].startswith("Started at 500 TRP") and got["solves"] == "2 solves" and got["solved"] and got["needJs"] == ""
    assert page.programme_view({**programme, "solves": 1}, "CH 1 · X", detail)["solves"] == "1 solve"
    assert _bare(decay=True, start=None, floor=None)["note"] == ""


def test_the_instance_lines_for_every_state_with_the_platforms_own_note():
    quota = {"used": 1, "limit": 3}
    states = {
        "running": {"state": "running", "connection": {"kind": "http", "url": "https://x.example/"}, "expires_in": 2400, "quota": quota},
        "tcp": {"state": "running", "connection": {"kind": "tcp", "command": "nc h 1"}, "expires_in": 30, "quota": quota},
        "starting": {"state": "starting", "connection": None, "expires_in": 0, "quota": quota},
        "stopped": {"state": "stopped", "connection": None, "expires_in": 0, "quota": {"used": 0, "limit": 3}},
    }
    assert page.instance_text(states["running"])["text"] == "Running at https://x.example/. It switches off in about 40 minutes."
    assert page.instance_text(states["tcp"])["text"] == "Running at nc h 1. It switches off in about 1 minutes."
    assert page.instance_text(states["starting"])["text"] == "Warming up the set."
    off = page.instance_text(states["stopped"])
    assert off == {"text": "Not running.", "quota": "Your studio has 0 of 3 instances running.", "note": "Starting, renewing and stopping an instance need JavaScript."}
    assert "prototype" not in str(off).lower()


# ---- the crew's own text for a category (CTFd keeps "Web", the board knows "web") --------------------------------------------------------------


@pytest.mark.parametrize("raw,known", [("web", "web"), ("Web", "web"), (" web ", "web"), ("WEB3", "web3"), ("RF", "rf"), ("Misc", "misc"), ("Quantum", "Quantum"), ("", ""), (None, None), (7, 7)])
def test_a_category_is_known_by_its_id_whatever_the_crew_typed(raw, known):
    assert page.category_id(raw) == known


def test_a_board_whose_categories_are_written_with_capitals_has_its_chips_and_its_labels():
    """The platform keeps the crew's own text ("Web"); the page must draw the same chips and labels as for the id ("web")."""
    moment = next(m for m in PAIRS["moments"] if m["input"]["programmes"])
    plain = moment["input"]
    shouting = copy.deepcopy(plain)
    for p in shouting["programmes"]:
        p["category"] = p["category"].capitalize() if p["category"] != "rf" else "RF"
    assert page.board_view(shouting, None, None) == page.board_view(plain, None, None)
    odd = copy.deepcopy(plain)
    odd["programmes"][0]["category"] = "Quantum"
    view = page.board_view(odd, None, None)
    assert "quantum" not in [c["id"] for c in view["cats"]], "a track the chips do not know has no chip"
