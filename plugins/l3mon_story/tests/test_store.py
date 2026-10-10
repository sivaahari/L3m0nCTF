"""The stories on disk: what the store reads, what it refuses and why, and when it looks at the folder again.

The store is the only thing that touches the filesystem, so each refusal is tested by its own reason (the log says why, once), and not only
by the file being absent: two rules that refuse the same file would otherwise hide one that had stopped working. No app is needed.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_story
"""
import json
import logging
import os
import threading
import types

import pytest

from CTFd.plugins.l3mon_story import format as fmt
from CTFd.plugins.l3mon_story import store
from story_world import SVG, bundle, write_story


@pytest.fixture()
def shelf(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("L3MON_STORY_DIR", str(tmp_path))
    caplog.set_level(logging.WARNING, logger="l3mon")
    store.reset()
    yield tmp_path
    store.reset()


def said(caplog):
    return [record.getMessage() for record in caplog.records if record.name == "l3mon"]


# ---- the good file ---------------------------------------------------------------------------------------------------------

def test_a_good_file_is_read_and_served_by_its_slug(shelf, caplog):
    write_story(shelf, "street", panels=3)
    story = store.get("street")
    assert story is not None and story.slug == "street" and story.panels == 3 and story.etag.startswith('"s')
    assert story.body.isascii() and b'"success":true' in story.body, "unicode is escaped, so the bytes are plain ASCII"
    assert store.panels_of("street") == 3 and store.panels_of("snack") is None
    assert set(store.all_stories()) == {"street"} and said(caplog) == []


def test_the_transcript_is_the_alt_text_and_the_words_of_the_bubbles(shelf):
    write_story(shelf, "street", panels=2)
    transcript = store.get("street").transcript
    assert [t["alt"] for t in transcript] == ["Panel 1: a round sun over a dark field.", "Panel 2: a round sun over a dark field."]
    assert transcript[0]["lines"] == [("Host", 'Line 1 & "quoted" it\'s fine.', "say")]


# ---- what is refused, each for its own reason ---------------------------------------------------------------------------------

def test_a_link_is_never_followed(shelf, tmp_path_factory, caplog):
    target = write_story(tmp_path_factory.mktemp("outside"), "street")
    os.symlink(target, shelf / "street.json")
    assert store.get("street") is None
    assert any("not a regular file" in line for line in said(caplog)), said(caplog)


def test_a_folder_named_like_a_story_is_not_read(shelf, caplog):
    (shelf / "snack.json").mkdir()
    assert store.get("snack") is None
    assert any("not a regular file" in line for line in said(caplog)), said(caplog)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="a named pipe needs a Unix system")
def test_a_pipe_named_like_a_story_does_not_make_the_server_wait(shelf):
    fifo = shelf / "street.json"
    os.mkfifo(fifo)
    done = threading.Event()

    def scan():
        store.all_stories()
        done.set()

    thread = threading.Thread(target=scan, daemon=True)
    thread.start()
    finished = done.wait(5)
    if not finished:  # let the stuck reader go, so that the other tests can still use the store
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        thread.join(5)
    assert finished, "opening a pipe waits for a writer: the store must look at the kind of file before it opens anything"
    assert store.get("street") is None


def test_a_file_over_the_size_limit_is_not_read(shelf, caplog):
    (shelf / "street.json").write_bytes(b" " * (fmt.LIMITS["bundle_bytes"] + 1))
    assert store.get("street") is None
    assert any("is larger than" in line for line in said(caplog)), said(caplog)


def test_a_file_that_carries_another_channels_slug_is_not_served_under_either_name(shelf, caplog):
    write_story(shelf, "street", bundle("snack"))
    assert store.get("street") is None and store.get("snack") is None and store.all_stories() == {}
    assert any("not the name of the file" in line for line in said(caplog)), said(caplog)


def test_a_file_that_fails_the_format_checks_is_not_served_and_the_log_says_why_once(shelf, caplog, monkeypatch):
    obj = bundle("street")
    obj["art"]["sun"] = SVG.replace("<rect", "<script>alert(1)</script><rect", 1)
    write_story(shelf, "street", obj)
    monkeypatch.setattr(store, "RESCAN_SECONDS", 0)  # look at the folder on every call: the same refusal must not be written again
    for _ in range(3):
        assert store.get("street") is None
    lines = [line for line in said(caplog) if "street.json" in line]
    assert len(lines) == 1 and "script" in lines[0], lines


