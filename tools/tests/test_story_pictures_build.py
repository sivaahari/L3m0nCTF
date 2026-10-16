"""`python -m l3mon story build` for picture stories (format version 2).

The author's folder holds `channels/<slug>.json` (the script, with `"v": 2`, layers that name a picture by id, and the painted words as
`lines`) and `pictures/<slug>/<id>.webp`. The build works out what the bundle says about each picture (size in pixels from the file's own
header, bytes and SHA-256), refuses anything that is not a plain, still WebP of a sensible size, copies the pictures it uses to
`<out>/pics/<slug>/<id>.webp` (where the platform looks) and writes the bundle. Nothing is written unless every channel passes, and the same
input gives the same bytes. The checks of the bundle itself are the server's (plugins/l3mon_story/format.py).
"""
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from l3mon import story

ROOT = Path(__file__).resolve().parents[1]


def riff(chunks: bytes) -> bytes:
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WEBP" + chunks


def chunk(fourcc: bytes, body: bytes) -> bytes:
    return fourcc + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 else b"")


def lossy(w=1600, h=893, pad=64) -> bytes:
    frame = bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + struct.pack("<HH", w, h)  # a key frame: tag, start code, size
    return riff(chunk(b"VP8 ", frame + b"\0" * pad))


def lossy_raw(width_field, height_field, pad=64) -> bytes:
    """A lossy header with the two size fields exactly as given (the top two bits of each are a scale, not part of the size)."""
    frame = bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + struct.pack("<HH", width_field, height_field)
    return riff(chunk(b"VP8 ", frame + b"\0" * pad))


def lossless(w=1600, h=893, pad=64) -> bytes:
    bits = (w - 1) | ((h - 1) << 14)
    return riff(chunk(b"VP8L", b"\x2f" + struct.pack("<I", bits) + b"\0" * pad))


def extended(w=1600, h=893, animated=False) -> bytes:
    flags = 0x02 if animated else 0x00
    head = bytes([flags]) + b"\0\0\0" + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return riff(chunk(b"VP8X", head) + chunk(b"VP8 ", bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + struct.pack("<HH", w, h) + b"\0" * 32))


def script(slug="street", pics=("p1", "p2"), **over):
    base = {
        "v": 2, "slug": slug, "title": "Moth Hour", "kicker": "CH 01 · Street", "lang": "en", "flip": True,
        "panels": [
            {
                "id": name, "ms": 9000, "enter": "static", "alt": f"Picture {name}.",
                "layers": [{"pic": name, "x": 0, "y": 0, "w": 100, "h": 100}],
                "lines": [{"kind": "say", "who": "Host", "text": "Now!"}],
            }
            for name in pics
        ],
    }
    base.update(over)
    return base


def folder(tmp_path, scripts=None, files=None, library=False):
    """-> src. `files`: {slug: {id: bytes}} (by default two good pictures for `street`)."""
    src = tmp_path / "src"
    (src / "channels").mkdir(parents=True)
    if library:
        (src / "library").mkdir()
    for slug, obj in (scripts if scripts is not None else {"street": script()}).items():
        (src / "channels" / f"{slug}.json").write_text(json.dumps(obj), encoding="utf-8")
    for slug, pictures in (files if files is not None else {"street": {"p1": lossy(), "p2": lossless()}}).items():
        (src / "pictures" / slug).mkdir(parents=True, exist_ok=True)
        for name, data in pictures.items():
            (src / "pictures" / slug / f"{name}.webp").write_bytes(data)
    return src


# ---- the header ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("data,expected", [
    (lossy(1600, 893), (1600, 893)), (lossy(320, 320), (320, 320)), (lossy(16383, 1), (16383, 1)), (lossy_raw(800 | 0x8000, 600 | 0x4000), (800, 600)),
    (lossless(1600, 893), (1600, 893)), (lossless(1, 16384), (1, 16384)),
    (extended(1600, 893), (1600, 893)), (extended(4096, 4096), (4096, 4096)),
])
def test_the_size_of_a_picture_is_read_from_its_own_header(data, expected):
    assert story.webp_size(data) == expected


def test_the_size_agrees_with_a_real_encoder():
    from io import BytesIO

    PIL = pytest.importorskip("PIL.Image")
    for mode, kwargs in (("lossy", {"quality": 60}), ("lossless", {"lossless": True})):
        out = BytesIO()
        PIL.new("RGB", (640, 360), (10, 80, 200)).save(out, "WEBP", **kwargs)
        assert story.webp_size(out.getvalue()) == (640, 360), mode


