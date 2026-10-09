"""The cold-open comic's player: its clock in Node, and the whole player in a real browser.

Needs Node 22 or newer, and Chrome for the second test (or set CHROME). Neither needs the platform or a network: the browser check serves the
player's own files and a made-up story from a small local server. Skipped, not failed, when Node or Chrome is missing. Run:
    python -m pytest tests/browser/test_story_player_browser.py -q
A folder of screenshots is kept when L3MON_SHOTS is set.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from test_release_page_browser import have_a_browser  # noqa: E402


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_the_clock_of_the_cold_open_as_pure_functions():
    done = subprocess.run(["node", "--test", os.path.join(HERE, "story_timeline.test.mjs")], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-800:]


@pytest.mark.skipif(shutil.which("node") is None or not have_a_browser(), reason="needs Node and Chrome")
def test_the_player_works_in_a_real_browser():
    done = subprocess.run(["node", os.path.join(HERE, "story_player_check.mjs")], env={**os.environ}, capture_output=True, text=True, timeout=300)
    print(done.stdout)
    assert done.returncode == 0, done.stdout[-3500:] + done.stderr[-800:]
