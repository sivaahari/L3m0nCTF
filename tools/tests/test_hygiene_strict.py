"""The stricter hygiene rules added after the independent audit: encodings, binaries, more token shapes, git awareness, history."""
from __future__ import annotations

import base64
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l3mon import hygiene  # noqa: E402

# Built from pieces, so this file does not trip the scanner it tests.
OPEN = "L3m0n" + "{"
RETIRED = "L3m0nCTF" + "{"  # the opening the event used before, still looked for


def make(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def rules(findings) -> set[str]:
    return {f.rule for f in findings}


def test_the_flag_prefix_is_found_in_any_letter_case_and_encoding(tmp_path):
    raw = OPEN.encode()
    make(
        tmp_path,
        {
            "lower.md": OPEN.lower() + "abcdefgh}\n",
            "upper.md": OPEN.upper() + "ABCDEFGH}\n",
            "url.md": "l3m0nctf" + "%7B" + "abc\n",
            "html.md": "L3m0nCTF" + "&#123;" + "abc\n",
            "hex.md": "data " + raw.hex() + "\n",
            "b64.md": "data " + base64.b64encode(raw + b"xyz").decode() + "\n",
            "b64b.md": "data " + base64.b64encode(b"q" + raw + b"xyz").decode() + "\n",
            "b64c.md": "data " + base64.b64encode(b"qq" + raw + b"xyz").decode() + "\n",
        },
    )
    (tmp_path / "utf16.txt").write_bytes(b"\x00" + OPEN.encode("utf-16-le"))
    found = {f.path for f in hygiene.scan(tmp_path)}
    assert found == {"lower.md", "upper.md", "url.md", "html.md", "hex.md", "b64.md", "b64b.md", "b64c.md", "utf16.txt"}


def test_the_format_hint_stays_allowed_in_text_in_any_case(tmp_path):
    make(tmp_path, {"a.md": "Format: " + OPEN + "...}\n", "b.md": "format: " + OPEN.lower() + "...}\n"})
    assert hygiene.scan(tmp_path) == []


def test_more_token_shapes_are_found(tmp_path):
    make(
        tmp_path,
        {
            "ctfd.txt": "token ctfd" + "_" + "a1" * 32 + "\n",
            "pat.txt": "github" + "_pat_" + "A" * 30 + "\n",
            "google.txt": "key AI" + "za" + "A" * 35 + "\n",
            "oauth.txt": "secret GOCSPX" + "-" + "a" * 24 + "\n",
        },
    )
    found = hygiene.scan(tmp_path)
    assert {f.path for f in found} == {"ctfd.txt", "pat.txt", "google.txt", "oauth.txt"}
    assert rules(found) == {"token"}


def test_archives_captures_and_executables_are_found_by_name(tmp_path):
    make(tmp_path, {"files.zip": "x", "dump.pcap": "x", "run.exe": "x", "chal.elf": "x", "ok.png": "x"})
    found = hygiene.scan(tmp_path)
    assert {f.path for f in found} == {"files.zip", "dump.pcap", "run.exe", "chal.elf"}
    assert rules(found) == {"artifact"}


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args], cwd=cwd, check=True, capture_output=True)


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@needs_git
def test_inside_a_git_repository_only_tracked_and_unignored_files_are_scanned(tmp_path):
    bad = OPEN + "abcdefgh}\n"
    make(tmp_path, {".gitignore": "ignored/\n", "ignored/x.md": bad, "tracked.md": "fine\n", "untracked_bad.md": bad})
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".gitignore", "tracked.md")
    git(tmp_path, "commit", "-q", "-m", "first")
    # an untracked file that git would add counts; an ignored one does not
    assert {f.path for f in hygiene.scan(tmp_path)} == {"untracked_bad.md"}
    make(tmp_path, {"private/x.md": bad})  # content in a folder called private is NOT skipped inside git
    git(tmp_path, "add", "private/x.md")
    assert "private/x.md" in {f.path for f in hygiene.scan(tmp_path)}


@needs_git
def test_history_finds_a_secret_that_was_deleted_from_the_tree(tmp_path):
    make(tmp_path, {"config.md": "token ctfd" + "_" + "b2" * 32 + "\n"})
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "config.md")
    git(tmp_path, "commit", "-q", "-m", "oops")
    make(tmp_path, {"config.md": "removed\n"})
    git(tmp_path, "add", "config.md")
    git(tmp_path, "commit", "-q", "-m", "fix")
    assert hygiene.scan(tmp_path) == []
    found = hygiene.scan_history(tmp_path)
    assert rules(found) == {"token"} and found[0].path.startswith("config.md@")


@needs_git
def test_a_clean_history_has_no_findings(tmp_path):
    make(tmp_path, {"a.md": "fine\n"})
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "a.md")
    git(tmp_path, "commit", "-q", "-m", "ok")
    assert hygiene.scan_history(tmp_path) == []


def test_the_retired_opening_is_still_found_in_every_form(tmp_path):
    raw = RETIRED.encode()
    make(
        tmp_path,
        {
            "plain.md": RETIRED + "abcdefgh}\n",
            "upper.md": RETIRED.upper() + "ABCDEFGH}\n",
            "url.md": "L3m0nCTF" + "%7B" + "abc\n",
            "hex.md": "data " + raw.hex() + "\n",
            "b64.md": "data " + base64.b64encode(raw + b"xyz").decode() + "\n",
            "b64b.md": "data " + base64.b64encode(b"q" + raw + b"xyz").decode() + "\n",
            "b64c.md": "data " + base64.b64encode(b"qq" + raw + b"xyz").decode() + "\n",
        },
    )
    (tmp_path / "utf16.txt").write_bytes(b"\x00" + RETIRED.encode("utf-16-le"))
    found = {f.path for f in hygiene.scan(tmp_path)}
    assert found == {"plain.md", "upper.md", "url.md", "hex.md", "b64.md", "b64b.md", "b64c.md", "utf16.txt"}


def test_the_retired_format_hint_is_not_a_leak_but_the_current_one_is_the_documented_form(tmp_path):
    make(tmp_path, {"a.md": "Old: " + RETIRED + "...}\n", "b.md": "Now: " + OPEN + "...}\n"})
    assert hygiene.scan(tmp_path) == []
