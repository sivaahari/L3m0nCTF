"""A programme's own page without script, through a whole CTFd (stream A, round 2): the task text, the files with the studio's links, the hints the studio has
opened and the ones it has not, the sentence where the flag form would be, the instance lines, and every state of the broadcast.

The page is `/board/<slug>`; its panel is drawn by script, and this is what a visitor without script reads (`pn`, plugins/l3mon_theme/pages/board.py).
The world is the board plugin's made-up one (three channels, seven programmes; "Lantern Walk" has two hints, "Steam Tunnel" is live).

Run through tools/run-ctfd-tests.sh (see docs/deploy/theme-slices.md):
    env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme/test_programme_page.py
"""
import datetime
import html as htmllib
import re

import pytest

from CTFd.models import Awards, ChallengeFiles, Challenges, Hints, Teams, db
from CTFd.utils import set_config
from theme_a_world import T_END, T_LIVE, T_START, clock, on_air, play, statements_of, text  # noqa: F401  (the fixture is used by name)

SENTENCE = "Flags and hints need JavaScript. Turn it on in your browser to send a flag or open a hint."
PAGE = "/board/lantern_walk"


def article(html):
    """The no-script programme page (inside <noscript>), or fail."""
    m = re.search(r'<article class="nsp".*?</article>', html, re.S)
    assert m, "the page carries the no-script programme"
    return m.group(0)


def flag_part(html):
    part = article(html)
    return part[part.index('id="pn-ns-flag-h"'):part.index('class="nsp-back"')]


def say(fragment):
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))


def lantern():
    return Challenges.query.filter_by(name="Lantern Walk").one()


def award(team_name, value=100):
    team = Teams.query.filter_by(name=team_name).one()
    db.session.add(Awards(user_id=team.members[0].id, team_id=team.id, name="Invented bonus", value=value, category="bonus"))
    db.session.commit()
    from CTFd.cache import clear_challenges, clear_standings

    clear_standings()
    clear_challenges()


def unlock(client, hint_id):
    return client.post("/api/v1/unlocks", json={"target": hint_id, "type": "hints"})


# ---- what the page says ------------------------------------------------------------------------------------------------------------------------


def test_the_page_carries_the_task_the_costs_and_the_one_sentence_instead_of_the_forms(play):
    with clock(T_LIVE):
        html = text(play.alice.get(PAGE))
    page = article(html)
    assert "Lantern Walk" in page and "P101" in page and "CH 1 · Mighty Street" in htmllib.unescape(page) and "Warm-up" in page
    assert "100</b> TRP" in page, "the value, in TRP"
    assert "Asha" in page, "the author is CTFd's own attribution"
    assert "In the story" not in page, "the platform has no story line yet: the part is left out"
    assert "Hint 1</b> · costs 10 TRP" in page and "Hint 2</b> · costs 25 TRP" in page
    assert "<form" not in page and 'type="hidden"' not in page and "<input" not in page, "no form: CTFd's attempt route takes JSON, and a second way to send a flag would bypass its limits"
    assert flag_part(html).count(SENTENCE) == 1, "the lead's sentence stands where the flag form would be"
    assert say(page).count(SENTENCE) == 3, "and once in each locked hint, where its form would be"
    assert "prototype" not in page.lower() and "mock" not in page.lower()
    assert 'href="/board"' in page, "the way back"


def test_the_task_text_the_crew_wrote_is_plain_paragraphs_and_hostile_markup_stays_text(play):
    lantern().description = (
        "<p>Find the <strong>lantern</strong>.</p><ul><li>First &amp; second.</li><li>Third.</li></ul>"
        "<script>alert(1)</script><img src=x onerror=alert(2)><a href=\"javascript:alert(3)\">link</a>"
    )
    db.session.commit()
    with clock(T_LIVE):
        html = text(play.alice.get(PAGE))
    page = article(html)
    task = page[page.index('id="pn-ns-task"'):page.index("</section>", page.index('id="pn-ns-task"'))]
    assert "<p>Find the lantern.</p>" in task and "First &amp; second. Third. link" in task
    for hostile in ("<script", "<img", "onerror", "javascript:", "<a href=\"javascript"):
        assert hostile not in task, hostile


def test_a_files_name_and_link_are_on_the_page_with_this_studios_token_and_no_size_or_hash(play):
    db.session.add(ChallengeFiles(challenge_id=lantern().id, location="ab12cd/ledger.txt"))
    db.session.commit()
    with clock(T_LIVE):
        mine = article(text(play.alice.get(PAGE)))
        theirs = article(text(play.bob.get(PAGE))) if play.bob.get(PAGE).status_code == 200 else ""
    assert "<b>ledger.txt</b>" in mine and "Download ledger.txt" in mine
    link = re.search(r'href="(/files/ab12cd/ledger\.txt\?token=[^"]+)"', mine)
    assert link, "the link carries the studio's own token"
    assert "sha256" not in mine, "the platform keeps no size or hash for a file, so the page shows none"
    assert "These files are made for your studio" in mine
    if theirs:
        assert link.group(1) not in theirs, "another studio gets another token"


