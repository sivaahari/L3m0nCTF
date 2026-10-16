"""The stories on disk: one checked bundle per channel, read from a folder the server is given and never written.

`L3MON_STORY_DIR` names a folder of `<slug>.json` files (made by `python -m l3mon story build` from the private story repository and
copied there at deployment, read-only). The server holds no story in its database and ships none in its image.

A picture story (format version 2) also has its WebP files in `pics/<slug>/<id>.webp` in that folder. The bundle names each one by size,
bytes and SHA-256 and the store checks all three, and that the file is a WebP, when it looks at the folder (a story with a picture that is
missing, a link, or not what the bundle says is simply not available) and again when it sends the picture (a file swapped in between is
never sent). The pictures are not kept in memory: the file system's own cache does that.

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

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\.json\Z")
RESERVED = {"manifest.json"}  # the build's own list of what it made: not a story, and not worth a line in the log
FINALE = "finale"  # the one story that is no channel's (finale.py); `panels_of` never answers for it, so no channel's button can be made from it
SLUG = story_format.SLUG
RESCAN_SECONDS = 20

_log = logging.getLogger("l3mon")
_lock = threading.Lock()
_state = {"at": 0.0, "folder": None, "stories": {}, "keys": {}, "parsed": {}, "verified": {}, "reported": set()}


class Story:
    """A checked story: what the page and the list need, and the bytes to serve."""

    __slots__ = ("slug", "title", "kicker", "lang", "panels", "body", "etag", "transcript", "version", "flip", "pics")

    def __init__(self, obj):
        self.slug, self.title, self.kicker, self.lang = obj["slug"], obj["title"], obj["kicker"], obj["lang"]
        self.panels = len(obj["panels"])
        self.version = obj["v"]
        self.flip = bool(obj.get("flip", False))
        self.pics = {name: dict(info) for name, info in obj.get("pics", {}).items()}
        self.body = json.dumps({"success": True, "data": obj}, separators=(",", ":")).encode("utf-8")  # ASCII only: unicode is escaped
        self.etag = '"s' + hashlib.sha256(self.body).hexdigest()[:22] + '"'
        self.transcript = [
            {
                "alt": panel["alt"],
                "pics": [layer["pic"] for layer in panel["layers"] if "pic" in layer],
                "lines": [(bubble.get("who", ""), bubble["text"], bubble["kind"]) for bubble in panel.get("bubbles", []) + panel.get("lines", [])],
            }
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
        _state.update({"at": 0.0, "folder": None, "stories": {}, "keys": {}, "parsed": {}, "verified": {}, "reported": set()})


def _say(name, message):
    if (name, message) not in _state["reported"]:
        _state["reported"].add((name, message))
        _log.warning("l3mon: the story file %s is not available: %s", name, message)


def _read(directory, entry, info):
    """-> Story or None for one directory entry whose `info` was just taken (see the module docstring for what is refused)."""
    name = entry.name
    if not stat.S_ISREG(info.st_mode):
        _say(name, "it is not a regular file (links are not followed)")
        return None
    if info.st_size > story_format.LIMITS["bundle_bytes"]:
        _say(name, f"it is larger than {story_format.LIMITS['bundle_bytes']} bytes")
        return None
    try:
        # never wait: opening a pipe for reading waits for a writer for ever, so open without blocking and look at what was really opened
        fd = os.open(directory / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError as error:
        _say(name, f"it could not be read: {error}")
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            _say(name, "it is not a regular file (links are not followed)")
            return None
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(story_format.LIMITS["bundle_bytes"] + 1)
    except OSError as error:
        _say(name, f"it could not be read: {error}")
        return None
    finally:
        os.close(fd)
    obj, problems = story_format.load_bundle(raw)
    if problems:
        _say(name, "; ".join(str(p) for p in problems[:3]) + (f" (and {len(problems) - 3} more)" if len(problems) > 3 else ""))
        return None
    if obj["slug"] != name[: -len(".json")]:
        _say(name, f"its slug is {obj['slug']!r}, not the name of the file")
        return None
    return Story(obj)


def _picture_folder(directory, slug):
    """-> (path, None) of a channel's pictures folder, or (None, why not). Neither `pics` nor the channel's folder may be a link."""
    path = directory / "pics"
    for part in (path, path / slug):
        try:
            info = os.lstat(part)
        except FileNotFoundError:
            return None, "its pictures folder is missing"
        except OSError as error:
            return None, f"its pictures folder could not be read: {error}"
        if not stat.S_ISDIR(info.st_mode):
            return None, "its pictures folder is not a folder (links are not followed)"
    return path / slug, None


def _read_picture(where, name, info):
    """-> (bytes, None) or (None, why not) for one picture of a story, checked against what the bundle says about it."""
    path = where / f"{name}.webp"
    try:
        seen = os.lstat(path)
    except FileNotFoundError:
        return None, f"the picture {name} is missing"
    except OSError as error:
        return None, f"the picture {name} could not be read: {error}"
    if not stat.S_ISREG(seen.st_mode):
        return None, f"the picture {name} is not a regular file (links are not followed)"
    if seen.st_size != info["bytes"]:
        return None, f"the picture {name} is {seen.st_size} bytes; the story says {info['bytes']}"
    try:
        # never wait: opening a pipe for reading waits for a writer for ever, so open without blocking and look at what was really opened
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError as error:
        return None, f"the picture {name} could not be read: {error}"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, f"the picture {name} is not a regular file (links are not followed)"
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(info["bytes"] + 1)
    except OSError as error:
        return None, f"the picture {name} could not be read: {error}"
    finally:
        os.close(fd)
    if len(raw) != info["bytes"]:
        return None, f"the picture {name} is {len(raw)} bytes; the story says {info['bytes']}"
    if hashlib.sha256(raw).hexdigest() != info["sha256"]:
        return None, f"the picture {name} does not match its checksum"
    found, why = story_format.webp_info(raw)
    if why is not None:
        return None, f"the picture {name} is not a plain still WebP file ({why})"
    if found != (info["w"], info["h"]):
        return None, f"the picture {name} is {found[0]} x {found[1]} pixels; the story says {info['w']} x {info['h']}"
    return raw, None


def _key_of(path):
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return (info.st_mode, info.st_mtime_ns, info.st_size, info.st_ino)


def _pictures_key(directory, story):
    """What the file system says about a story's pictures: when this is the same as last time they are not read again."""
    where = directory / "pics"
    return (_key_of(where), _key_of(where / story.slug)) + tuple((name, _key_of(where / story.slug / f"{name}.webp")) for name in sorted(story.pics))


