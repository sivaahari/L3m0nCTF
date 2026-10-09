"""`python -m l3mon story build`: compile a story folder into checked bundles.

The checks are the server's own (plugins/l3mon_story/format.py), so what matters here is the build's own job: it assembles a bundle from a script
and the pictures its layers use, cleans what an editor adds, refuses every problem with the file named, writes nothing unless everything passes,
and writes the same bytes every time.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from l3mon import story

ROOT = Path(__file__).resolve().parents[1]
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 90"><rect width="160" height="90" fill="#223"/></svg>'


def script(slug="street", art="sun", **over):
    base = {
        "v": 1, "slug": slug, "title": "Moth Hour", "kicker": "CH 01 · Street", "lang": "en",
        "panels": [{
            "id": "p1", "ms": 4000, "enter": "cut", "alt": "A round sun.",
            "layers": [{"art": art, "x": 0, "y": 0, "w": 100, "h": 100}],
            "bubbles": [{"kind": "say", "text": "Hello.", "x": 5, "y": 5, "w": 30}],
        }],
    }
    base.update(over)
    return base


def folder(tmp_path, scripts=None, library=None):
    src = tmp_path / "src"
    (src / "library").mkdir(parents=True)
    (src / "channels").mkdir()
    for name, text in (library if library is not None else {"sun": SVG}).items():
        (src / "library" / f"{name}.svg").write_text(text, encoding="utf-8")
    for slug, obj in (scripts if scripts is not None else {"street": script()}).items():
        (src / "channels" / f"{slug}.json").write_text(json.dumps(obj), encoding="utf-8")
    return src


def test_a_good_folder_builds_one_bundle_per_channel_and_a_manifest(tmp_path):
    src = folder(tmp_path, {"street": script(), "snack": script("snack")})
    out = tmp_path / "out"
    errors, notes = story.build(src, out)
    assert errors == [] and notes == []
    assert sorted(p.name for p in out.iterdir()) == ["manifest.json", "snack.json", "street.json"]
    bundle = json.loads((out / "street.json").read_text(encoding="utf-8"))
    assert bundle["art"] == {"sun": SVG} and story.fmt.validate_bundle(bundle) == []
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert [m["slug"] for m in manifest] == ["snack", "street"] and manifest[1]["panels"] == 1 and len(manifest[1]["sha256"]) == 64


def test_the_same_input_gives_the_same_bytes(tmp_path):
    src = folder(tmp_path)
    story.build(src, tmp_path / "a")
    story.build(src, tmp_path / "b")
    assert (tmp_path / "a" / "street.json").read_bytes() == (tmp_path / "b" / "street.json").read_bytes()
    assert (tmp_path / "a" / "manifest.json").read_bytes() == (tmp_path / "b" / "manifest.json").read_bytes()


def test_only_the_pictures_a_channel_uses_go_into_its_bundle_and_an_unused_one_is_a_note(tmp_path):
    src = folder(tmp_path, library={"sun": SVG, "moon": SVG})
    errors, notes = story.build(src, tmp_path / "out")
    assert errors == [] and notes == ["library/moon.svg is not used by any channel"]
    assert list(json.loads((tmp_path / "out" / "street.json").read_text())["art"]) == ["sun"]


def test_what_an_editor_adds_is_cleaned_before_the_checks(tmp_path):
    dirty = '﻿<?xml version="1.0" encoding="UTF-8"?>\n<!-- made by hand -->\n' + SVG.replace("><", ">\n  <") + "\n"
    src = folder(tmp_path, library={"sun": dirty})
    errors, _ = story.build(src, tmp_path / "out")
    assert errors == []
    assert json.loads((tmp_path / "out" / "street.json").read_text())["art"]["sun"] == SVG


@pytest.mark.parametrize(
    "scripts,library,needle",
    [
        ({"street": script(art="ghost")}, None, "ghost"),
        ({"street": script("other")}, None, "must be the file's name"),
        ({"street": dict(script(), art={"sun": SVG})}, None, "leave `art` out"),
        ({"street": script(title="")}, None, "title"),
        ({"street": script(lang="english")}, None, "lang"),
        (None, {"sun": SVG.replace("<rect", "<script>alert(1)</script><rect")}, "script"),
        (None, {"Sun": SVG}, "the id"),
        ({}, None, "holds no scripts"),
    ],
)
def test_every_problem_is_refused_with_the_file_named_and_nothing_is_written(tmp_path, scripts, library, needle):
    src = folder(tmp_path, scripts, library)
    out = tmp_path / "out"
    errors, _ = story.build(src, out)
    assert any(needle in e for e in errors), errors
    assert not out.exists()


def test_a_script_that_is_not_json_or_repeats_a_key_is_refused(tmp_path):
    src = folder(tmp_path)
    (src / "channels" / "street.json").write_text('{"v": 1, "v": 1}', encoding="utf-8")
    errors, _ = story.build(src, tmp_path / "out")
    assert errors and "not valid JSON" in errors[0]
    (src / "channels" / "street.json").write_text("{", encoding="utf-8")
    assert story.build(src, tmp_path / "out")[0]
    (src / "channels" / "street.json").write_text("[]", encoding="utf-8")
    assert "must be a JSON object" in story.build(src, tmp_path / "out")[0][0]


def test_one_bad_channel_stops_the_whole_build(tmp_path):
    src = folder(tmp_path, {"street": script(), "snack": script("snack", art="ghost")})
    out = tmp_path / "out"
    errors, _ = story.build(src, out)
    assert errors and not out.exists(), "a good channel is not published beside a bad one"


def test_check_writes_nothing(tmp_path):
    src = folder(tmp_path)
    assert story.main(str(src), None, check=True) == 0
    assert not (tmp_path / "out").exists()


def test_the_command_line_runs_and_fails_with_a_non_zero_status(tmp_path):
    src = folder(tmp_path)
    ok = subprocess.run([sys.executable, "-m", "l3mon", "story", "build", str(src), str(tmp_path / "out")], cwd=ROOT, capture_output=True, text=True)
    assert ok.returncode == 0 and "every channel passes" in ok.stdout
    bad = folder(tmp_path / "bad", {"street": script(art="ghost")})
    failed = subprocess.run([sys.executable, "-m", "l3mon", "story", "build", str(bad), "--check"], cwd=ROOT, capture_output=True, text=True)
    assert failed.returncode == 1 and "ERROR" in failed.stdout
