"""Stream A, round 2 (the lead's audit of 24 October): rules that had no test of their own.

  * the slug pattern (`SLUG`) is pinned directly, not only through `find_programme`;
  * a request for another page never reads the news crawl (it runs a database query when read);
  * `/board` is answered by this module and never by the lead's alias redirect (`pages/routes.py`), and the order of the two file names that makes it so is pinned;
  * the footer's wording that the report box reads its contact from is pinned (src/js/report.js in the UI repository reads it).

Run through tools/run-ctfd-tests.sh (see docs/deploy/theme-slices.md):
    env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme/test_board_round2.py
"""
import pkgutil
import re
from unittest import mock

import pytest

from CTFd.plugins.l3mon_theme import pages, registry
from CTFd.plugins.l3mon_theme.pages import board as page
from theme_a_world import T_LIVE, T_START, clock, main_of, play, text  # noqa: F401  (the fixture is used by name)

# ---- A5: the slug pattern -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("slug", [
    "a", "0", "lantern_walk", "moth-cipher", "a1", "9lives", "x" * 48, "a_b-c_9", "web01", "l0ng_name-with_everything-09",
])
def test_the_pattern_accepts_the_slugs_the_plan_loader_writes(slug):
    from CTFd.plugins.l3mon_release.plan import PROGRAMME_SLUG

    assert PROGRAMME_SLUG.match(slug), "the sample is a slug the loader accepts"
    assert page.SLUG.match(slug), slug


@pytest.mark.parametrize("slug,why", [
    ("Lantern", "an uppercase letter"),
    ("lanterN", "an uppercase letter at the end"),
    ("-lantern", "a leading dash"),
    ("_lantern", "a leading underscore"),
    ("x" * 49, "49 characters (the column holds 48)"),
    ("x" * 300, "far too long"),
    ("lantern\n", "a newline at the end"),
    ("lantern\nwalk", "a newline inside"),
    ("", "empty"),
    ("lantern walk", "a space"),
    ("lantern.walk", "a dot"),
    ("lantern/walk", "a slash"),
    ("..", "a path"),
    ("lantern%00", "an escaped null"),
    ("lantern\x00", "a null"),
    ("lanterń", "a letter outside ASCII"),
    ("ланtern", "Cyrillic letters"),
    ("lantern²", "a superscript digit"),
    ("١٢٣", "Arabic-Indic digits"),
])
def test_the_pattern_refuses_what_is_not_a_plain_slug(slug, why):
    assert page.SLUG.match(slug) is None, why


def test_the_pattern_is_what_find_programme_checks_first():
    """Defence in depth: a withheld programme and a slug that is not plain are both None, but a plain-looking slug is still compared with the real ones."""
    data = {"programmes": [{"slug": "lantern_walk"}]}
    with mock.patch.object(page, "SLUG", re.compile(r"[a-z]{1,3}\Z")):
        assert page.find_programme(data, "lantern_walk") is None, "the pattern is consulted: a slug it refuses is never looked up"
    assert page.find_programme(data, "lantern_walk") == {"slug": "lantern_walk"}
    assert page.find_programme(data, "lantern_walk\n") is None
    assert page.find_programme(data, None) is None
    assert page.find_programme(data, 7) is None


# ---- A4: the crawl costs nothing on a page that does not draw it ------------------------------------------------------------------------


@pytest.fixture()
def counted_crawl():
    calls = []

    def counting():
        calls.append(1)
        return ["A line."]

    with mock.patch.dict(registry._PROVIDERS, {"crawl": (counting, ())}):
        yield calls


@pytest.mark.parametrize("path,who", [
    ("/login", "visitor"), ("/scoreboard", "visitor"), ("/settings", "alice"), ("/notifications", "alice"), ("/api/v1/challenges", "alice"),
    ("/themes/l3mon/static/bundles.json", "visitor"), ("/team", "alice"), ("/missing-page", "alice"),
])
def test_a_request_for_another_page_does_not_read_the_crawl(play, counted_crawl, path, who):
    client = play.app.test_client() if who == "visitor" else play.alice
    with clock(T_LIVE):
        counted_crawl.clear()
        client.get(path)
    assert counted_crawl == [], f"{path} read the news crawl"


def test_the_board_reads_the_crawl_once(play, counted_crawl):
    with clock(T_LIVE):
        counted_crawl.clear()
        r = play.alice.get("/board")
        assert r.status_code == 200
    assert len(counted_crawl) == 1, "the board reads the crawl exactly once"
    assert "A line." in text(r)


def test_the_page_before_the_start_does_not_read_the_crawl(play, counted_crawl):
    with clock(T_START - 3600):
        r = play.alice.get("/board")
    assert r.status_code == 200 and 'data-slate="before"' in text(r)
    assert counted_crawl == [], "the 'not on air yet' page draws no crawl"


# ---- A7: /board is this module's, never the alias ---------------------------------------------------------------------------------------


def test_the_board_module_is_imported_before_the_alias_module():
    names = [info.name for info in sorted(pkgutil.iter_modules(pages.__path__), key=lambda i: i.name)]
    assert "board" in names and "routes" in names
    assert names.index("board") < names.index("routes"), (
        "pages/board.py must be imported before pages/routes.py, or the alias redirect takes /board first; a rename of either file breaks this order"
    )


def test_board_is_answered_by_the_board_module_not_by_the_alias(play):
    rules = [rule for rule in play.app.url_map.iter_rules() if rule.rule == "/board"]
    assert [rule.endpoint for rule in rules] == ["l3mon_theme_board"], "one rule for /board, and it is ours"
    assert not [rule for rule in play.app.url_map.iter_rules() if rule.endpoint.startswith("l3mon_theme_alias_") and "board" in rule.endpoint]
    with clock(T_LIVE):
        for client in (play.alice, play.admin):
            r = client.get("/board")
            assert r.status_code == 200, "a redirect to /challenges would mean the alias answered"
            assert "Location" not in r.headers
            assert 'data-board' in text(r)


def test_the_programme_page_is_answered_by_the_board_module_too(play):
    rules = [rule for rule in play.app.url_map.iter_rules() if rule.rule == "/board/<slug>"]
    assert [rule.endpoint for rule in rules] == ["l3mon_theme_programme"]


# ---- A3: the footer's wording the report box depends on ---------------------------------------------------------------------------------


def first_small_print(html):
    m = re.search(r'<p class="foot-small">(.*?)</p>', html, re.S)
    assert m, "the footer has a small print"
    return re.sub(r"<[^>]*>", "", m.group(1))


@pytest.mark.parametrize("path", ["/board", "/challenges", "/board/lantern_walk", "/notifications"])
def test_the_first_small_print_of_the_footer_is_the_contact_line(play, path):
    """src/js/report.js reads `.foot-small` (the first one) and the text from "Contact:" up to a middle dot. Until the crew has set an address
    the line is "Contact: announced soon"; if the footer changes, the report box would silently lose its contact, so this fails first."""
    with clock(T_LIVE):
        html = text(play.alice.get(path, follow_redirects=True))
    assert first_small_print(html) == "Contact: announced soon"
    assert re.search(r"Contact:[^·]*", first_small_print(html)).group(0).strip() == "Contact: announced soon"
    assert "·" not in first_small_print(html), "the real platform's footer never carries the prototype tools' status text"


def test_the_report_boxs_sentence_for_a_player_without_script_is_on_the_programmes_page(play):
    with clock(T_LIVE):
        html = main_of(text(play.alice.get("/board/lantern_walk")))
    assert "Flags and hints need JavaScript. Turn it on in your browser to send a flag or open a hint." in html
