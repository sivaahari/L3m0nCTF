"""tools/ops/backup.sh must never encrypt a backup to a key that anybody can read the private half of.

Found by the integration audit (2026-10-18): when no real recipients file existed, the script fell back to config/backup/recipients.example.txt with a
warning. That file holds the public example key from age's own documentation, whose private key is printed in the same documentation, so a backup made
that way (every player's email and API token inside) could be opened by anyone who finds it. The script now stops before it touches the database."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GIT_BASH = "C:/Program Files/Git/bin/bash.exe"
BASH = os.environ.get("L3MON_BASH") or (GIT_BASH if sys.platform == "win32" and os.path.exists(GIT_BASH) else "bash")
SCRIPT = ROOT / "tools" / "ops" / "backup.sh"
EXAMPLE_KEY = "age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p"


def run(*args, env=None, tmp: Path):
    full = {k: v for k, v in os.environ.items() if k not in ("RECIPIENTS_FILE", "L3MON_ALLOW_EXAMPLE_BACKUP_KEY")}
    full.update(env or {})
    return subprocess.run([BASH, str(SCRIPT), "--full", "--sink", f"file://{tmp.as_posix()}/sink", *args], capture_output=True, text=True, env=full, timeout=60)


def test_without_a_recipients_file_it_stops_and_does_not_fall_back_to_the_example_key(tmp_path):
    # the repository has no config/backup/recipients.txt (it is never committed), only the example file
    assert not (ROOT / "config" / "backup" / "recipients.txt").exists()
    done = run(tmp=tmp_path)
    assert done.returncode != 0
    assert "using example recipients" not in done.stderr
    assert "recipients" in done.stderr.lower()
    assert not (tmp_path / "sink").exists(), "nothing may be written to the sink"


def test_a_recipients_file_that_holds_the_documentation_key_is_refused(tmp_path):
    keyfile = tmp_path / "r.txt"
    keyfile.write_text(f"# a copy of the example\n{EXAMPLE_KEY}\n", encoding="utf-8")
    done = run("--recipients", str(keyfile), tmp=tmp_path)
    assert done.returncode != 0
    assert "example" in done.stderr.lower()
    assert not (tmp_path / "sink").exists()


def test_the_documentation_key_can_be_used_only_when_the_tests_say_so(tmp_path):
    keyfile = tmp_path / "r.txt"
    keyfile.write_text(f"{EXAMPLE_KEY}\n", encoding="utf-8")
    done = run("--recipients", str(keyfile), env={"L3MON_ALLOW_EXAMPLE_BACKUP_KEY": "1"}, tmp=tmp_path)
    # it gets past the key check (and then fails for another reason on a machine with no stack or no age: never because of the key)
    assert "example" not in done.stderr.lower() or "refus" not in done.stderr.lower()


def test_a_missing_explicit_file_is_still_an_error(tmp_path):
    done = run("--recipients", str(tmp_path / "nope.txt"), tmp=tmp_path)
    assert done.returncode != 0 and "recipients" in done.stderr.lower()
