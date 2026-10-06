"""The hygiene scanner keeps flags, secrets, dumps and story material out of the public repository."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l3mon import hygiene  # noqa: E402

# Built from pieces, so this file does not trip the scanner it tests.
OPEN = "L3m0nCTF" + "{"
PEM = "-----BEGIN " + "PRIVATE KEY-----"


def make(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def rules(findings) -> set[str]:
    return {f.rule for f in findings}


def test_a_clean_tree_has_no_findings(tmp_path):
    make(tmp_path, {"README.md": "Nothing to see here.\n", "docs/x.md": "The flag format is " + OPEN + "...}.\n"})
    assert hygiene.scan(tmp_path) == []


def test_a_real_looking_flag_is_found(tmp_path):
    make(tmp_path, {"notes.md": OPEN + "ab12" * 8 + "}\n"})
    found = hygiene.scan(tmp_path)
    assert rules(found) == {"flag"}
    assert found[0].path == "notes.md" and found[0].line == 1


def test_any_flag_with_content_is_found_but_the_hint_is_not(tmp_path):
    make(tmp_path, {"a.md": OPEN + "something_else}\n", "b.md": OPEN + "...}\n"})
    found = hygiene.scan(tmp_path)
    assert [f.path for f in found] == ["a.md"]


def test_private_keys_are_found(tmp_path):
    make(tmp_path, {"k.txt": PEM + "\nabc\n"})
    assert rules(hygiene.scan(tmp_path)) == {"private-key"}


def test_token_shapes_are_found(tmp_path):
    make(tmp_path, {"t.txt": "aws AK" + "IA" + "A" * 16 + "\n", "g.txt": "gh gh" + "p_" + "a" * 36 + "\n"})
    assert rules(hygiene.scan(tmp_path)) == {"token"}


def test_dumps_and_key_files_are_found_by_name(tmp_path):
    make(tmp_path, {"backup.sql": "x", "data.sqlite": "x", "id.pem": "x", ".env": "A=1", "deploy/.env.example": "A=\n"})
    found = hygiene.scan(tmp_path)
    assert {f.path for f in found} == {"backup.sql", "data.sqlite", "id.pem", ".env"}
    assert rules(found) == {"dump", "key-file", "env-file"}


def test_deny_words_are_whole_words_and_ignore_case(tmp_path):
    make(tmp_path, {"a.md": "The Tara and Lag appear.\n", "b.md": "A stalagmite and a plaza.\n"})
    found = hygiene.scan(tmp_path, deny_words=["tara", "lag"])
    assert [f.path for f in found] == ["a.md"]  # one finding per line
    assert rules(found) == {"deny-word"}


def test_tool_folders_and_nested_repositories_are_not_scanned_outside_git(tmp_path):
    bad = OPEN + "abcdefgh}"
    make(tmp_path, {".git/x": bad, "node_modules/x": bad, ".secrets/x": bad, "private/.git/HEAD": "ref", "private/x": bad})
    assert hygiene.scan(tmp_path) == []  # private/ holds its own .git: it is a different repository
    make(tmp_path, {"build/x": bad, "dist/x": bad})
    assert {f.path for f in hygiene.scan(tmp_path)} == {"build/x", "dist/x"}  # build and dist are scanned: leaks hide there


def test_a_folder_named_private_is_scanned_when_it_is_not_a_repository(tmp_path):
    make(tmp_path, {"private/x": OPEN + "abcdefgh}"})
    assert {f.path for f in hygiene.scan(tmp_path)} == {"private/x"}


def test_a_harmless_binary_is_fine_and_one_holding_the_prefix_is_not(tmp_path):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x00\x00" + bytes(range(256)) * 20)
    assert hygiene.scan(tmp_path) == []
    (tmp_path / "chal.png").write_bytes(b"\x89PNG\r\n\x00\x00" + OPEN.encode() + b"abcdefgh}")
    assert rules(hygiene.scan(tmp_path)) == {"flag-binary"}


def test_the_command_exits_one_with_findings_and_zero_without(tmp_path, capsys):
    from l3mon.cli import main

    make(tmp_path, {"ok.md": "fine\n"})
    assert main(["hygiene", "--root", str(tmp_path)]) == 0
    make(tmp_path, {"bad.md": OPEN + "abcdefgh}\n"})
    assert main(["hygiene", "--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "bad.md:1" in out and "flag" in out


def test_the_deny_words_file_is_read(tmp_path):
    from l3mon.cli import main

    make(tmp_path / "repo", {"a.md": "the Glitch is here\n"})
    words = tmp_path / "words.txt"
    words.write_text("# story words, kept outside the repository\nGlitch\n", encoding="utf-8")
    assert main(["hygiene", "--root", str(tmp_path / "repo"), "--deny-words-file", str(words)]) == 1