def _pictures_ok(directory, name, story):
    """True when every picture of a version 2 story is there and is what the bundle says; the first problem is logged once."""
    where, problem = _picture_folder(directory, story.slug)
    if problem is None:
        for pic_id in sorted(story.pics):
            _, problem = _read_picture(where, pic_id, story.pics[pic_id])
            if problem:
                break
    if problem:
        _say(name, problem)
        return False
    return True


def _scan():
    directory = folder()
    stories, keys, parsed, verified = {}, {}, {}, {}
    if directory is not None:
        try:
            entries = [e for e in os.scandir(directory) if NAME.match(e.name) and e.name not in RESERVED]
        except OSError as error:
            _say(str(directory), f"the folder could not be read: {error}")
            entries = []
        for entry in sorted(entries, key=lambda e: e.name):
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue  # gone between the listing and the look (the folder is being refreshed): it is simply not there
            key = (info.st_mtime_ns, info.st_size, info.st_ino)
            old = _state["keys"].get(entry.name)
            same = old is not None and old[0] == key and _state["folder"] == directory
            story = _state["parsed"].get(entry.name) if same else _read(directory, entry, info)
            keys[entry.name], parsed[entry.name] = (key,), story
            if story is None:
                continue
            if story.version == 2:  # its pictures are checked too: again when the story or any of them changed
                seal = (key, _pictures_key(directory, story))
                before = _state["verified"].get(entry.name)
                ok = before[1] if (same and before is not None and before[0] == seal) else _pictures_ok(directory, entry.name, story)
                verified[entry.name] = (seal, ok)
                if not ok:
                    continue
            stories[story.slug] = story
    _state.update({"folder": directory, "stories": stories, "keys": keys, "parsed": parsed, "verified": verified, "at": time.monotonic()})


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


def picture(slug, name):
    """-> (bytes, etag) of one painted picture of an available story, or None. Both names must look like ids before anything is opened, the
    story must be available and must name the picture, and the file is checked against the bundle again (a file swapped since the last
    look at the folder is not sent)."""
    if not (isinstance(slug, str) and SLUG.match(slug) and isinstance(name, str) and story_format.ART_ID.match(name)):
        return None
    story = get(slug)
    directory = _state["folder"]
    if story is None or name not in story.pics or directory is None:
        return None
    where, problem = _picture_folder(directory, slug)
    raw = None
    if problem is None:
        raw, problem = _read_picture(where, name, story.pics[name])
    if problem:
        with _lock:
            _say(f"{slug}/{name}", problem)
        return None
    return raw, '"i' + story.pics[name]["sha256"][:22] + '"'


def panels_of(slug):
    """How many panels a channel's story has, or None. The board calls this for a channel that has something on air. The finale is no channel's."""
    if slug == FINALE:
        return None
    story = get(slug)
    return story.panels if story is not None else None
