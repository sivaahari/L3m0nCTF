"""Login with CTFtime on the real stack: it must be OFF by default (no event number, no secret), through nginx, and no secret may sit in the
container's environment. The flow itself (state, token, profile, accounts, studios, every failure) is proved in the plugin's tests
against a stand-in CTFtime, because the real one needs an approved event and the stack may not call out.

Same prerequisites and runner as test_stack.py.
"""
from __future__ import annotations

import pytest

import test_stack as t

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("path", ["/auth/ctftime", "/auth/ctftime/", "/auth/ctftime/callback", "/auth/ctftime/callback?code=x&state=y"])
def test_01_without_an_event_number_and_a_secret_the_sign_in_routes_do_not_exist(path):
    status, pairs, _ = t.request(path)
    assert status == 404, (path, status)
    assert t.header_values(pairs, "X-Content-Type-Options") == ["nosniff"], "nginx's own headers are there"


def test_02_the_event_number_reaches_the_container_and_the_secret_is_a_file_never_an_environment_variable():
    env = dict(line.split("=", 1) for line in t.inspect("ctfd")["Config"]["Env"] if "=" in line)
    assert env.get("CTFTIME_CLIENT_ID") == "", "empty until CTFtime approves the event (event.toml, [ctftime])"
    assert env.get("CTFTIME_CLIENT_SECRET_FILE") == "/run/secrets/CTFTIME_CLIENT_SECRET"
    assert "CTFTIME_CLIENT_SECRET" not in env and env.get("L3MON_PLATFORM_HOST") == t.HOST