def test_a_file_that_is_not_json_is_not_served(shelf, caplog):
    (shelf / "street.json").write_text("{ this is not json", encoding="utf-8")
    assert store.get("street") is None
    assert any("not valid JSON" in line for line in said(caplog)), said(caplog)


@pytest.mark.parametrize("name", ["Street.json", ".street.json", "street.json.bak", "a_b.json", "street.JSON", "-street.json", "x" * 41 + ".json"])
def test_a_file_that_is_not_named_like_a_channel_is_not_even_opened(shelf, caplog, name):
    (shelf / name).write_text(json.dumps(bundle("street")), encoding="utf-8")
    assert store.all_stories() == {}
    assert said(caplog) == [], "ignored without a word: the name is looked at before anything else"


class Vanishing:
    """A directory entry whose file is deleted between the listing and the look (the folder is refreshed while the platform runs)."""

    name = "street.json"

    def stat(self, follow_symlinks=True):
        raise FileNotFoundError(2, "gone")


def test_a_file_that_vanishes_between_the_listing_and_the_look_is_skipped_and_never_breaks_a_request(shelf, monkeypatch):
    monkeypatch.setattr(store.os, "scandir", lambda directory: [Vanishing()])
    assert store.all_stories() == {} and store.get("street") is None and store.panels_of("street") is None


# ---- the folder ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["", "relative/folder", "/this/folder/does/not/exist"])
def test_no_usable_folder_means_no_stories_and_no_error(monkeypatch, value):
    monkeypatch.setenv("L3MON_STORY_DIR", value)
    store.reset()
    assert store.folder() is None and store.all_stories() == {} and store.get("street") is None and store.panels_of("street") is None
    store.reset()


def test_a_file_where_the_folder_should_be_means_no_stories(tmp_path, monkeypatch):
    path = tmp_path / "not-a-folder"
    path.write_text("x")
    monkeypatch.setenv("L3MON_STORY_DIR", str(path))
    store.reset()
    assert store.folder() is None and store.all_stories() == {}
    store.reset()


def test_the_folder_is_looked_at_again_only_after_the_interval_and_unchanged_files_are_not_read_again(shelf, monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(store, "time", types.SimpleNamespace(monotonic=lambda: now["t"]))
    write_story(shelf, "street")
    first = store.get("street")
    assert first is not None
    write_story(shelf, "snack")
    now["t"] += store.RESCAN_SECONDS - 1
    assert store.get("snack") is None, "a request never lists the folder inside the interval"
    now["t"] += 2
    assert store.get("snack") is not None, "and does after it"
    assert store.get("street") is first, "a file that did not change is not parsed again"
    write_story(shelf, "street", title="A New Title")
    now["t"] += store.RESCAN_SECONDS + 1
    assert store.get("street").title == "A New Title", "a file that changed is"
    os.remove(shelf / "snack.json")
    now["t"] += store.RESCAN_SECONDS + 1
    assert store.get("snack") is None and set(store.all_stories()) == {"street"}


def test_reset_makes_the_next_request_look_at_the_folder(shelf):
    write_story(shelf, "street")
    assert store.get("street") is not None
    write_story(shelf, "snack")
    assert store.get("snack") is None
    store.reset()
    assert store.get("snack") is not None


def test_a_name_that_is_not_a_slug_never_reaches_the_dictionary(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(store, "_fresh", lambda: {"Bad Name": sentinel, "..": sentinel, "ok-slug": sentinel})
    for name in ("Bad Name", "..", "", "x" * 41, None, 5, b"ok-slug", ["ok-slug"]):
        assert store.get(name) is None, name
    assert store.get("ok-slug") is sentinel


def test_the_list_is_a_copy_so_a_caller_cannot_change_what_is_served(shelf):
    write_story(shelf, "street")
    listing = store.all_stories()
    listing.clear()
    assert set(store.all_stories()) == {"street"}
