"""The values the theme's pages read (SP6 slice 1): what they hold, that reading them is cheap, and that a failure is quiet and safe.

Run through tools/run-ctfd-tests.sh (see docs/deploy/theme-slices.md). The hook-cost rule has its own file (test_theme_hook_cost.py).
"""
import json
import logging

import pytest
from sqlalchemy import event

from CTFd.models import Users, db
from CTFd.plugins.l3mon_ctftime import oauth
from CTFd.plugins.l3mon_theme import values
from CTFd.utils.security.auth import login_user
from tests.helpers import create_ctfd, destroy_ctfd, register_user

PASSWORD = "a-long-test-password"  # made up, for the throwaway test app only


@pytest.fixture
def app():
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams", ctf_name="L3m0n test event")
    register_user(app, name="player", email="player@example.com", password=PASSWORD)
    yield app
    destroy_ctfd(app)


def statements(fn):
    seen = []

    def on(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", on)
    try:
        result = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", on)
    return result, seen


def test_the_manifest_names_the_bundles_and_the_urls_are_the_theme_static_files(app):
    with app.app_context():
        urls = values.bundle_urls()
        assert {"css.app", "css.slate", "js.boot", "js.login", "js.slate"} <= set(urls), "the bundles of the registered pages (a stream that adds a page adds its bundles)"
        for logical, url in urls.items():
            assert url.startswith("/themes/l3mon/static/"), logical
            assert values.FILE.match(url.rsplit("/", 1)[1]), logical


def test_a_manifest_that_is_missing_or_broken_gives_nothing_and_says_so_once(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="l3mon")
    missing = tmp_path / "nope" / "manifest.json"
    broken = tmp_path / "manifest.json"
    broken.write_text("{ this is not json", encoding="utf-8")
    assert values.read_manifest(str(missing)) == {}
    assert values.read_manifest(str(missing)) == {}
    assert values.read_manifest(str(broken)) == {}
    lines = [r.getMessage() for r in caplog.records if r.name == "l3mon"]
    assert len(lines) == 2, lines  # one line for each file, not one for each read
    assert all("this is not json" not in line for line in lines)


def test_a_manifest_with_a_name_that_is_not_a_plain_bundle_is_refused(tmp_path):
    bad = tmp_path / "manifest.json"
    bad.write_text(json.dumps({"css.app": "../../etc/passwd", "js.boot": "boot.0123456789.js"}), encoding="utf-8")
    assert values.read_manifest(str(bad)) == {}
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"css.app": "app.0123456789.css", "js.boot": "boot.0123456789.js"}), encoding="utf-8")
    assert values.read_manifest(str(good)) == {"css.app": "app.0123456789.css", "js.boot": "boot.0123456789.js"}


def test_reading_the_values_of_a_visitor_costs_no_statement(app):
    with app.app_context():
        with app.test_request_context("/login"):
            v = values.Values(app)

            def read():
                return (v.assets, v.csrf, v.signed_in, v.me, v.player, v.ctftime_on, v.build_sha)

            result, seen = statements(read)
            assert seen == []
            assets, csrf, signed_in, me, player, ctftime_on, sha = result
            assert signed_in is False and me is None and player is False
            assert isinstance(csrf, str)


def test_a_signed_in_player_is_named_and_has_no_studio_yet(app):
    with app.app_context():
        user = Users.query.filter_by(name="player").first()
        with app.test_request_context("/login"):
            login_user(user)  # what a sign-in does to the session
            v = values.Values(app)
            assert v.signed_in is True
            assert v.me == {"name": "player", "crew": False}
            assert v.player is False  # team mode and no studio: no bell for this account


def test_the_ctftime_value_is_the_sign_in_switch_of_the_ctftime_plugin(app, monkeypatch):
    with app.app_context():
        with app.test_request_context("/login"):
            monkeypatch.setattr(oauth, "configured", lambda: True)
            assert values.Values(app).ctftime_on is True
            monkeypatch.setattr(oauth, "configured", lambda: False)
            assert values.Values(app).ctftime_on is False


def test_the_build_is_named_from_the_environment_and_is_dev_without_it(app, monkeypatch):
    with app.app_context():
        with app.test_request_context("/login"):
            monkeypatch.delenv("L3MON_BUILD", raising=False)
            assert values.Values(app).build_sha == "dev"
            monkeypatch.setenv("L3MON_BUILD", "abc1234")
            assert values.Values(app).build_sha == "abc1234"


def test_the_event_name_is_the_configured_name(app):
    with app.app_context():
        with app.test_request_context("/login"):
            assert values.Values(app).event_name == "L3m0n test event"


def test_a_value_that_cannot_be_read_is_safe_and_is_logged_once_without_the_error_text(app, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="l3mon")

    def broken(path):
        raise RuntimeError("a secret-looking text that must not be logged")

    monkeypatch.setattr(values, "read_manifest", broken)
    with app.app_context():
        with app.test_request_context("/login"):
            v = values.Values(app)
            assert v.assets == {}
            assert v.assets == {}
    lines = [r.getMessage() for r in caplog.records if r.name == "l3mon"]
    assert len(lines) == 1, lines
    assert "assets" in lines[0]
    assert "secret-looking" not in lines[0] and "RuntimeError" in lines[0]


def test_a_failing_event_name_gives_a_safe_default(app, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="l3mon")

    def broken_name():
        raise ValueError("database detail")

    monkeypatch.setattr(values, "ctf_name", broken_name)
    with app.app_context():
        with app.test_request_context("/login"):
            assert values.Values(app).event_name == ""
    assert any("event_name" in r.getMessage() for r in caplog.records)
    assert not any("database detail" in r.getMessage() for r in caplog.records)
