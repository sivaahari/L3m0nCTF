"""The nginx rules for CTFd's administrator API: how routes are read and how they become regular expressions."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l3mon import api_rules  # noqa: E402


def route(method, path, guard):
    return {"method": method, "path": path, "guard": guard}


ROUTES = [
    route("GET", "/api/v1/teams", "open"),
    route("POST", "/api/v1/teams", "admin"),
    route("PATCH", "/api/v1/teams/me", "player"),
    route("PATCH", "/api/v1/teams/<int:team_id>", "admin"),
    route("POST", "/api/v1/teams/me/members", "player"),
    route("POST", "/api/v1/teams/<team_id>/members", "admin"),
    route("DELETE", "/api/v1/tokens/<token_id>", "player"),
    route("GET", "/api/v1/submissions", "admin"),
    route("POST", "/api/v1/challenges/attempt", "player"),
]


def lines(name):
    text = api_rules.render(ROUTES)[name]
    return [l for l in text.splitlines() if l and not l.startswith("#")]


def matches(line, method, path):
    pattern = re.match(r'^"~(.+)" 1;$', line).group(1)
    return re.search(pattern, f"{method}:{path}") is not None


def any_match(name, method, path):
    return any(matches(l, method, path) for l in lines(name))


def test_admin_routes_become_expressions_that_match_real_paths_with_or_without_a_trailing_slash():
    assert any_match("api-admin-routes.conf", "GET", "/api/v1/submissions")
    assert any_match("api-admin-routes.conf", "GET", "/api/v1/submissions/")
    assert any_match("api-admin-routes.conf", "PATCH", "/api/v1/teams/12")
    assert any_match("api-admin-routes.conf", "POST", "/api/v1/teams/12/members")


def test_a_variable_never_swallows_the_fixed_word_of_a_sibling_route():
    # the bug the first integration run found: /teams/<team_id>/members (admin) also matched a player's own /teams/me/members
    assert not any_match("api-admin-routes.conf", "POST", "/api/v1/teams/me/members")
    assert not any_match("api-admin-routes.conf", "PATCH", "/api/v1/teams/me")
    assert any_match("api-player-writes.conf", "POST", "/api/v1/teams/me/members")
    assert any_match("api-player-writes.conf", "PATCH", "/api/v1/teams/me")


def test_integer_variables_only_match_digits():
    assert not any_match("api-admin-routes.conf", "PATCH", "/api/v1/teams/abc")


def test_only_non_admin_writes_are_player_writes_and_reads_are_never_listed_there():
    player = lines("api-player-writes.conf")
    assert len(player) == 4  # PATCH teams/me, POST teams/me/members, DELETE tokens/<id>, POST challenges/attempt
    assert not any_match("api-player-writes.conf", "GET", "/api/v1/teams")
    assert not any_match("api-player-writes.conf", "POST", "/api/v1/teams")  # an admin write


def test_an_admin_route_on_a_path_players_read_is_listed_by_method_only():
    assert any_match("api-admin-routes.conf", "POST", "/api/v1/teams")
    assert not any_match("api-admin-routes.conf", "GET", "/api/v1/teams")


def test_the_files_start_with_the_generated_notice_and_end_with_a_newline():
    for text in api_rules.render(ROUTES).values():
        assert text.startswith("# GENERATED") and text.endswith("\n")


def test_extract_reads_prefixes_routes_and_guards_from_a_source_tree(tmp_path):
    api = tmp_path / "CTFd" / "api"
    (api / "v1").mkdir(parents=True)
    (api / "__init__.py").write_text(
        "from CTFd.api.v1.widgets import widgets_namespace\n"
        "CTFd_API_v1.add_namespace(widgets_namespace, '/widgets')\n",
        encoding="utf-8",
    )
    (api / "v1" / "widgets.py").write_text(
        "@widgets_namespace.route('')\n"
        "class WidgetList(Resource):\n"
        "    def get(self):\n        pass\n"
        "    @admins_only\n    def post(self):\n        pass\n"
        "@widgets_namespace.route('/<int:widget_id>')\n"
        "class Widget(Resource):\n"
        "    @authed_only\n    def patch(self, widget_id):\n        pass\n"
        "    @admins_only\n    def delete(self, widget_id):\n        pass\n",
        encoding="utf-8",
    )
    got = {(r["method"], r["path"], r["guard"]) for r in api_rules.extract(str(tmp_path))}
    assert got == {
        ("GET", "/api/v1/widgets", "open"),
        ("POST", "/api/v1/widgets", "admin"),
        ("PATCH", "/api/v1/widgets/<int:widget_id>", "player"),
        ("DELETE", "/api/v1/widgets/<int:widget_id>", "admin"),
    }
