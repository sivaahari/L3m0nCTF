"""The crew-only gate's route list: one source, no holes, and a decision for every route.

What this guards (19 Nov 2026): the gate (nginx) hides the programmes, scores and flags from players while the crew tests. Its list of routes used to sit
inline in nginx.conf, and three kinds of page were missing from it: the public studio page /teams/<id> (it shows a studio's solves), the story pages and the story
API. Nothing noticed, because nothing forced a decision when a route was added.

Now:
  * the list lives in deploy/nginx/snippets/programme-routes.conf, the only place it is written;
  * every route the plugins register has a recorded decision in DECISIONS below (gated / open / crew / feed / internal), and a route with no decision
    fails the test: whoever adds a route has to say whether players may see it while the gate is closed;
  * every decision is checked against the real list: a "gated" route must be caught by it, an "open" route (registration, sign-in, settings, health) must not be.
The core CTFd routes (/challenges, /scoreboard, /files, /api/v1/...) are not registered by our plugins, so they are checked by CORE_GATED and CORE_OPEN, and on
the real nginx by tests/integration/test_rehearsal_gate.py.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SNIPPET = ROOT / "deploy" / "nginx" / "snippets" / "programme-routes.conf"
NGINX_CONF = ROOT / "deploy" / "nginx" / "nginx.conf"
SLATE = ROOT / "deploy" / "nginx" / "snippets" / "slates" / "not-yet.html"

# The decision for every route our plugins register (the rule exactly as written, with the blueprint's prefix in front).
#   gated    players are shut out while the gate is closed (it carries programmes, scores, flags, studios' solves or the story)
#   open     must keep working while the gate is closed: registration, sign-in, the player's own settings and studio, health, static art
#   crew     the administrator surface: only the crew's host and addresses (nginx's admin rules and CTFd's own guard), never the programme gate's business
#   feed     the public CTFtime feed: the gate answers it with an empty list in its own rule
#   internal metrics for the monitoring stack
DECISIONS = {
    "/auth/google": "open", "/auth/google/callback": "open", "/auth/ctftime": "open", "/auth/ctftime/callback": "open",
    "/l3mon/healthz": "open", "/readyz": "open", "/logout": "open", "/settings": "open", "/team": "open", "/team/members/<int:member_id>/captain": "open", "/team/members/<int:member_id>/remove": "open",
    "/rules": "open", "/privacy": "open", "/favicon.ico": "open", "/favicon.svg": "open", "/art/<name>": "open",
    "/api/v1/l3mon/ticks": "open",  # the header's pulse (versions only); every page polls it, so a refusal would break the header
    # registration (the steps, the password reset, the studio-name check of the wizard): a player who is shut out of the programmes must still be able to finish signing up
    "/register/account": "open", "/register/verify": "open", "/register/studio": "open", "/register/go-live": "open", "/register/done": "open",
    "/reset": "open", "/reset/<data>": "open", "/api/v1/l3mon/teams/available": "open",
    "/l3mon/metrics": "internal",
    "/ctftime/standings.json": "feed", "/ctftime/final-standings.json": "feed",
    "/board": "gated", "/board/<slug>": "gated", "/guide": "gated", "/stories": "gated", "/story/<slug>": "gated", "/teams/<int:team_id>": "gated",
    "/api/v1/l3mon/board": "gated", "/api/v1/l3mon/guide": "gated", "/api/v1/l3mon/guide/epg": "gated",
    "/api/v1/l3mon/scoreboard": "gated", "/api/v1/l3mon/scoreboard/rows": "gated",
    "/api/v1/l3mon/instances": "gated", "/api/v1/l3mon/instances/<challenge_id_param>/renew": "gated", "/api/v1/l3mon/instances/<challenge_id_param>": "gated",
    "/api/v1/l3mon/story": "gated", "/api/v1/l3mon/story/<slug>": "gated", "/api/v1/l3mon/story/<slug>/pic/<name>": "gated",
    "/admin/l3mon/guard": "crew", "/admin/l3mon/release": "crew", "/admin/l3mon/scoring": "crew", "/admin/l3mon/activity": "crew",
    "/plugins/l3mon_guard/assets/<path:name>": "open", "/plugins/l3mon_release/assets/<path:name>": "open", "/plugins/l3mon_scoring/assets/<path:name>": "open",
    "/api/v1/l3mon/admin/guard/evidence": "crew", "/api/v1/l3mon/admin/guard/evidence/<evidence_id>": "crew",
    "/api/v1/l3mon/admin/instances": "crew", "/api/v1/l3mon/admin/instances/kill": "crew",
    "/api/v1/l3mon/admin/release": "crew", "/api/v1/l3mon/admin/programmes": "crew", "/api/v1/l3mon/admin/programmes/<int:programme_id>": "crew",
    "/api/v1/l3mon/admin/channels/<int:channel_id>": "crew",
    "/api/v1/l3mon/admin/removals": "crew", "/api/v1/l3mon/admin/removals/<int:removal_id>/lift": "crew",
    "/api/v1/l3mon/admin/activity": "crew", "/api/v1/l3mon/admin/activity/verify": "crew",
    "/api/v1/l3mon/admin/scoring": "crew", "/api/v1/l3mon/admin/scoring/revoke": "crew", "/api/v1/l3mon/admin/scoring/restore": "crew",
    "/api/v1/l3mon/admin/scoring/bonus": "crew", "/api/v1/l3mon/admin/scoring/recalculate": "crew", "/api/v1/l3mon/admin/scoring/channel-bonus": "crew",
    # the qualifier certificates (27 Nov 2026): the crew's release page and API; a player's own card is on /team (open)
    "/admin/l3mon/certificates": "crew", "/plugins/l3mon_certificates/assets/<path:name>": "open",
    "/api/v1/l3mon/admin/certificates": "crew", "/api/v1/l3mon/admin/certificates/release": "crew",
    "/api/v1/l3mon/certificate/qr.svg": "open",  # the card's picture, the signed-in player's own only (like /team, which carries the card)
}

# Routes of CTFd itself (or of the theme replacing CTFd's page) that the plugins do not add as URL rules.
CORE_GATED = ["/challenges", "/challenges/1", "/scoreboard", "/files/00000000/x.txt", "/api/v1/challenges", "/api/v1/challenges/1", "/api/v1/hints/1",
              "/api/v1/unlocks", "/api/v1/solutions/1", "/api/v1/scoreboard", "/api/v1/scoreboard/top/10", "/api/v1/statistics/challenges/solves",
              "/api/v1/awards", "/api/v1/submissions", "/api/v1/files", "/api/v1/users/me/solves", "/api/v1/teams/me/solves", "/api/v1/teams/3/awards",
              "/api/v1/users/5/fails", "/api/v1/teams/3/submissions"]
CORE_OPEN = ["/", "/login", "/register", "/confirm", "/confirm/abc", "/reset_password", "/reset_password/abc", "/teams/new", "/teams/join", "/teams/invite",
             "/notifications", "/themes/l3mon/static/app.css", "/healthcheck", "/api/v1/users/me", "/api/v1/teams/me", "/api/v1/notifications"]


def patterns() -> list[re.Pattern]:
    """The regular expressions of programme-routes.conf, read the way nginx reads a `map` entry (`~` case-sensitive, `~*` not; `\\z` is the end of the text)."""
    found = []
    for raw in SNIPPET.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        match = re.fullmatch(r'"(~\*?)(.+)"\s+1;', line)
        if match:
            flags = re.IGNORECASE if match.group(1) == "~*" else 0
            found.append(re.compile(match.group(2).replace(r"\z", r"\Z"), flags))
    return found


def gated(path: str) -> bool:
    return any(p.search(path) for p in patterns())


def sample(rule: str) -> str:
    """A real-looking URL for a Flask rule: numbers for <int:...>, a word for <name>, a short path for <path:...>."""
    rule = re.sub(r"<int:[^>]+>", "1", rule)
    rule = re.sub(r"<path:[^>]+>", "a/b.js", rule)
    return re.sub(r"<[^>]+>", "x", rule)


def registered_rules() -> set[str]:
    """Every URL rule the plugins register, found by reading their source (blueprint prefixes included). A rule built from a variable cannot be read and is reported."""
    rules: set[str] = set()
    unreadable: list[str] = []
    for path in sorted((ROOT / "plugins").glob("*/**/*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prefixes: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", getattr(node.value.func, "attr", "")) == "Blueprint":
                prefix = ""
                for kw in node.value.keywords:
                    if kw.arg == "url_prefix" and isinstance(kw.value, ast.Constant):
                        prefix = kw.value.value
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        prefixes[target.id] = prefix
        for node in ast.walk(tree):
            call = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "route":
                        rules.update(_rule_of(dec, dec.func.value, prefixes, path, unreadable))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_url_rule":
                rules.update(_rule_of(node, node.func.value, prefixes, path, unreadable))
    assert not unreadable, f"routes built from a variable cannot be checked, write them as plain strings (or as the keys of a module-level ALIASES dictionary): {unreadable}"
    return rules


def _alias_keys(tree) -> list[str]:
    """The keys of a module-level `ALIASES = {"/path": ...}` dictionary: pages/routes.py registers one redirect per key in a loop."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ALIASES" for t in node.targets) and isinstance(node.value, ast.Dict):
            return [k.value for k in node.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    return []


def _rule_of(call, owner, prefixes, path, unreadable) -> list[str]:
    if not call.args:
        return []
    first = call.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        keys = _alias_keys(ast.parse(path.read_text(encoding="utf-8")))
        if keys:
            return keys
        unreadable.append(f"{path.relative_to(ROOT)}:{call.lineno}")
        return []
    prefix = prefixes.get(owner.id, "") if isinstance(owner, ast.Name) else ""
    return [prefix + first.value]


# ------------------------------------------------------------------ one list

def test_the_list_is_written_in_one_file_and_nginx_reads_that_file():
    conf = NGINX_CONF.read_text(encoding="utf-8")
    block = conf.split("map $uri $programme_route {", 1)[1].split("}", 1)[0]
    assert "include /etc/nginx/snippets/programme-routes.conf;" in block
    assert "~^/" not in block, "a route written inline in nginx.conf is a second list that the tests do not read"
    assert patterns(), "the list is empty"


def test_the_list_file_has_a_default_of_open_and_only_one_pattern_per_line():
    text = SNIPPET.read_text(encoding="utf-8")
    assert re.search(r"^default 0;", text, re.MULTILINE)
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        assert not line or line == "default 0;" or re.fullmatch(r'"~\*?.+"\s+1;', line), f"not a list entry: {raw!r}"


# ------------------------------------------------------------------ every route has a decision

def test_every_route_the_plugins_register_has_a_recorded_decision():
    undecided = sorted(registered_rules() - set(DECISIONS))
    assert not undecided, ("these routes have no decision about the crew-only gate: say in DECISIONS (tools/tests/test_gate_routes.py) whether players may see them "
                           "while the gate is closed, and if they may not, add them to deploy/nginx/snippets/programme-routes.conf: " + ", ".join(undecided))


def test_a_decision_for_a_route_that_no_longer_exists_is_removed():
    stale = sorted(set(DECISIONS) - registered_rules())
    assert not stale, f"decisions for routes that are gone (delete them): {stale}"


@pytest.mark.parametrize("rule", sorted(r for r, d in DECISIONS.items() if d == "gated"))
def test_a_route_decided_gated_is_caught_by_the_list(rule):
    assert gated(sample(rule)), f"{rule} is decided 'gated' but the list does not catch {sample(rule)}"


@pytest.mark.parametrize("rule", sorted(r for r, d in DECISIONS.items() if d in ("open", "feed", "internal")))
def test_a_route_decided_open_is_not_caught_by_the_list(rule):
    assert not gated(sample(rule)), f"{rule} must keep working while the gate is closed, but the list catches {sample(rule)}"


@pytest.mark.parametrize("rule", sorted(r for r, d in DECISIONS.items() if d == "crew"))
def test_a_crew_route_lives_under_an_administrator_prefix(rule):
    assert rule.startswith(("/admin/", "/api/v1/l3mon/admin/")), rule


# ------------------------------------------------------------------ the routes of CTFd itself

@pytest.mark.parametrize("path", CORE_GATED)
def test_the_core_programme_routes_are_gated(path):
    assert gated(path), path


@pytest.mark.parametrize("path", CORE_OPEN)
def test_registration_sign_in_and_the_players_own_pages_are_never_gated(path):
    assert not gated(path), path


def test_a_studio_page_is_gated_but_the_join_and_invite_pages_are_not():
    assert gated("/teams/7") and gated("/teams/7/") and gated("/teams/123")
    assert not gated("/teams/new") and not gated("/teams/join") and not gated("/teams/invite") and not gated("/team")


# ------------------------------------------------------------------ the page a shut-out player sees

def test_the_not_on_air_page_is_not_a_dead_end():
    html = SLATE.read_text(encoding="utf-8")
    for href in ("/team", "/settings", "/login"):
        assert f'href="{href}"' in html, f"the page has no way to {href}"
        assert not gated(href), f"{href} is linked from the not-on-air page, so it must work while the gate is closed"
    assert "<script" not in html, "the page is static on purpose: it must work when the platform is down"