@pytest.mark.parametrize("label,data", [
    ("nothing", b""), ("too short", b"RIFF"), ("not riff", b"RIFX" + lossy()[4:]), ("not webp", lossy()[:8] + b"WAVE" + lossy()[12:]),
    ("a riff length that is wrong", lossy()[:4] + struct.pack("<I", 9999) + lossy()[8:]),
    ("a file with something added after it", lossy() + b"extra"),
    ("a truncated file", lossy()[:-10]),
    ("an animation", extended(animated=True)),
    ("a chunk nobody knows", riff(chunk(b"JUNK", b"\0" * 40))),
    ("a lossy frame without its start code", riff(chunk(b"VP8 ", bytes([0x10, 0x02, 0x00]) + b"\x00\x00\x00" + struct.pack("<HH", 800, 600) + b"\0" * 32))),
    ("a lossless frame without its signature", riff(chunk(b"VP8L", b"\x00" + struct.pack("<I", 5) + b"\0" * 32))),
    ("a header cut short", riff(chunk(b"VP8 ", b"\x10\x02"))),
    ("a text file", b"<html>not a picture</html>" * 4),
    ("a png", b"\x89PNG\r\n\x1a\n" + b"\0" * 40),
    ("junk chunks inside the file", riff(chunk(b"VP8 ", bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + struct.pack("<HH", 640, 360) + b"\0" * 24) + chunk(b"JUNK", b"x" * 300))),
    ("a picture chunk that lies about its size", riff(b"VP8 " + struct.pack("<I", 10) + bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + struct.pack("<HH", 640, 360) + b"\0" * 24)),
    ("a canvas that disagrees with the picture", riff(chunk(b"VP8X", bytes([0, 0, 0, 0]) + (639).to_bytes(3, "little") + (359).to_bytes(3, "little")) + chunk(b"VP8L", b"\x2f" + struct.pack("<I", 4095 | (4095 << 14)) + b"\0" * 64))),
    ("an animation frame chunk with the animation flag off", riff(chunk(b"VP8X", bytes([0, 0, 0, 0]) + (639).to_bytes(3, "little") + (359).to_bytes(3, "little")) + chunk(b"ANMF", b"\0" * 40))),
    ("two pictures in one file", riff(chunk(b"VP8L", b"\x2f" + struct.pack("<I", 639 | (359 << 14)) + b"\0" * 20) * 2)),
    ("metadata hidden in the file", riff(chunk(b"VP8 ", bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + struct.pack("<HH", 640, 360) + b"\0" * 24) + chunk(b"EXIF", b"secret" * 10))),
    ("no picture at all", riff(chunk(b"VP8X", bytes([0, 0, 0, 0]) + (639).to_bytes(3, "little") + (359).to_bytes(3, "little")))),
    ("a chunk that claims more than the file holds", riff(b"VP8 " + struct.pack("<I", 5000) + bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + struct.pack("<HH", 640, 360) + b"\0" * 24)),
    ("stray bytes after the last chunk", riff(chunk(b"VP8 ", bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + struct.pack("<HH", 640, 360) + b"\0" * 24) + b"abc")),
    ("the extended header after the picture", riff(chunk(b"VP8L", b"/" + struct.pack("<I", 639 | (359 << 14)) + b"\0" * 20) + chunk(b"VP8X", bytes([0, 0, 0, 0]) + (639).to_bytes(3, "little") + (359).to_bytes(3, "little")))),
])
def test_a_file_that_is_not_a_plain_still_webp_has_no_size(label, data):
    assert story.webp_size(data) is None, label


# ---- the good folder -------------------------------------------------------------------------------------------------------

