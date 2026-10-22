"""How a page of the theme adds its own values and hooks (SP6 plumbing): the registry, and the pages/ folder it discovers.

Run through tools/run-ctfd-tests.sh (see docs/deploy/theme-slices.md).
"""
import logging

import pytest
from flask import render_template_string

from CTFd.plugins.l3mon_theme import registry
from tests.helpers import create_ctfd, destroy_ctfd


@pytest.fixture
def app(monkeypatch):
    # a registration made by one test must not be there for the next
    monkeypatch.setattr(registry, "_PROVIDERS", dict(registry._PROVIDERS))
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon", user_mode="teams", ctf_name="L3m0n test event")
    yield app
    destroy_ctfd(app)


def read(app, name):
    with app.test_request_context("/"):
        return render_template_string("{{ l3mon.%s }}" % name)


def test_a_page_registers_a_value_and_a_template_reads_it(app):
    @registry.value("demo_value", default="none")
    def demo_value():
        return "from the page"

    assert read(app, "demo_value") == "from the page"


def test_the_value_is_read_only_when_a_template_asks_for_it(app):
    calls = []

    @registry.value("lazy_value", default="")
    def lazy_value():
        calls.append(1)
        return "x"

    with app.test_request_context("/"):
        assert render_template_string("nothing here") == "nothing here"
        assert calls == [], "a page that does not read the value must not pay for it"
        render_template_string("{{ l3mon.lazy_value }}")
    assert calls == [1]


def test_a_failing_value_gives_its_default_and_one_quiet_log_line(app, caplog):
    @registry.value("broken_value", default="safe")
    def broken_value():
        raise RuntimeError("SECRET text that must not reach the log")

    with caplog.at_level(logging.WARNING, logger="l3mon"):
        assert read(app, "broken_value") == "safe"
        assert read(app, "broken_value") == "safe"
    lines = [r.getMessage() for r in caplog.records if "broken_value" in r.getMessage()]
    assert len(lines) == 1, "one line, however often it fails"
    assert "RuntimeError" in lines[0] and "SECRET" not in lines[0], "the kind of error, never its text"


def test_a_shared_value_cannot_be_shadowed_and_a_name_is_taken_once(app):
    with pytest.raises(ValueError, match="belongs to values.py"):
        registry.value("assets")(lambda: {})

    @registry.value("taken_value")
    def first():
        return 1

    with pytest.raises(ValueError, match="already provided"):

        @registry.value("taken_value")
        def second():
            return 2

    registry.value("taken_value")(first)  # the same function again (a module imported twice) is not a clash


@pytest.mark.parametrize("bad", ["", "with space", "9lives", "_private", "a.b", None, 5])
def test_a_value_name_is_a_plain_public_name(app, bad):
    with pytest.raises(ValueError, match="plain name"):
        registry.value(bad)


def test_an_unknown_value_is_undefined_in_a_template_and_not_an_error(app):
    assert read(app, "nobody_registered_this") == ""


def test_every_module_of_pages_is_discovered_and_installed(app, monkeypatch):
    calls = []
    import types

    from CTFd.plugins.l3mon_theme import pages

    class FakeInfo:
        def __init__(self, name):
            self.name = name

    modules = {"zeta": types.SimpleNamespace(install=lambda a: calls.append(("zeta", a))), "alpha": types.SimpleNamespace(install=lambda a: calls.append(("alpha", a))), "plain": types.SimpleNamespace()}
    monkeypatch.setattr(registry.pkgutil, "iter_modules", lambda path: [FakeInfo(n) for n in ("zeta", "plain", "alpha")])
    monkeypatch.setattr(registry.importlib, "import_module", lambda name: modules[name.rsplit(".", 1)[1]])
    found = registry.discover("the-app")
    assert found == ["alpha", "plain", "zeta"], "in the order of their names"
    assert calls == [("alpha", "the-app"), ("zeta", "the-app")], "install() is called for the modules that have one"
    assert pages.__name__ == "CTFd.plugins.l3mon_theme.pages"


def test_the_login_page_module_is_there_and_the_plugin_installed_it(app):
    # the real folder: pages/login.py carries the sign-in's `next` from the form into the address (its own tests are in test_theme_pages.py)
    from CTFd.plugins.l3mon_theme.pages import login

    assert callable(login.install)
    hooks = [f.__name__ for f in app.before_request_funcs.get(None, [])]
    assert "carry_next_from_the_form_into_the_address" in hooks, hooks
