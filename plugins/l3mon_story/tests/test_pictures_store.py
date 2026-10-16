"""The painted pictures on disk: what the store checks before a picture story is offered and before a picture is served.

A picture story (format version 2) is a bundle plus its WebP files in `<folder>/pics/<slug>/<id>.webp`. The bundle names each file by
size, bytes and SHA-256; the store checks all of it when it looks at the folder (so a story with a missing or altered picture is simply not
available) and again when a picture is served (so a file swapped between two looks at the folder is never sent). Each refusal is tested by its
own reason, because two rules refusing the same file would hide one that had stopped working.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_story
"""
import hashlib
import logging
import os
import stat
import threading
import types

import pytest

from CTFd.plugins.l3mon_story import store
from story_world import bundle, pic_bundle, webp, write_pic_story, write_pictures, write_story


@pytest.fixture()
def shelf(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("L3MON_STORY_DIR", str(tmp_path))
    caplog.set_level(logging.WARNING, logger="l3mon")
    store.reset()
    yield tmp_path
    store.reset()


def said(caplog):
    return [record.getMessage() for record in caplog.records if record.name == "l3mon"]


# ---- the good story --------------------------------------------------------------------------------------------------------

def test_a_picture_story_is_read_and_its_pictures_are_served_by_id(shelf, caplog):
    obj, files = write_pic_story(str(shelf), "street", panels=3)
    story = store.get("street")
    assert story is not None and story.version == 2 and story.flip is True and story.panels == 3
    assert set(story.pics) == {"p1", "p2", "p3"} and said(caplog) == []
    for name, data in files.items():
        got = store.picture("street", name)
        assert got is not None and got[0] == data
        assert got[1] == '"i' + hashlib.sha256(data).hexdigest()[:22] + '"', "a strong tag made from the file's own checksum"


def test_the_painted_words_are_in_the_transcript_in_the_order_they_were_written(shelf):
    write_pic_story(str(shelf), "street", panels=2)
    transcript = store.get("street").transcript
    assert [t["alt"] for t in transcript] == ["Picture 1: a round sun over a dark field.", "Picture 2: a round sun over a dark field."]
    assert transcript[0]["lines"] == [("", "Line 1 sets the scene.", "caption"), ("Host", 'Line 1 & "quoted" it\'s fine.', "say")]


def test_a_drawn_story_is_still_a_version_one_story_with_no_pictures(shelf):
    write_story(str(shelf), "street")
    story = store.get("street")
    assert story.version == 1 and story.pics == {} and story.flip is False
    assert store.picture("street", "sun") is None, "the id of a drawn picture is not a painted picture"


def test_the_served_bundle_names_the_pictures_by_checksum_and_never_by_a_path(shelf):
    obj, files = write_pic_story(str(shelf), "street")
    body = store.get("street").body.decode("ascii")
    assert "pics/" not in body and ".webp" not in body and str(shelf) not in body
    assert hashlib.sha256(files["p1"]).hexdigest() in body


# ---- what is refused, each for its own reason ---------------------------------------------------------------------------------

def test_a_story_whose_picture_file_is_missing_is_not_offered(shelf, caplog):
    write_pic_story(str(shelf), "street")
    os.remove(shelf / "pics" / "street" / "p2.webp")
    assert store.get("street") is None and store.panels_of("street") is None
    assert any("p2" in line and "missing" in line for line in said(caplog)), said(caplog)


def test_a_story_with_no_pictures_folder_at_all_is_not_offered(shelf, caplog):
    obj, files = pic_bundle("street")
    write_story(str(shelf), "street", obj)
    assert obj["v"] == 2 and store.get("street") is None
    assert any("missing" in line for line in said(caplog)), said(caplog)


def test_a_picture_that_is_a_link_is_not_followed(shelf, tmp_path_factory, caplog):
    obj, files = write_pic_story(str(shelf), "street")
    outside = tmp_path_factory.mktemp("outside") / "p1.webp"
    outside.write_bytes(files["p1"])
    os.remove(shelf / "pics" / "street" / "p1.webp")
    os.symlink(str(outside), shelf / "pics" / "street" / "p1.webp")
    assert store.get("street") is None
    assert any("not a regular file" in line for line in said(caplog)), said(caplog)


def test_a_picture_folder_that_is_a_link_is_not_followed(shelf, tmp_path_factory, caplog):
    obj, files = write_pic_story(str(shelf), "street")
    elsewhere = tmp_path_factory.mktemp("outside")
    write_pictures(str(elsewhere), "street", files)
    os.rename(shelf / "pics" / "street", shelf / "pics" / "gone")
    os.symlink(str(elsewhere / "pics" / "street"), shelf / "pics" / "street")
    assert store.get("street") is None
    assert any("not a folder" in line for line in said(caplog)), said(caplog)


def test_a_picture_that_is_a_folder_is_not_read(shelf, caplog):
    write_pic_story(str(shelf), "street")
    os.remove(shelf / "pics" / "street" / "p1.webp")
    os.mkdir(shelf / "pics" / "street" / "p1.webp")
    assert store.get("street") is None
    assert any("not a regular file" in line for line in said(caplog)), said(caplog)


def test_a_picture_of_the_wrong_size_is_not_offered(shelf, caplog):
    write_pic_story(str(shelf), "street")
    with open(shelf / "pics" / "street" / "p1.webp", "ab") as handle:
        handle.write(b"x")
    assert store.get("street") is None
    assert any("p1" in line and "bytes" in line for line in said(caplog)), said(caplog)


def test_a_picture_of_the_wrong_size_is_not_even_opened(shelf, monkeypatch):
    """The size is compared before the file is read, so a huge file put in a picture's place costs a look, not a read."""
    write_pic_story(str(shelf), "street")
    with open(shelf / "pics" / "street" / "p1.webp", "ab") as handle:
        handle.write(b"x" * 100)
    opened = []
    real = os.open
    monkeypatch.setattr(store.os, "open", lambda path, *a, **k: opened.append(str(path)) or real(path, *a, **k))
    assert store.get("street") is None
    assert not any(path.endswith("p1.webp") for path in opened), opened


def test_a_picture_with_the_right_size_but_other_content_is_not_offered(shelf, caplog):
    obj, files = write_pic_story(str(shelf), "street")
    data = bytearray(files["p1"])
    data[-1] ^= 0xFF
    (shelf / "pics" / "street" / "p1.webp").write_bytes(bytes(data))
    assert len(data) == len(files["p1"])
    assert store.get("street") is None
    assert any("p1" in line and "checksum" in line for line in said(caplog)), said(caplog)


def test_a_file_that_is_not_a_webp_is_not_offered_even_when_its_checksum_is_right(shelf, caplog):
    obj, files = pic_bundle("street")
    page = b"<html><script>alert(1)</script></html>" + b" " * 40
    obj["pics"]["p1"].update(bytes=len(page), sha256=hashlib.sha256(page).hexdigest())
    files["p1"] = page
    write_story(str(shelf), "street", obj)
    write_pictures(str(shelf), "street", files)
    assert store.get("street") is None
    assert any("p1" in line and "WebP" in line for line in said(caplog)), said(caplog)


def test_a_riff_file_that_says_it_is_not_a_webp_is_not_offered(shelf, caplog):
    obj, files = pic_bundle("street")
    wave = bytearray(files["p1"])
    wave[8:12] = b"WAVE"
    files["p1"] = bytes(wave)
    obj["pics"]["p1"].update(bytes=len(wave), sha256=hashlib.sha256(bytes(wave)).hexdigest())
    write_story(str(shelf), "street", obj)
    write_pictures(str(shelf), "street", files)
    assert store.get("street") is None and any("WebP" in line for line in said(caplog)), said(caplog)


def test_a_picture_swapped_after_the_story_was_read_is_never_sent(shelf, caplog):
    obj, files = write_pic_story(str(shelf), "street")
    assert store.get("street") is not None
    swapped = webp(9)
    assert swapped != files["p1"]
    (shelf / "pics" / "street" / "p1.webp").write_bytes(swapped)
    assert store.picture("street", "p1") is None, "the story is still listed until the next look, but the swapped file is not sent"
    assert any("checksum" in line or "bytes" in line for line in said(caplog)), said(caplog)
    assert store.picture("street", "p2") is not None, "the other pictures are not affected"


def test_a_picture_deleted_after_the_story_was_read_is_not_sent(shelf):
    write_pic_story(str(shelf), "street")
    assert store.get("street") is not None
    os.remove(shelf / "pics" / "street" / "p1.webp")
    assert store.picture("street", "p1") is None


def test_a_picture_turned_into_a_link_after_the_story_was_read_is_not_sent(shelf, tmp_path_factory):
    obj, files = write_pic_story(str(shelf), "street")
    assert store.get("street") is not None
    outside = tmp_path_factory.mktemp("outside") / "p1.webp"
    outside.write_bytes(files["p1"])
    os.remove(shelf / "pics" / "street" / "p1.webp")
    os.symlink(str(outside), shelf / "pics" / "street" / "p1.webp")
    assert store.picture("street", "p1") is None


# ---- names that are not ids never reach the disk ---------------------------------------------------------------------------

@pytest.mark.parametrize("slug,name", [
    ("../street", "p1"), ("street", "../p1"), ("street", "p1.webp"), ("street", "P1"), ("street", ""), ("Street", "p1"), ("", "p1"),
    ("street/", "p1"), ("street", "p1/../p2"), ("street", "p1\x00"), ("street\x00", "p1"), ("street", "p" * 41), (None, "p1"), ("street", None), (1, 2),
])
def test_a_name_that_is_not_an_id_never_reaches_the_disk(shelf, monkeypatch, slug, name):
    write_pic_story(str(shelf), "street")
    store.get("street")
    opened = []
    real = os.open
    monkeypatch.setattr(store.os, "open", lambda *a, **k: opened.append(a) or real(*a, **k))
    assert store.picture(slug, name) is None and opened == []


def test_a_picture_the_story_does_not_name_is_not_served_even_if_the_file_is_there(shelf):
    write_pic_story(str(shelf), "street")
    store.get("street")
    (shelf / "pics" / "street" / "p9.webp").write_bytes(webp(5))
    assert store.picture("street", "p9") is None
    (shelf / "pics" / "snack").mkdir()
    (shelf / "pics" / "snack" / "p1.webp").write_bytes(webp(5))
    assert store.picture("snack", "p1") is None, "a channel with no story serves nothing"


def test_the_pictures_of_one_channel_are_not_served_under_another(shelf):
    write_pic_story(str(shelf), "street")
    write_pic_story(str(shelf), "snack", panels=1)
    store.reset()
    assert store.picture("snack", "p1") is not None and store.picture("snack", "p2") is None
    assert store.picture("street", "p2") is not None


# ---- looking again ---------------------------------------------------------------------------------------------------------

def test_a_picture_that_arrives_after_the_story_makes_it_available_at_the_next_look(shelf, monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(store, "time", types.SimpleNamespace(monotonic=lambda: now["t"]))
    obj, files = pic_bundle("street")
    write_story(str(shelf), "street", obj)
    assert store.get("street") is None
    write_pictures(str(shelf), "street", files)
    assert store.get("street") is None, "not before the interval is over: a request never looks at the folder itself"
    now["t"] += store.RESCAN_SECONDS + 1
    assert store.get("street") is not None


def test_a_picture_that_goes_missing_takes_the_story_away_at_the_next_look(shelf, monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(store, "time", types.SimpleNamespace(monotonic=lambda: now["t"]))
    write_pic_story(str(shelf), "street")
    assert store.get("street") is not None
    os.remove(shelf / "pics" / "street" / "p2.webp")
    now["t"] += store.RESCAN_SECONDS + 1
    assert store.get("street") is None


def test_unchanged_pictures_are_not_read_again_at_every_look(shelf, monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(store, "time", types.SimpleNamespace(monotonic=lambda: now["t"]))
    write_pic_story(str(shelf), "street")
    assert store.get("street") is not None
    hashed = []
    real = store.hashlib.sha256
    monkeypatch.setattr(store.hashlib, "sha256", lambda *a, **k: hashed.append(1) or real(*a, **k))
    for _ in range(3):
        now["t"] += store.RESCAN_SECONDS + 1
        assert store.get("street") is not None
    assert hashed == [], "the look at the folder only compares file times, sizes and numbers"


def test_a_picture_that_changes_is_checked_again_at_the_next_look(shelf, monkeypatch, caplog):
    now = {"t": 1000.0}
    monkeypatch.setattr(store, "time", types.SimpleNamespace(monotonic=lambda: now["t"]))
    obj, files = write_pic_story(str(shelf), "street")
    assert store.get("street") is not None
    other = bytearray(files["p1"])
    other[-1] ^= 0x01
    target = shelf / "pics" / "street" / "p1.webp"
    target.write_bytes(bytes(other))
    later = target.stat().st_mtime_ns + 5_000_000_000  # a file that is written again some time later has a later time (a test is faster than the clock's tick)
    os.utime(target, ns=(later, later))
    now["t"] += store.RESCAN_SECONDS + 1
    assert store.get("street") is None, "the story's own file did not change, but a picture did"


# ---- a drawn story and a painted one side by side ---------------------------------------------------------------------------

def test_drawn_and_painted_stories_live_in_one_folder(shelf):
    write_story(str(shelf), "snack", bundle("snack"))
    write_pic_story(str(shelf), "street")
    assert set(store.all_stories()) == {"snack", "street"}
    assert (shelf / "pics").is_dir() and "pics" not in store.all_stories(), "the folder of pictures is not read as a story"


def test_the_builds_manifest_is_not_a_story_and_is_not_reported(shelf, caplog):
    write_pic_story(str(shelf), "street")
    (shelf / "manifest.json").write_text('[{"slug": "street"}]', encoding="utf-8")
    assert set(store.all_stories()) == {"street"} and said(caplog) == []


# ---- what the review of 2026-10-16 found ---------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="a named pipe needs a Unix system")
def test_a_pipe_swapped_in_for_a_picture_between_the_look_and_the_open_never_makes_the_server_wait(shelf):
    """The look at the folder said "a regular file"; by the time the file is opened somebody has put a pipe there. Opening a pipe for reading waits for a
    writer for ever, so the open must not wait and the descriptor must be checked after it is opened."""
    obj, files = write_pic_story(str(shelf), "street")
    where = shelf / "pics" / "street"
    target = where / "p1.webp"
    declared = obj["pics"]["p1"]
    target.unlink()
    os.mkfifo(target)
    real = os.lstat
    liar = lambda path, *a, **k: (  # noqa: E731  (what the look saw a moment earlier)
        os.stat_result((stat.S_IFREG | 0o644, 1, 1, 1, 0, 0, declared["bytes"], 0, 0, 0)) if str(path).endswith("p1.webp") else real(path, *a, **k)
    )
    answer = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store.os, "lstat", liar)
        thread = threading.Thread(target=lambda: answer.append(store._read_picture(where, "p1", declared)), daemon=True)
        thread.start()
        thread.join(5)
    assert not thread.is_alive(), "the server is waiting for a writer on a pipe that will never get one"
    assert answer and answer[0][0] is None and "regular file" in answer[0][1], answer


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="a named pipe needs a Unix system")
def test_the_same_for_a_story_file_swapped_for_a_pipe(shelf, caplog):
    write_pic_story(str(shelf), "street")
    entry_path = shelf / "street.json"
    entry_path.unlink()
    os.mkfifo(entry_path)
    real = os.lstat
    size = 500
    liar = lambda path, *a, **k: (  # noqa: E731
        os.stat_result((stat.S_IFREG | 0o644, 1, 1, 1, 0, 0, size, 0, 0, 0)) if str(path).endswith("street.json") else real(path, *a, **k)
    )
    answer = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store.os, "lstat", liar)

        def look():
            with os.scandir(shelf) as entries:
                for entry in entries:
                    if entry.name == "street.json":
                        answer.append(store._read(shelf, entry, os.stat_result((stat.S_IFREG | 0o644, 1, 1, 1, 0, 0, size, 0, 0, 0))))

        thread = threading.Thread(target=look, daemon=True)
        thread.start()
        thread.join(5)
    assert not thread.is_alive() and answer == [None]
    assert any("regular file" in line for line in said(caplog)), said(caplog)  # refused for what it is, not for the empty text a pipe without a writer gives


def test_a_file_name_with_a_new_line_after_json_is_not_read(shelf):
    write_pic_story(str(shelf), "street")
    (shelf / "snack.json\n").write_text((shelf / "street.json").read_text(encoding="utf-8").replace("street", "snack"), encoding="utf-8")
    assert set(store.all_stories()) == {"street"}


def test_an_id_or_a_slug_with_a_new_line_at_the_end_is_not_one(shelf):
    write_pic_story(str(shelf), "street")
    assert store.picture("street\n", "p1") is None and store.picture("street", "p1\n") is None
    assert store.get("street\n") is None


def test_the_header_of_a_picture_is_checked_when_it_is_sent_not_only_when_the_story_was_built(shelf, caplog):
    """A bundle edited by hand on the server could name an animated picture or one of another size; the checksum and the size would still agree."""
    obj, files = pic_bundle("street")
    declared = dict(obj["pics"]["p1"])
    for label, data, expected in (
        ("an animation", _riff(_chunk(b"VP8X", bytes([0x02, 0, 0, 0]) + (639).to_bytes(3, "little") + (359).to_bytes(3, "little")) + _chunk(b"VP8 ", _lossy_frame(640, 360))), "animation"),
        ("another size", _riff(_chunk(b"VP8 ", _lossy_frame(800, 600))), "800 x 600"),
        ("a canvas that disagrees with the picture", _riff(_chunk(b"VP8X", bytes([0, 0, 0, 0]) + (3999).to_bytes(3, "little") + (3999).to_bytes(3, "little")) + _chunk(b"VP8 ", _lossy_frame(640, 360))), "disagree"),
        ("junk chunks inside the file", _riff(_chunk(b"VP8 ", _lossy_frame(640, 360)) + _chunk(b"JUNK", b"x" * 200)), "not used here"),
        ("a chunk that lies about its size", _riff(_chunk(b"VP8 ", _lossy_frame(640, 360))[:4] + (10).to_bytes(4, "little") + _lossy_frame(640, 360)), "not a plain still WebP"),
    ):
        variant = dict(obj)
        variant["pics"] = {"p1": dict(declared, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()), "p2": obj["pics"]["p2"]}
        files2 = {"p1": data, "p2": files["p2"]}
        for stale in (shelf / "street.json",):
            stale.unlink(missing_ok=True)
        write_story(str(shelf), "street", variant)
        write_pictures(str(shelf), "street", files2)
        store.reset()
        caplog.clear()
        assert store.get("street") is None, label
        assert any(expected in line for line in said(caplog)), (label, said(caplog))


def _riff(chunks):
    return b"RIFF" + (4 + len(chunks)).to_bytes(4, "little") + b"WEBP" + chunks


def _chunk(fourcc, body):
    return fourcc + len(body).to_bytes(4, "little") + body + (b"\0" if len(body) % 2 else b"")


def _lossy_frame(width, height):
    return bytes([0x10, 0x02, 0x00]) + b"\x9d\x01\x2a" + width.to_bytes(2, "little") + height.to_bytes(2, "little") + b"\0" * 24