def test_a_good_folder_builds_the_bundle_and_copies_the_pictures_where_the_platform_looks(tmp_path):
    src = folder(tmp_path)
    out = tmp_path / "out"
    errors, notes = story.build(src, out)
    assert errors == [] and notes == []
    assert sorted(p.name for p in out.iterdir()) == ["manifest.json", "pics", "street.json"]
    assert (out / "pics" / "street" / "p1.webp").read_bytes() == lossy() and (out / "pics" / "street" / "p2.webp").read_bytes() == lossless()
    bundle = json.loads((out / "street.json").read_text(encoding="utf-8"))
    assert bundle["v"] == 2 and "art" not in bundle and story.fmt.validate_bundle(bundle) == []
    assert bundle["pics"] == {
        "p1": {"w": 1600, "h": 893, "bytes": len(lossy()), "sha256": hashlib.sha256(lossy()).hexdigest()},
        "p2": {"w": 1600, "h": 893, "bytes": len(lossless()), "sha256": hashlib.sha256(lossless()).hexdigest()},
    }
    assert bundle["flip"] is True and bundle["panels"][0]["lines"] == [{"kind": "say", "who": "Host", "text": "Now!"}]


def test_the_manifest_counts_the_pictures(tmp_path):
    src = folder(tmp_path)
    story.build(src, tmp_path / "out")
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == [{
        "slug": "street", "title": "Moth Hour", "panels": 2, "bytes": manifest[0]["bytes"], "sha256": manifest[0]["sha256"],
        "pictures": 2, "picture_bytes": len(lossy()) + len(lossless()),
    }]


