"""The theme's pages lead to places that exist (SP6 plumbing): the website's address, the route table, and the links on the sign-in and error pages.

Run through tools/run-ctfd-tests.sh with the working-tree theme and plugins mounted (see docs/deploy/theme-slices.md).
The platform host has no Rules or Privacy page: those are the website's (https://l3m0nctf.xyz/rules/ and /privacy/).
"""
import logging
import re

import pytest

from CTFd.plugins.l3mon_theme import values
from tests.helpers import create_ctfd, destroy_ctfd


@pytest.fixture
def app():
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams", ctf_name="L3m0n test event")
    yield app
    destroy_ctfd(app)


def test_the_website_address_is_the_landing_page_unless_a_plain_https_address_is_set(monkeypatch):
    monkeypatch.delenv(values.SITE_URL_SETTING, raising=False)
    assert values.site_url() == "https://l3m0nctf.xyz"
    monkeypatch.setenv(values.SITE_URL_SETTING, "https://staging.example.org/")
    assert values.site_url() == "https://staging.example.org", "no trailing slash"
    for bad in ("http://l3m0nctf.xyz", "https://", "https://a b.example", 'https://x.example/"', "https://x.example/path", "//x.example", "javascript:alert(1)", "https://x.example?q=1"):
        monkeypatch.setenv(values.SITE_URL_SETTING, bad)
        with pytest.raises(ValueError):
            values.site_url()


def test_a_bad_website_setting_gives_the_default_and_one_quiet_line(app, monkeypatch, caplog):
    monkeypatch.setenv(values.SITE_URL_SETTING, "javascript:alert(1)")
    values._logged.discard("site_url")
    with app.test_request_context("/"):
        found = values.Values(app)
        with caplog.at_level(logging.WARNING, logger="l3mon"):
            assert found.site_url == "https://l3m0nctf.xyz"
            assert found.site_url == "https://l3m0nctf.xyz"
    lines = [r.getMessage() for r in caplog.records if "site_url" in r.getMessage()]
    assert len(lines) == 1 and "javascript" not in lines[0], "one line, the kind of error and never the text"


def test_the_route_table_names_pages_that_answer_today(app):
    with app.test_request_context("/"):
        routes = values.Values(app).routes
    assert set(routes) >= {"channels", "scoreboard", "guide"}
    for name, path in routes.items():
        assert path.startswith("/") and not path.startswith("//"), name
        with app.test_client() as client:
            assert client.get(path).status_code in (200, 302, 403), f"{name} ({path}) must be a page that exists"
    routes["channels"] = "/changed"
    with app.test_request_context("/"):
        assert values.Values(app).routes["channels"] == "/challenges", "a page cannot change the shared table by changing what it was given"


def test_the_sign_in_footer_links_to_the_website_and_names_no_prototype(app):
    with app.test_client() as client:
        body = client.get("/login").get_data(as_text=True)
    for path in ("/", "/rules/", "/privacy/"):
        assert f'href="https://l3m0nctf.xyz{path}"' in body, path
    assert 'href="/rules"' not in body and 'href="/privacy"' not in body, "the platform host has neither page"
    assert "Rules (draft)" not in body and "Status: all on air" not in body
    assert "prototype" not in body.lower() and "mock" not in body.lower()


def test_the_error_pages_lead_to_pages_that_exist(app):
    with app.test_client() as client:
        body = client.get("/there-is-no-such-page").get_data(as_text=True)
    hrefs = re.findall(r'href="([^"]+)"', body)
    assert "/challenges" in hrefs and "/guide" in hrefs and "https://l3m0nctf.xyz/rules/" in hrefs
    offending = [h for h in hrefs if h in ("/board", "/rules", "/privacy")]
    assert not offending, f"links to pages that do not exist yet: {offending}; all links: {[h for h in hrefs if not h.startswith('#')]}"


def test_the_prototype_addresses_are_answered_by_their_own_pages_and_no_alias_stands_in_front_of_them(app):
    """The aliases (pages/routes.py) send a prototype address that CTFd lacks to the page that answers today. Stream A built /board and stream C built /guide, so
    both answer themselves and no alias is left; a visitor is asked to sign in at either, and the destination of the sign-in is a real page."""
    with app.test_client() as client:
        assert not [rule.endpoint for rule in app.url_map.iter_rules() if rule.endpoint.startswith("l3mon_theme_alias_")]
        for path in ("/board", "/guide"):
            r = client.get(path)
            assert r.status_code == 302 and r.headers["Location"].startswith("/login"), path
        assert client.get("/login").status_code == 200


def test_a_page_that_answers_its_own_address_is_not_aliased():
    from flask import Flask

    from CTFd.plugins.l3mon_theme.pages import routes

    app = Flask(__name__)
    app.add_url_rule("/board", endpoint="real_board", view_func=lambda: "the real board")
    routes.install(app)
    with app.test_client() as client:
        assert client.get("/board").get_data(as_text=True) == "the real board"
        assert client.get("/guide").status_code == 302
