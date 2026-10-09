"""The stories on disk: one checked bundle per channel, read from a folder the server is given and never written.

`L3MON_STORY_DIR` names a folder of `<slug>.json` files (made by `python -m l3mon story build` from the private story repository and
copied there at deployment, read-only). The server holds no story in its database and ships none in its image.

Every file is checked again here with the same checks the build ran (format.py): a file edited on the server into something unsafe is
simply not available, and the log says why once. A file is read only if it is a regular file (never a link), of a name that looks like
a channel, within the size limit, and its `slug` is its own name. The folder is looked at again at most every 20 seconds, and at once
after `reset()`; a request never lists the folder itself, so a flood of requests costs no disk work.
"""
import hashlib
import json
import logging
import os
import re
import stat
import threading
import time
from pathlib import Path

from CTFd.plugins.l3mon_story import format as story_format

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\.json$")
SLUG = story_format.SLUG
RESCAN_SECONDS = 20

_log = logging.getLogger("l3mon")
_lock = threading.Lock()
_state = {"at": 0.0, "folder": None, "stories": {}, "keys": {}, "reported": set()}


class Story:
    """A checked story: what the page and the list need, and the bytes to serve."""

    __slots__ = ("slug", "title", "kicker", "lang", "panels", "body", "etag", "transcript")

    def __init__(self, obj):
        self.slug, self.title, self.kicker, self.lang = obj["slug"], obj["title"], obj["kicker"], obj["lang"]
        self.panels = len(obj["panels"])
        self.body = json.dumps({"success": True, "data": obj}, separators=(",", ":")).encode("utf-8")  # ASCII only: unicode is escaped
        self.etag = '"s' + hashlib.sha256(self.body).hexdigest()[:22] + '"'
        self.transcript = [
            {"alt": panel["alt"], "lines": [(bubble.get("who", ""), bubble["text"], bubble["kind"]) for bubble in panel.get("bubbles", [])]}
            for panel in obj["panels"]
        ]


def folder():
    value = os.environ.get("L3MON_STORY_DIR", "").strip()
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() and path.is_dir() else None


def reset():
    """Forget what was read; the next request looks at the folder again."""
    with _lock:
        _state.update({"at": 0.0, "folder": None, "stories": {}, "keys": {}, "reported": set()})


def _say(name, message):
    if (name, message) not in _state["reported"]:
        _state["reported"].add((name, message))
        _log.warning("l3mon: the story file %s is not available: %s", name, message)


def _read(directory, entry):
    """-> Story or None for one directory entry (see the module docstring for what is refused)."""
    name = entry.name
    info = entry.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        _say(name, "it is not a regular file (links are not followed)")
        return None
    if info.st_size > story_format.LIMITS["bundle_bytes"]:
        _say(name, f"it is larger than {story_format.LIMITS['bundle_bytes']} bytes")
        return None
    try:
        fd = os.open(directory / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(story_format.LIMITS["bundle_bytes"] + 1)
    except OSError as error:
        _say(name, f"it could not be read: {error}")
        return None
    obj, problems = story_format.load_bundle(raw)
    if problems:
        _say(name, "; ".join(str(p) for p in problems[:3]) + (f" (and {len(problems) - 3} more)" if len(problems) > 3 else ""))
        return None
    if obj["slug"] != name[: -len(".json")]:
        _say(name, f"its slug is {obj['slug']!r}, not the name of the file")
        return None
    return Story(obj)


def _scan():
    directory = folder()
    stories, keys = {}, {}
    if directory is not None:
        try:
            entries = [e for e in os.scandir(directory) if NAME.match(e.name)]
        except OSError as error:
            _say(str(directory), f"the folder could not be read: {error}")
            entries = []
        for entry in sorted(entries, key=lambda e: e.name):
            info = entry.stat(follow_symlinks=False)
            key = (info.st_mtime_ns, info.st_size, info.st_ino)
            old = _state["keys"].get(entry.name)
            if old is not None and old[0] == key and _state["folder"] == directory:
                story = _state["stories"].get(entry.name[:-5])
            else:
                story = _read(directory, entry)
            keys[entry.name] = (key,)
            if story is not None:
                stories[story.slug] = story
    _state.update({"folder": directory, "stories": stories, "keys": keys, "at": time.monotonic()})


def _fresh():
    with _lock:
        if time.monotonic() - _state["at"] >= RESCAN_SECONDS or _state["at"] == 0.0:
            _scan()
        return _state["stories"]


def get(slug):
    """The story of a channel, or None. A name that is not a channel slug never reaches the dictionary."""
    if not isinstance(slug, str) or not SLUG.match(slug):
        return None
    return _fresh().get(slug)


def all_stories():
    return dict(_fresh())


def panels_of(slug):
    """How many panels a channel's story has, or None. The board calls this for a channel that has something on air."""
    story = get(slug)
    return story.panels if story is not None else None
