"""What the theme's pages read, from CTFd and from the environment (SP6 slice 1).

The context processor gives every template one object, `l3mon`. Its properties are read on demand: a page that is not ours
never reads them, so it costs no database statement (tests/test_theme_hook_cost.py). Only `me`, `player` and `event_name`
can touch the database, and only when a page of ours asks for them.

Failures are quiet and safe: a value that cannot be read gets a safe default and one line in the log (the kind of error,
never its text, a secret, a flag or a request body).
"""
import json
import logging
import os
import re

from CTFd.constants.sessions import Session
from CTFd.utils.config import ctf_name
from CTFd.utils.user import authed, get_current_user, is_admin

THEME = "l3mon"
PUBLIC = f"/themes/{THEME}/static/"  # CTFd serves a theme's static folder here
MANIFEST = "bundles.json"  # written by the exporter: logical name -> hashed file name (not manifest.json: CTFd reserves that name)
FILE = re.compile(r"^[a-z]+\.[0-9a-f]{10}\.(css|js)$")  # the only names the exporter writes

SITE_URL = "https://l3m0nctf.xyz"  # the landing page: the public website of the event (the platform host has no Rules or Privacy page)
SITE_URL_SETTING = "L3MON_SITE_URL"  # an environment setting may name another address (a staging website), https only
# Where the services are for a visitor, by name. Keep a name here until the page that answers it is built; then change its address.
ROUTES = {"channels": "/challenges", "scoreboard": "/scoreboard", "guide": "/scoreboard"}  # CTFd has no notifications page for players yet: the bell's slice adds one

_log = logging.getLogger("l3mon")
_manifests = {}  # path -> {logical name: file name}, read once per process
_logged = set()  # the failures already reported


def _note(key, err):
    """One log line per failure, naming what failed and the kind of error only."""
    if key not in _logged:
        _logged.add(key)
        _log.warning("l3mon theme: %s could not be read (%s); the page goes without it", os.path.basename(key), type(err).__name__)


def read_manifest(path):
    """logical name -> file name, from the theme's manifest. {} when the file is missing or broken, or when a name is not a
    plain bundle name (so nothing outside the static folder can be named). Read once per process."""
    if path in _manifests:
        return _manifests[path]
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) and FILE.match(v) for k, v in raw.items()):
            raise ValueError("a name is not a bundle name")
        result = dict(raw)
    except (OSError, ValueError) as err:
        _note(path, err)
        result = {}
    _manifests[path] = result
    return result


def bundle_urls():
    """logical name -> the address of the file under the theme's static folder."""
    from flask import current_app

    path = os.path.join(current_app.root_path, "themes", THEME, "static", MANIFEST)
    return {logical: PUBLIC + name for logical, name in read_manifest(path).items()}


def site_url():
    """SITE_URL, or the address named by L3MON_SITE_URL when that is a plain https address (a host, no path, query, spaces or quotes)."""
    raw = os.environ.get(SITE_URL_SETTING, "").strip().rstrip("/")
    if not raw:
        return SITE_URL
    if not re.fullmatch(r"https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?", raw):
        raise ValueError("not a plain https address")
    return raw


def captcha_state():
    from flask import request

    from CTFd.plugins.l3mon_guard import turnstile

    state = turnstile.get_guard_context()["captcha"]
    login = bool(state["login"])
    if not login and state["sitekey"] and request.method == "POST" and request.path == "/login":
        from CTFd.utils.user import get_ip

        login = bool(turnstile.is_login_captcha_required(request.form.get("name", ""), get_ip()))
    return {"sitekey": state["sitekey"] or "", "register": bool(state["register"]), "login": login, "reset": bool(state["reset"])}


def _ctftime_on():
    from CTFd.plugins.l3mon_ctftime import oauth

    return bool(oauth.configured())


def _quiet(name, default, read):
    """The value, or the default when reading it fails (logged once, by name and kind of error only)."""
    try:
        return read()
    except Exception as err:  # a page must never fail because of a value; the error text stays out of the log
        _note(name, err)
        return default


class Values:
    """The values one request's pages read. Nothing is read until a page asks for it."""

    def __init__(self, app):
        self._app = app

    def __getattr__(self, name):
        """A value a page registered (registry.py); anything else is not an attribute (a template then sees an undefined value)."""
        from CTFd.plugins.l3mon_theme import registry

        found = None if name.startswith("_") else registry.provider(name)
        if found is None:
            raise AttributeError(name)
        function, default = found
        return _quiet(name, default, function)

    @property
    def assets(self):
        return _quiet("assets", {}, bundle_urls)

    @property
    def csrf(self):
        return _quiet("csrf", "", lambda: Session.nonce or "")

    @property
    def signed_in(self):
        return _quiet("signed in", False, lambda: bool(authed()))

    @property
    def me(self):
        """{name, crew} for a signed-in viewer, else None (reads the account: a database statement, only when asked)."""
        return _quiet("me", None, self._me)

    def _me(self):
        if not authed():
            return None
        user = get_current_user()
        if user is None:
            return None
        return {"name": user.name, "crew": bool(is_admin())}

    @property
    def player(self):
        """Signed in with a studio, or the crew: the bell shows (somebody still setting up cannot read the news)."""
        return _quiet("player", False, self._player)

    def _player(self):
        if not authed():
            return False
        user = get_current_user()
        if user is None:
            return False
        return bool(is_admin()) or user.team_id is not None

    @property
    def ctftime_on(self):
        """The Login with CTFtime switch: the one the CTFtime plugin itself uses."""
        return _quiet("CTFtime sign-in switch", False, _ctftime_on)

    @property
    def build_sha(self):
        return _quiet("build", "dev", lambda: os.environ.get("L3MON_BUILD", "").strip() or "dev")

    @property
    def event_name(self):
        return _quiet("event_name", "", ctf_name)

    @property
    def site_url(self):
        """The event website's address (no trailing slash), for the footer's links and the pages that lead back to it."""
        return _quiet("site_url", SITE_URL, site_url)

    @property
    def captcha(self):
        """The human check (Cloudflare Turnstile, plugin l3mon_guard): {sitekey, register, login, reset}. `sitekey` is "" while the check is off;
        `login` is true when sign-in asks for it (after failures for the address, or for the name just posted)."""
        return _quiet("captcha", {"sitekey": "", "register": False, "login": False, "reset": False}, captcha_state)

    @property
    def routes(self):
        """Where the services are, by name. Today the stock CTFd pages answer; a slice that builds a page changes its line here."""
        return dict(ROUTES)


def install(app):
    @app.context_processor
    def theme_values():
        return {"l3mon": Values(app)}
