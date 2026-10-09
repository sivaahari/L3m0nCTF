"""The crew's release page in a real browser, against the running development stack.

Needs: the stack up (tools/compose.sh up -d --wait), Node 22 or newer, and Chrome (or set CHROME). Run:
    python -m pytest tests/browser -q

It seeds a small plan through the crew's own API (one challenge is named with markup on purpose), signs in as the organiser from the
stack's own secrets file (nothing is printed), runs release_page_check.mjs, and removes everything it made. Skipped, not failed,
when Node or Chrome is missing. A folder of two screenshots is kept when L3MON_SHOTS is set.
"""
from __future__ import annotations

import calendar
import datetime
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "integration"))

import test_release as r  # noqa: E402  (the helpers that talk to the stack)
import test_stack as t  # noqa: E402

pytestmark = pytest.mark.integration

NAMES = ["Wrestler Padding", "Midnight Mango", "Secret Sauce", "<img src=x onerror=alert(1)>", "Chase Logger"]


def have_a_browser() -> bool:
    candidates = [os.environ.get("CHROME"), "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
                  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    return any(c and os.path.exists(c) for c in candidates)


def clean_up():
    for name in NAMES:
        status, out = r.admin("GET", f"/api/v1/challenges?view=admin&field=name&q={name.split()[0]}")
        for row in (out["data"] if status == 200 else []):
            if row["name"] == name:
                r.admin("DELETE", f"/api/v1/challenges/{row['id']}")
    r.in_ctfd(r.CLEANUP)


@pytest.mark.skipif(shutil.which("node") is None or not have_a_browser(), reason="needs Node and Chrome")
def test_the_release_page_works_in_a_real_browser():
    clean_up()
    try:
        ids = []
        for n, name in enumerate(NAMES):
            status, out = r.admin("POST", "/api/v1/challenges", {"name": name, "category": "Test", "description": "plain words", "value": 100 + n, "state": "hidden", "type": "standard"})
            assert status == 200, out
            ids.append(out["data"]["id"])
        status, out = r.admin("PUT", r.PROGRAMMES, {
            "channels": [
                {"slug": "street", "name": "Mighty Street", "position": 1, "synopsis": "A loud street."},
                {"slug": "snack", "name": "Snack Square", "position": 2},
                {"slug": "break", "name": "Sponsored Break", "position": 7, "kind": "sponsored", "sponsor_name": "Acme"},
            ],
            "programmes": [
                {"challenge_id": ids[0], "channel": "street", "cell": 0, "number": 101, "slug": "padding"},
                {"challenge_id": ids[1], "channel": "street", "cell": 1, "number": 102, "slug": "mango"},
                {"challenge_id": ids[2], "channel": "snack", "cell": 0, "number": 201, "slug": "sauce"},
                {"challenge_id": ids[3], "channel": "snack", "cell": 1, "number": 202, "slug": "markup"},
                {"challenge_id": ids[4], "channel": "break", "cell": 0, "number": 701, "slug": "logger"},
            ],
        })
        assert status == 200, out
        status, view = r.admin("GET", r.RELEASE)
        channels = {c["slug"]: c["id"] for c in view["data"]["channels"]}
        programmes = {p["slug"]: p["id"] for c in view["data"]["channels"] for p in c["programmes"]}
        r.change({"kind": "channel", "id": channels["street"], "mode": "release"}, {"kind": "programme", "id": programmes["padding"], "mode": "release"})

        session = t.Session()
        answer = session.open("/login", {"name": "organiser", "password": t.secret("PRESET_ADMIN_PASSWORD"), "_submit": "Submit", "nonce": session.nonce("/login")})
        assert answer.status == 302, f"sign-in answered {answer.status}"
        cookie = next(c.value for c in session.jar if c.name == "session")

        ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        tomorrow = (datetime.datetime.now(ist) + datetime.timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        env = {**os.environ, "L3MON_BASE": f"http://localhost:{t.PORT}", "L3MON_COOKIE": cookie, "L3MON_TOMORROW_IST_EPOCH": str(calendar.timegm(tomorrow.astimezone(datetime.timezone.utc).timetuple()))}
        done = subprocess.run(["node", os.path.join(HERE, "release_page_check.mjs")], env=env, capture_output=True, text=True, timeout=240)
        print(done.stdout)
        assert done.returncode == 0, done.stdout[-2500:] + done.stderr[-800:]
    finally:
        clean_up()
