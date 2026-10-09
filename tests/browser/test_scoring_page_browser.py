"""The crew's scoring page in a real browser, against the running development stack.

Needs: the stack up (tools/compose.sh up -d --wait), Node 22 or newer, and Chrome (or set CHROME). Run:
    python -m pytest tests/browser -q

It seeds three studios and three challenges through the crew's own API (one challenge is named with markup on purpose; the dynamic one
is visible to players and solved by all three studios, then given a stale value behind the platform's back so the page has one to flag), signs in as the organiser from
the stack's own secrets file (nothing is printed), runs scoring_page_check.mjs, and removes everything it made. Skipped, not failed,
when Node or Chrome is missing. A folder of two screenshots is kept when L3MON_SHOTS is set.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "integration"))

import test_release as r  # noqa: E402  (the helpers that talk to the stack)
import test_stack as t  # noqa: E402
from test_release_page_browser import have_a_browser  # noqa: E402

pytestmark = pytest.mark.integration

DYN = "Kiwi Dynamic"
FIX = "Plum Fixed"
MARKUP = "<img src=x onerror=alert(1)>"
PLAYERS = ["scoreplayer1", "scoreplayer2", "scoreplayer3"]
# A plain UPDATE that no hook sees: it puts the old value back after the seeded solves were valued at once. The minute check would put it
# right again before the page could show it, so it is kept from running for the next three minutes.
MAKE_IT_STALE = (
    "from CTFd import create_app\nfrom CTFd.cache import cache, clear_challenges\nfrom CTFd.models import db\n"
    "app = create_app()\nwith app.app_context():\n"
    "    db.session.execute(db.text('update challenges set value = 500 where id = :i'), {'i': %d})\n    db.session.commit()\n"
    "    clear_challenges()\n    cache.set('l3mon:scoring:checked', 1, timeout=170)\n    print('STALE')\n"
)
CLEAN_AUDIT = (
    "from CTFd import create_app\nfrom CTFd.models import db\nfrom CTFd.plugins.l3mon_core.models import Audit\n"
    "app = create_app()\nwith app.app_context():\n    Audit.query.filter(Audit.action.like('scoring.%')).delete(synchronize_session=False)\n    db.session.commit()\n    print('CLEANED')\n"
)


def clean_up():
    for name in PLAYERS:
        for path, field, value in (("/api/v1/teams", "name", f"{name}-team"), ("/api/v1/users", "name", name)):
            status, out = r.admin("GET", f"{path}?field={field}&q={value}")
            for row in (out["data"] if status == 200 else []):
                if row["name"] == value:
                    r.admin("DELETE", f"{path}/{row['id']}")
    for name in (DYN, FIX, MARKUP):
        status, out = r.admin("GET", f"/api/v1/challenges?view=admin&field=name&q={name.split()[0]}")
        for row in (out["data"] if status == 200 else []):
            if row["name"] == name:
                r.admin("DELETE", f"/api/v1/challenges/{row['id']}")
    r.in_ctfd(CLEAN_AUDIT)


@pytest.mark.skipif(shutil.which("node") is None or not have_a_browser(), reason="needs Node and Chrome")
def test_the_scoring_page_works_in_a_real_browser():
    clean_up()
    try:
        made = {}
        specs = {
            DYN: {"value": 500, "initial": 500, "minimum": 200, "decay": 15, "function": "logarithmic", "type": "dynamic", "state": "visible"},
            FIX: {"value": 100, "type": "standard"},
            MARKUP: {"value": 50, "type": "standard"},
        }
        for name, extra in specs.items():
            status, out = r.admin("POST", "/api/v1/challenges", {"name": name, "category": "Test", "description": "plain words", "state": "hidden", **extra})
            assert status == 200, out
            made[name] = out["data"]["id"]
        players = [r.make_player(name) for name in PLAYERS]
        for _, user_id, team_id in players:
            status, out = r.admin("POST", "/api/v1/submissions", {"user_id": user_id, "team_id": team_id, "challenge_id": made[DYN], "provided": "seeded", "type": "correct"})
            assert status == 200, out
        _, user_id, team_id = players[0]
        status, out = r.admin("POST", "/api/v1/submissions", {"user_id": user_id, "team_id": team_id, "challenge_id": made[FIX], "provided": "seeded", "type": "correct"})
        assert status == 200, out
        r.in_ctfd(MAKE_IT_STALE % made[DYN])

        session = t.Session()
        answer = session.open("/login", {"name": "organiser", "password": t.secret("PRESET_ADMIN_PASSWORD"), "_submit": "Submit", "nonce": session.nonce("/login")})
        assert answer.status == 302, f"sign-in answered {answer.status}"
        cookie = next(c.value for c in session.jar if c.name == "session")

        env = {**os.environ, "L3MON_BASE": f"http://localhost:{t.PORT}", "L3MON_COOKIE": cookie,
               "L3MON_WORLD": json.dumps({"dyn": DYN, "fix": FIX, "markup": MARKUP, "studio": f"{PLAYERS[1]}-team"})}
        done = subprocess.run(["node", os.path.join(HERE, "scoring_page_check.mjs")], env=env, capture_output=True, text=True, timeout=240)
        print(done.stdout)
        assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-800:]
    finally:
        clean_up()
