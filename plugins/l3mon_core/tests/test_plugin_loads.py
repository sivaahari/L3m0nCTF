"""l3mon_core loads inside a real CTFd app and its endpoints and settings behave.

Run through tools/run-ctfd-tests.sh, which provides CTFd's own test helpers:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests

CTFd's test setup turns plugins off unless asked, so every app here is created with enable_plugins=True.
"""
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user, register_user


def test_the_health_endpoint_answers_and_is_never_cached():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with app.test_client() as client:
            r = client.get("/l3mon/healthz")
            assert r.status_code == 200
            assert r.get_json() == {"status": "ok", "plugin": "l3mon_core"}
            assert r.headers["Cache-Control"] == "no-store"
            # CTFd itself starts an anonymous session on every request, so a cookie can appear here; nginx removes it
            # for the health routes (checked in tests/integration/test_stack.py).
    destroy_ctfd(app)


def test_the_endpoint_does_not_exist_when_plugins_are_off():
    # proves the test above really exercises our plugin and not something else
    app = create_ctfd(enable_plugins=False)
    with app.app_context():
        with app.test_client() as client:
            assert client.get("/l3mon/healthz").status_code == 404
    destroy_ctfd(app)


def test_the_plugin_does_not_break_ctfds_own_health_check():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with app.test_client() as client:
            assert client.get("/healthcheck").status_code == 200
    destroy_ctfd(app)


def test_the_l3mon_theme_is_selectable_and_falls_back_to_core():
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon")
    with app.app_context():
        with app.test_client() as client:
            r = client.get("/login")
            assert r.status_code == 200  # the empty theme serves core's template through THEME_FALLBACK
    destroy_ctfd(app)


def test_the_root_sends_visitors_to_sign_in_and_players_to_the_challenges():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with app.test_client() as anonymous:
            r = anonymous.get("/")
            assert r.status_code == 303 and r.headers["Location"].endswith("/login")
        register_user(app)
        with login_as_user(app) as player:
            r = player.get("/")
            assert r.status_code == 303 and r.headers["Location"].endswith("/challenges")
    destroy_ctfd(app)


def test_secure_cookies_are_switched_on_by_the_environment(monkeypatch):
    monkeypatch.setenv("L3MON_SECURE_COOKIES", "true")
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with app.test_client() as client:
            cookie = client.get("/login").headers.get("Set-Cookie", "")
            assert cookie.startswith("__Host-session="), cookie
            assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
            assert "Domain=" not in cookie  # the __Host- prefix forbids a Domain, which keeps the cookie to this exact host
    destroy_ctfd(app)


def test_cookies_are_plain_when_the_environment_does_not_ask(monkeypatch):
    monkeypatch.delenv("L3MON_SECURE_COOKIES", raising=False)
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with app.test_client() as client:
            cookie = client.get("/login").headers.get("Set-Cookie", "")
            assert cookie.startswith("session=") and "Secure" not in cookie
    destroy_ctfd(app)
