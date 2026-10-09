"""The crew's page: /admin/l3mon/release.

The page is a shell that reads and writes only through the crew's API (test_api.py); what it draws is checked in a real browser
against the running stack (tests/integration and the verification log). Here: who may open it, that its files are served, that it
is on the admin menu, and the rules that keep it safe to open with a Content-Security-Policy and safe for names that contain markup.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_release
"""
import os
import re

from CTFd.plugins import l3mon_release
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user

PAGE = "/admin/l3mon/release"
HERE = os.path.dirname(os.path.abspath(l3mon_release.__file__))
TEMPLATE = os.path.join(HERE, "templates", "l3mon_release", "release.html")
SCRIPT = os.path.join(HERE, "assets", "release.js")
STYLE = os.path.join(HERE, "assets", "release.css")


def test_an_administrator_gets_the_page_and_its_script():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        admin = login_as_user(app, "admin")
        r = admin.get(PAGE)
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "Release control" in html
        assert 'id="release-app"' in html and 'data-api="/api/v1/l3mon/admin/release"' in html
        assert '<script defer src="/plugins/l3mon_release/assets/release.js"></script>' in html
        assert '<link rel="stylesheet" href="/plugins/l3mon_release/assets/release.css">' in html
    destroy_ctfd(app)


def test_a_visitor_and_a_player_are_sent_away():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        register_user(app, name="player", email="player@example.com")
        player = login_as_user(app, "player")
        for client in (app.test_client(), player):
            r = client.get(PAGE)
            assert r.status_code in (302, 403)
            assert "release-app" not in r.get_data(as_text=True)
    destroy_ctfd(app)


def test_the_script_and_the_style_are_served_to_administrators_only():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        admin = login_as_user(app, "admin")
        js = admin.get("/plugins/l3mon_release/assets/release.js")
        css = admin.get("/plugins/l3mon_release/assets/release.css")
        assert js.status_code == 200 and "javascript" in js.headers["Content-Type"]
        assert css.status_code == 200 and "css" in css.headers["Content-Type"]
        anonymous = app.test_client()
        assert anonymous.get("/plugins/l3mon_release/assets/release.js").status_code in (302, 403)
        assert anonymous.get("/plugins/l3mon_release/assets/release.css").status_code in (302, 403)
        register_user(app, name="player", email="player@example.com")
        assert login_as_user(app, "player").get("/plugins/l3mon_release/assets/release.js").status_code in (302, 403)
        for sneaky in ("../__init__.py", "..%2f__init__.py", "../tests/test_page.py", "%2e%2e/api.py"):
            assert admin.get("/plugins/l3mon_release/assets/" + sneaky).status_code == 404, sneaky
        assert admin.get("/plugins/l3mon_release/assets/nothing.js").status_code == 404
    destroy_ctfd(app)


def test_the_page_is_on_the_admin_menu():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        admin = login_as_user(app, "admin")
        html = admin.get("/admin/notifications").get_data(as_text=True)
        assert re.search(r'<a[^>]+href="/admin/l3mon/release"[^>]*>\s*Release control', html)
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
        assert forbidden not in source, f"release.js must not use {forbidden}"
    assert "textContent" in source
    assert "CSRF-Token" in source, "every write carries CTFd's token"


def test_the_script_turns_a_time_in_india_into_the_right_epoch_second():
    """09:00 IST on 28 November 2026 is 03:30 UTC. The page does this sum in the browser, so the rule is checked here on the source."""
    source = open(SCRIPT, encoding="utf-8").read()
    assert "IST_OFFSET_MINUTES = 330" in source
    assert "Date.UTC(" in source and "- IST_OFFSET_MINUTES * 60" in source