def test_a_hint_the_studio_opened_shows_its_text_and_the_other_still_costs(play):
    award("studio-a", 100)
    first = Hints.query.filter_by(challenge_id=lantern().id).order_by(Hints.id).all()[0]
    with clock(T_LIVE):
        before = article(text(play.alice.get(PAGE)))
        assert "look left" not in before and "look right" not in before, "a hint nobody paid for is not on the page"
        assert unlock(play.alice, first.id).status_code == 200
        after = article(text(play.alice.get(PAGE)))
        mate = article(text(play.abe.get(PAGE)))
        other = article(text(play.bob.get(PAGE))) if play.bob.get(PAGE).status_code == 200 else ""
    assert "look left" in after and "look right" not in after
    assert "look left" in mate, "a studio shares what it paid for"
    assert "look left" not in other


def test_hostile_hint_text_stays_text(play):
    first = Hints.query.filter_by(challenge_id=lantern().id).order_by(Hints.id).all()[0]
    first.content = "<img src=x onerror=alert(1)><script>alert(2)</script>look"
    db.session.commit()
    award("studio-a", 100)
    with clock(T_LIVE):
        assert unlock(play.alice, first.id).status_code == 200
        page = article(text(play.alice.get(PAGE)))
    assert "&lt;img src=x onerror=alert(1)&gt;" in page and "<img" not in page and "<script" not in page


def test_the_tries_left_are_not_on_the_page_because_the_form_that_carried_them_is_not_there(play):
    """The prototype prints "N tries left" inside the flag form's hint. The platform page has no form (the sentence stands there), so the line is left out
    with it: a visitor without script cannot send a flag, so the number would only be a puzzle. The panel (script) shows it."""
    lantern().max_attempts = 5
    db.session.commit()
    with clock(T_LIVE):
        page = article(text(play.alice.get(PAGE)))
    assert "tries left" not in say(page)


def test_a_dynamic_programme_says_how_its_value_moves(play):
    with clock(T_LIVE):
        page = article(text(play.alice.get("/board/moth_cipher")))
    assert re.search(r"S(tart|tarted)s? at 500 TRP\. Falls as more studios solve it, down to 200\.", say(page))


def test_a_solved_programme_says_so_and_has_no_sentence_where_the_form_would_be(play):
    from board_world import solve

    team = Teams.query.filter_by(name="studio-a").one()
    solve(team.id, team.members[0].id, lantern().id)
    with clock(T_LIVE):
        html = text(play.alice.get(PAGE))
        other = text(play.bob.get(PAGE))
    assert "SOLVED by your studio" in article(html) and "Your studio has solved this programme." in article(html)
    assert SENTENCE not in flag_part(html)
    assert "SOLVED by your studio" not in article(other) and SENTENCE in flag_part(other)


# ---- every state of the broadcast -------------------------------------------------------------------------------------------------------------


def test_paused_the_page_says_so_and_still_names_what_needs_script(play):
    set_config("paused", True)
    with clock(T_LIVE):
        html = text(play.alice.get(PAGE))
    flag = say(flag_part(html))
    assert "PAUSED." in flag and "Submissions are paused for a moment." in flag and SENTENCE in flag


def test_ended_the_page_says_it_is_a_wrap_and_asks_for_no_script(play):
    set_config("view_after_ctf", True)
    with clock(T_END + 60):
        html = text(play.alice.get(PAGE))
    flag = say(flag_part(html))
    assert "THAT'S A WRAP." in flag and "Submissions are closed." in flag and SENTENCE not in flag
    assert "THAT'S A WRAP." in htmllib.unescape(html)


def test_ended_every_hint_shows_its_text_as_ctfd_does(play):
    set_config("view_after_ctf", True)
    with clock(T_END + 60):
        page = article(text(play.alice.get(PAGE)))
    assert "look left" in page and "look right" in page


def test_before_the_start_there_is_no_programme_page_only_the_slate(play):
    with clock(T_START - 3600):
        html = text(play.alice.get(PAGE))
    assert 'data-slate="before"' in html and 'id="pn-ns"' not in html and "Lantern Walk" not in html


