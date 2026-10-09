"""The crew's scoring page: /admin/l3mon/scoring.

The page is a shell that reads and writes only through the crew's API (test_scoring_api.py); what it draws is checked in a real
browser against the running stack (tests/browser/scoring_page_check.mjs). Here: who may open it, that its files are served, that it
is on the admin menu, and the rules that keep it safe to open with a Content-Security-Policy and safe for names that contain markup.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_scoring
"""
import os
import re

from CTFd.plugins import l3mon_scoring
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

PAGE = "/admin/l3mon/scoring"
HERE = os.path.dirname(os.path.abspath(l3mon_scoring.__file__))
TEMPLATE = os.path.join(HERE, "templates", "l3mon_scoring", "scoring.html")
SCRIPT = os.path.join(HERE, "assets", "scoring.js")
STYLE = os.path.join(HERE, "assets", "scoring.css")


def test_an_administrator_gets_the_page_and_its_script():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        admin = login_as_user(app, "admin")
        r = admin.get(PAGE)
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "Scoring" in html
        assert 'id="scoring-app"' in html and 'data-api="/api/v1/l3mon/admin/scoring"' in html
        assert '<script defer src="/plugins/l3mon_scoring/assets/scoring.js"></script>' in html
        assert '<link rel="stylesheet" href="/plugins/l3mon_scoring/assets/scoring.css">' in html
    destroy_ctfd(app)


def test_a_visitor_and_a_player_are_sent_away():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        register_user(app, name="player", email="player@example.com")
        player = login_as_user(app, "player")
        for client in (app.test_client(), player):
            r = client.get(PAGE)
            assert r.status_code in (302, 403)
            assert "scoring-app" not in r.get_data(as_text=True)
    destroy_ctfd(app)


def test_the_script_and_the_style_are_served_to_administrators_only():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        admin = login_as_user(app, "admin")
        js = admin.get("/plugins/l3mon_scoring/assets/scoring.js")
        css = admin.get("/plugins/l3mon_scoring/assets/scoring.css")
        assert js.status_code == 200 and "javascript" in js.headers["Content-Type"]
        assert css.status_code == 200 and "css" in css.headers["Content-Type"]
        anonymous = app.test_client()
        assert anonymous.get("/plugins/l3mon_scoring/assets/scoring.js").status_code in (302, 403)
        assert anonymous.get("/plugins/l3mon_scoring/assets/scoring.css").status_code in (302, 403)
        register_user(app, name="player", email="player@example.com")
        assert login_as_user(app, "player").get("/plugins/l3mon_scoring/assets/scoring.js").status_code in (302, 403)
        for sneaky in ("../__init__.py", "..%2f__init__.py", "../tests/test_scoring_page.py", "%2e%2e/api.py", "../voids.py"):
            assert admin.get("/plugins/l3mon_scoring/assets/" + sneaky).status_code == 404, sneaky
        assert admin.get("/plugins/l3mon_scoring/assets/nothing.js").status_code == 404
    destroy_ctfd(app)


def test_the_page_is_on_the_admin_menu():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        admin = login_as_user(app, "admin")
        html = admin.get("/admin/notifications").get_data(as_text=True)
        assert re.search(r'<a[^>]+href="/admin/l3mon/scoring"[^>]*>\s*Scoring', html)
    destroy_ctfd(app)


def test_the_template_has_no_inline_script_and_no_inline_handlers_so_a_content_security_policy_can_be_added():
    source = open(TEMPLATE, encoding="utf-8").read()
    assert not re.search(r"<script(?![^>]*\bsrc=)", source), "every script in the page is a file"
    assert not re.search(r"\son[a-z]+\s*=", source, re.I), "no inline event handlers"
    assert "| safe" not in source and "|safe" not in source, "nothing from the database is ever marked safe"
    assert "style=" not in source, "no inline styles"


def test_the_script_draws_with_text_only_so_a_name_that_contains_markup_stays_text():
    source = open(SCRIPT, encoding="utf-8").read()
    # the words the script must never use; this list is what the test searches for, not code that runs
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout(\"", "javascript:"):
        assert forbidden not in source, f"scoring.js must not use {forbidden}"
    assert "textContent" in source
    assert "CSRF-Token" in source, "every write carries CTFd's token"


def test_every_score_word_on_the_page_is_trp():
    """The crew's page too: 'points' never appears in what it says."""
    for path in (TEMPLATE, SCRIPT):
        text = open(path, encoding="utf-8").read()
        assert not re.search(r"\bpoints?\b", text, re.I), f"{os.path.basename(path)} says 'points'"


def test_the_script_asks_before_it_changes_anything_and_stops_a_double_click():
    source = open(SCRIPT, encoding="utf-8").read()
    assert source.count("window.confirm(") >= 3, "set aside, put back and give a bonus each ask first"
    assert source.count("if (busy) return;") >= 4, "the three buttons and the saving step all stop while a change is on its way"
    assert "busy = true;" in source


def test_the_confirmations_show_what_the_studios_will_read_and_warn_about_a_programme_still_on_air():
    source = open(SCRIPT, encoding="utf-8").read()
    assert "restore_note" in source and "The studios are told" in source, "the Restore confirmation shows the line that will be sent"
    assert "c.state === 'visible'" in source and "solve it again at once" in source, "Revoke warns when players can still see the challenge"