def test_the_same_input_gives_the_same_bytes(tmp_path):
    src = folder(tmp_path)
    story.build(src, tmp_path / "a")
    story.build(src, tmp_path / "b")
    for name in ("street.json", "manifest.json", "pics/street/p1.webp", "pics/street/p2.webp"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name


def test_a_folder_without_a_library_builds_when_every_story_is_painted(tmp_path):
    src = folder(tmp_path, library=False)
    errors, notes = story.build(src, tmp_path / "out")
    assert errors == [], errors


def test_drawn_and_painted_stories_build_together(tmp_path):
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 90"><rect width="160" height="90" fill="#223"/></svg>'
    src = folder(tmp_path, {"street": script(), "snack": {
        "v": 1, "slug": "snack", "title": "Moth Hour", "kicker": "CH 02", "lang": "en",
        "panels": [{"id": "p1", "ms": 4000, "enter": "cut", "alt": "A sun.", "layers": [{"art": "sun", "x": 0, "y": 0, "w": 100, "h": 100}]}]}}, library=True)
    (src / "library" / "sun.svg").write_text(svg, encoding="utf-8")
    errors, notes = story.build(src, tmp_path / "out")
    assert errors == [], errors
    assert json.loads((tmp_path / "out" / "snack.json").read_text(encoding="utf-8"))["v"] == 1
    assert not (tmp_path / "out" / "pics" / "snack").exists(), "a drawn story has no pictures folder"


def test_check_only_writes_nothing(tmp_path):
    src = folder(tmp_path)
    errors, notes = story.build(src, None)
    assert errors == [] and not (tmp_path / "out").exists()


# ---- what is refused -------------------------------------------------------------------------------------------------------

def refused(tmp_path, needle, **kw):
    src = folder(tmp_path, **kw)
    out = tmp_path / "out"
    errors, notes = story.build(src, out)
    assert any(needle in line for line in errors), (needle, errors)
    assert not out.exists(), "nothing is written unless every channel passes"
    return errors


def test_a_script_that_brings_its_own_pictures_is_refused(tmp_path):
    refused(tmp_path, "leave `pics` out", scripts={"street": script() | {"pics": {}}})


def test_a_script_that_brings_its_own_art_is_refused(tmp_path):
    refused(tmp_path, "leave `art` out", scripts={"street": script() | {"art": {}}})


def test_a_layer_whose_picture_has_no_file_is_refused(tmp_path):
    refused(tmp_path, "p2", files={"street": {"p1": lossy()}})


def test_a_picture_that_is_not_a_webp_is_refused(tmp_path):
    refused(tmp_path, "p2", files={"street": {"p1": lossy(), "p2": b"\x89PNG\r\n\x1a\n" + b"\0" * 64}})


def test_an_animated_picture_is_refused(tmp_path):
    refused(tmp_path, "animation", files={"street": {"p1": lossy(), "p2": extended(animated=True)}})


def test_a_picture_that_is_too_small_is_refused(tmp_path):
    refused(tmp_path, "p2", files={"street": {"p1": lossy(), "p2": lossy(200, 100)}})


def test_a_picture_that_is_too_big_in_pixels_is_refused(tmp_path):
    refused(tmp_path, "p2", files={"street": {"p1": lossy(), "p2": lossy(5000, 3000)}})


def test_a_picture_that_is_too_heavy_is_refused_before_it_is_read(tmp_path):
    refused(tmp_path, "the limit is 400000", files={"street": {"p1": lossy(), "p2": lossy(pad=story.fmt.LIMITS["pic_bytes"])}})


def test_a_picture_that_is_a_folder_is_refused(tmp_path):
    src = folder(tmp_path)
    (src / "pictures" / "street" / "p2.webp").unlink()
    (src / "pictures" / "street" / "p2.webp").mkdir()
    errors, _ = story.build(src, tmp_path / "out")
    assert any("p2" in line and "plain file" in line for line in errors), errors


def test_an_id_that_is_not_an_id_is_never_looked_for_on_disk(tmp_path, monkeypatch):
    bad = script()
    bad["panels"][0]["layers"][0]["pic"] = "../p1"
    src = folder(tmp_path, scripts={"street": bad})
    (src / "pictures" / "p1.webp").write_bytes(lossy())  # the file a path-minded id would reach
    asked = []
    real = story._read_picture
    monkeypatch.setattr(story, "_read_picture", lambda root, slug, name: asked.append(name) or real(root, slug, name))
    errors, _ = story.build(src, tmp_path / "out")
    assert errors and "../p1" not in asked, (errors, asked)


def test_a_picture_that_is_a_link_is_refused(tmp_path):
    src = folder(tmp_path)
    target = tmp_path / "elsewhere.webp"
    target.write_bytes(lossy())
    (src / "pictures" / "street" / "p2.webp").unlink()
    try:
        (src / "pictures" / "street" / "p2.webp").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make links")
    errors, _ = story.build(src, tmp_path / "out")
    assert any("p2" in line and "link" in line for line in errors), errors


def test_a_script_that_fails_the_servers_checks_is_refused_with_the_file_named(tmp_path):
    bad = script()
    bad["panels"][0]["lines"][0]["text"] = "<b>hi</b>"
    errors = refused(tmp_path, "channels/street.json", scripts={"street": bad})
    assert any("lines[0].text" in line for line in errors)


def test_a_script_with_the_wrong_slug_is_refused(tmp_path):
    refused(tmp_path, "must be the file's name", scripts={"street": script(slug="snack")})


def test_a_picture_the_script_does_not_use_is_a_note_not_an_error(tmp_path):
    src = folder(tmp_path, files={"street": {"p1": lossy(), "p2": lossless(), "p3": lossy()}})
    errors, notes = story.build(src, tmp_path / "out")
    assert errors == [] and any("p3" in line and "not used" in line for line in notes), notes
    assert not (tmp_path / "out" / "pics" / "street" / "p3.webp").exists(), "only the pictures the script uses are copied"


def test_a_pictures_folder_with_no_story_is_a_note(tmp_path):
    src = folder(tmp_path, files={"street": {"p1": lossy(), "p2": lossless()}, "snack": {"p1": lossy()}})
    errors, notes = story.build(src, tmp_path / "out")
    assert errors == [] and any("snack" in line for line in notes), notes


def test_a_picture_id_that_is_not_an_id_never_names_a_file(tmp_path):
    bad = script()
    bad["panels"][0]["layers"][0]["pic"] = "../p1"
    refused(tmp_path, "pic", scripts={"street": bad})
    assert not (tmp_path / "p1.webp").exists()


# ---- the command line --------------------------------------------------------------------------------------------------------

def test_the_command_builds_a_picture_folder_and_says_so(tmp_path):
    src = folder(tmp_path)
    done = subprocess.run([sys.executable, "-m", "l3mon", "story", "build", str(src), str(tmp_path / "out")], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (tmp_path / "out" / "pics" / "street" / "p1.webp").is_file()


# ---- what the review of 2026-10-16 found: the output folder ---------------------------------------------------------------------------------------

def test_a_story_that_was_pulled_is_gone_from_the_output_folder_at_the_next_build(tmp_path):
    src = folder(tmp_path, {"street": script(), "snack": script("snack")}, {"street": {"p1": lossy(), "p2": lossless()}, "snack": {"p1": lossy(), "p2": lossless()}})
    out = tmp_path / "out"
    assert story.build(src, out)[0] == []
    assert (out / "snack.json").is_file() and (out / "pics" / "snack" / "p1.webp").is_file()
    (src / "channels" / "snack.json").unlink()
    for name in ("p1", "p2"):
        (src / "pictures" / "snack" / f"{name}.webp").unlink()
    assert story.build(src, out)[0] == []
    assert not (out / "snack.json").exists() and not (out / "pics" / "snack").exists(), "the platform serves every file it finds, so a pulled story must not stay"
    assert (out / "street.json").is_file() and (out / "pics" / "street" / "p2.webp").is_file()
    assert [m["slug"] for m in json.loads((out / "manifest.json").read_text(encoding="utf-8"))] == ["street"]


def test_the_build_leaves_alone_what_it_did_not_write(tmp_path):
    src = folder(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "README.txt").write_text("not ours", encoding="utf-8")
    assert story.build(src, out)[0] == []
    assert (out / "README.txt").read_text(encoding="utf-8") == "not ours"


def test_a_write_that_fails_leaves_the_output_folder_exactly_as_it_was_and_says_so(tmp_path, monkeypatch):
    src = folder(tmp_path, {"street": script(), "snack": script("snack")}, {"street": {"p1": lossy(), "p2": lossless()}, "snack": {"p1": lossy(), "p2": lossless()}})
    out = tmp_path / "out"
    assert story.build(src, out)[0] == []
    before = {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    other = lossy(1700, 893)
    (src / "pictures" / "street" / "p1.webp").write_bytes(other.replace(b"", b""))  # a different picture: the output would change
    real = Path.write_bytes

    def broken(self, data):
        if self.name == "snack.json":
            raise OSError("the disk is full")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", broken)
    errors, _ = story.build(src, out)
    monkeypatch.undo()
    assert any("could not write" in line and "disk is full" in line for line in errors), errors
    after = {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert after == before, "nothing of the failed build reached the folder the platform reads"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("out.") ], "and no half-built folder is left beside it"


@pytest.mark.parametrize("name", ["nul", "con", "aux", "prn", "com1", "lpt9"])
def test_a_name_that_windows_keeps_for_a_device_is_refused_on_any_system(tmp_path, name):
    assert story.reserved_name(name) and story.reserved_name(name.upper())
    assert not story.reserved_name("street") and not story.reserved_name("console") and not story.reserved_name("nullable")
    if sys.platform == "win32":
        pytest.skip("on Windows a source file with that name cannot even be made")
    src = folder(tmp_path, {name: script(name)}, {name: {"p1": lossy(), "p2": lossless()}})
    out = tmp_path / "out"
    errors, _ = story.build(src, out)
    assert any("reserves" in line for line in errors), errors
    assert not out.exists()


def test_the_manifest_and_the_stories_have_the_same_bytes_on_every_system(tmp_path):
    src = folder(tmp_path)
    story.build(src, tmp_path / "out")
    assert b"\r" not in (tmp_path / "out" / "manifest.json").read_bytes()


@pytest.mark.parametrize("data,words", [
    (riff(chunk(b"VP8X", bytes([0, 0, 0, 0]) + (639).to_bytes(3, "little") + (359).to_bytes(3, "little")) + chunk(b"ANMF", b"\0" * 40)), "animation"),
    (riff(chunk(b"VP8L", b"/" + struct.pack("<I", 639 | (359 << 14)) + b"\0" * 20) + chunk(b"ANIM", b"\0" * 8)), "animation"),
    (riff(b"VP8 " + struct.pack("<I", 5000) + bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + struct.pack("<HH", 640, 360) + b"\0" * 24), "runs past the end"),
    (riff(chunk(b"VP8 ", bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + struct.pack("<HH", 640, 360) + b"\0" * 24) + b"abc"), "cut short"),
    (riff(chunk(b"VP8L", b"/" + struct.pack("<I", 639 | (359 << 14)) + b"\0" * 20) + chunk(b"VP8X", bytes([0, 0, 0, 0]) + (639).to_bytes(3, "little") + (359).to_bytes(3, "little"))), "exactly one picture"),
    (riff(chunk(b"VP8 ", bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + struct.pack("<HH", 640, 360) + b"\0" * 24) + chunk(b"EXIF", b"x" * 20)), "not used here"),
])
def test_each_refusal_of_a_header_says_what_was_wrong(data, words):
    size, why = story.webp_info(data)
    assert size is None and words in why, why