def test_frozen_the_pages_still_serve_and_say_so(play):
    set_config("freeze", T_START + 600)
    with clock(T_LIVE):
        board = play.alice.get("/board")
        page = play.alice.get(PAGE)
        news = play.alice.get("/notifications")
    for r in (board, page, news):
        assert r.status_code == 200
        assert "frozen" in htmllib.unescape(text(r)).lower() or "Ranks frozen" in htmllib.unescape(text(r)) or "FROZEN" in text(r)
    assert SENTENCE in flag_part(text(page)), "a frozen scoreboard still takes flags"


def test_the_crew_open_the_page_with_no_studio_and_nothing_to_send(play):
    with clock(T_LIVE):
        r = play.admin.get(PAGE)
        html = text(r)
    assert r.status_code == 200
    assert "<form" not in article(html) and "Hint 1</b> · costs 10 TRP" in article(html)
    assert "look left" not in article(html), "the crew read hints in the admin panel; the board shows them what a player sees"


def test_a_visitor_and_a_player_without_a_studio_never_get_the_page(play):
    from tests.helpers import login_as_user, register_user

    with clock(T_LIVE):
        assert play.app.test_client().get(PAGE).status_code == 302
        register_user(play.app, name="loner", email="loner@example.com")
        loner = login_as_user(play.app, "loner")
        r = loner.get(PAGE)
        assert r.status_code == 302 and "/team" in r.headers["Location"]
        assert 'id="pn-ns"' not in text(r)


# ---- the instance part, with the instancer overlay off and on ---------------------------------------------------------------------------------------


def test_a_live_programme_without_the_instancer_says_not_running_and_that_starting_needs_script(play):
    on_air(play.admin, "steam_tunnel")
    with clock(T_LIVE):
        page = article(text(play.alice.get("/board/steam_tunnel")))
    inst = page[page.index('id="pn-ns-inst"'):page.index("</section>", page.index('id="pn-ns-inst"'))]
    assert "Not running." in inst and "Your studio has 0 of 3 instances running." in inst
    assert "Starting, renewing and stopping an instance need JavaScript." in inst
    assert "prototype" not in inst.lower()


def test_a_programme_that_is_not_live_has_no_instance_part(play):
    with clock(T_LIVE):
        page = article(text(play.alice.get(PAGE)))
    assert "Your instance" not in page


@pytest.mark.parametrize("state,expect", [("running", "Running at https://abc.example/. It switches off in about 40 minutes."), ("starting", "Warming up the set.")])
def test_a_studios_running_instance_is_on_its_programmes_page(play, state, expect):
    from CTFd.plugins.l3mon_instances.models import L3monInstance

    on_air(play.admin, "steam_tunnel")
    team = Teams.query.filter_by(name="studio-a").one()
    steam = Challenges.query.filter_by(name="Steam Tunnel").one()
    now = datetime.datetime.utcfromtimestamp(T_LIVE)
    db.session.add(L3monInstance(
        instance_id="abc123", team_id=team.id, challenge_id=steam.id, slug="steam_tunnel", protocol="http", state=state, url="https://abc.example/",
        created_at=now, expires_at=now + datetime.timedelta(minutes=40), active_key=f"{team.id}:{steam.id}",
    ))
    db.session.commit()
    with clock(T_LIVE):
        mine = article(text(play.alice.get("/board/steam_tunnel")))
        theirs = article(text(play.bob.get("/board/steam_tunnel")))
    assert expect in say(mine) and "Your studio has 1 of 3 instances running." in mine
    assert "abc.example" not in theirs and "Not running." in theirs and "Your studio has 0 of 3" in theirs, "another studio does not see it"


# ---- what it costs and what it must not reveal -------------------------------------------------------------------------------------------------


def test_the_page_of_a_programme_reads_the_database_for_that_programme_only(play):
    """Whatever is asked, the extra statements belong to the one programme: opening a second programme costs about the same."""
    with clock(T_LIVE):
        play.alice.get(PAGE)
        _, one = statements_of(lambda: play.alice.get(PAGE))
        _, two = statements_of(lambda: play.alice.get("/board/moth_cipher"))
    assert abs(len(one) - len(two)) <= 4, (len(one), len(two))
    assert len(one) <= 34


def test_a_held_back_programme_gets_no_page_and_no_statement_about_its_task(play):
    with clock(T_LIVE):
        for slug in ("hush_alley", "never_existed"):
            play.alice.get(f"/board/{slug}")  # the first request of a kind fills CTFd's caches
        _, held = statements_of(lambda: play.alice.get("/board/hush_alley"))
        _, unknown = statements_of(lambda: play.alice.get("/board/never_existed"))
    assert len(held) == len(unknown), "a withheld programme costs what an unknown one does"
    assert not any("hints" in s.lower() or "files" in s.lower() for s in held), "nothing about its hints or files is read"
