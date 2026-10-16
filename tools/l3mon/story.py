"""Compile a story folder into the checked bundles the platform serves: `python -m l3mon story build SRC OUT`.

SRC holds the author's files (the private story repository):

    library/<id>.svg            every drawn picture (version 1), by id (lower-case letters, digits and underscores); comments and an XML
                                declaration are allowed here. The folder is only needed by drawn stories
    pictures/<slug>/<id>.webp   the painted pictures of a version 2 story, one WebP file per picture id (a plain still picture, 320 to 4096 pixels
                                a side, at most 400 KB; never a link)
    channels/<slug>.json        one script per channel: the bundle without `art` and without `pics` (the build adds exactly the pictures its
                                layers use: the drawn ones from the library, the painted ones with their size, bytes and SHA-256)

OUT receives `<slug>.json` for each channel (compact, ASCII, the same bytes every time for the same input), `pics/<slug>/<id>.webp` for the
painted pictures a script uses (where the platform looks for them) and `manifest.json` (slug, title, panels, size and a checksum of each).
Nothing is written unless every channel passes: the checks are the server's own (plugins/l3mon_story/format.py is loaded from the plugin, so
the build and the server can never disagree about what a good story is).

Standard library only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path


def _format():
    path = Path(__file__).resolve().parents[2] / "plugins" / "l3mon_story" / "format.py"
    spec = importlib.util.spec_from_file_location("l3mon_story_format", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("l3mon_story_format", module)
    spec.loader.exec_module(module)
    return module


fmt = _format()
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>\s*", re.IGNORECASE)
_BETWEEN = re.compile(r">[ \t]*\r?\n\s*<")  # layout (a line break and indentation) goes; a space between two <tspan> is part of the words and stays


def normalize_svg(text: str) -> str:
    """What an editor adds and the server does not accept (a byte-order mark, an XML declaration, comments), taken out; the layout between tags dropped."""
    text = text.lstrip("﻿")
    text = _DECLARATION.sub("", text)
    text = _COMMENT.sub("", text)
    return _BETWEEN.sub("><", text).strip()


WINDOWS_DEVICES = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}


def reserved_name(slug: str) -> bool:
    """True for a name Windows keeps for a device (a file called nul.json is the NUL device there): the build refuses it on every system, because the
    story folder is made on laptops of both kinds."""
    return slug.lower() in WINDOWS_DEVICES


def _problems_of(label: str, problems) -> list[str]:
    return [f"{label}: {p}" for p in problems]


def webp_info(data: bytes):
    """-> ((width, height), None) for a plain, still WebP, or (None, why not). The same reader the server uses (plugins/l3mon_story/format.py)."""
    return fmt.webp_info(data)


def webp_size(data: bytes):
    """-> (width, height) of a plain still WebP, or None."""
    return webp_info(data)[0]


def _read_picture(src: Path, slug: str, name: str):
    """-> (bytes, None) or (None, why not) for pictures/<slug>/<name>.webp: a regular file (never a link) within the size limit."""
    path = src / "pictures" / slug / f"{name}.webp"
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None, "the file is missing"
    except OSError as error:
        return None, f"the file could not be read: {error}"
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None, "it is a link or not a plain file; copy the picture itself"
    limit = fmt.LIMITS["pic_bytes"]
    if info.st_size > limit:
        return None, f"it is {info.st_size} bytes; the limit is {limit}"
    return path.read_bytes(), None


def build(src: Path, out: Path | None) -> tuple[list[str], list[str]]:
    """-> (errors, notes). Writes OUT only when there are no errors and `out` is given."""
    errors: list[str] = []
    notes: list[str] = []
    library: dict[str, str] = {}
    lib_dir = src / "library"
    for path in sorted(lib_dir.glob("*.svg")) if lib_dir.is_dir() else []:
        art_id = path.stem
        if not fmt.ART_ID.match(art_id):
            errors.append(f"library/{path.name}: the id {art_id!r} must be lower-case letters, digits and underscores")
            continue
        svg = normalize_svg(path.read_text(encoding="utf-8"))
        bad = fmt.check_svg(svg)
        if bad:
            errors += [f"library/{path.name}: {m}" for m in bad]
            continue
        library[art_id] = svg
    channels_dir = src / "channels"
    scripts = sorted(channels_dir.glob("*.json")) if channels_dir.is_dir() else []
    if not scripts:
        errors.append(f"{channels_dir} holds no scripts")
    built: dict[str, tuple[dict, bytes]] = {}
    pictures: dict[str, dict[str, bytes]] = {}  # slug -> {id: file bytes} for the painted stories that pass
    used_anywhere: set[str] = set()
    for path in scripts:
        slug = path.stem
        try:
            script = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=fmt._no_duplicates, parse_constant=fmt._no_constants)
        except (ValueError, UnicodeDecodeError) as error:
            errors.append(f"channels/{path.name}: not valid JSON: {error}")
            continue
        if not isinstance(script, dict):
            errors.append(f"channels/{path.name}: must be a JSON object")
            continue
        if "art" in script:
            errors.append(f"channels/{path.name}: leave `art` out; the build adds the pictures the layers use")
            continue
        if script.get("v") == 2 and "pics" in script:
            errors.append(f"channels/{path.name}: leave `pics` out; the build adds the pictures the layers use")
            continue
        if reserved_name(slug):
            errors.append(f"channels/{path.name}: {slug!r} is a name Windows reserves for a device, so it cannot be the name of a file")
            continue
        if script.get("slug") != slug:
            errors.append(f"channels/{path.name}: the slug is {script.get('slug')!r}; it must be the file's name, {slug!r}")
        painted = script.get("v") == 2
        field = "pic" if painted else "art"
        used = []
        for panel in script.get("panels", []) if isinstance(script.get("panels"), list) else []:
            for layer in panel.get("layers", []) if isinstance(panel, dict) and isinstance(panel.get("layers"), list) else []:
                name = layer.get(field) if isinstance(layer, dict) else None
                if isinstance(name, str) and name not in used:
                    used.append(name)
        bundle = dict(script)
        if painted:
            held: dict[str, bytes] = {}
            bundle["pics"] = {}
            for name in used:
                if not fmt.ART_ID.match(name):
                    continue  # the format check below names it; it never reaches the file system
                data, why = _read_picture(src, slug, name)
                if why is None:
                    size, why = webp_info(data)
                if why is not None:
                    errors.append(f"pictures/{slug}/{name}.webp: {why}")
                    continue
                held[name] = data
                bundle["pics"][name] = {"w": size[0], "h": size[1], "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            pictures[slug] = held
            where = src / "pictures" / slug
            for extra in sorted(where.glob("*.webp")) if where.is_dir() else []:
                if extra.stem not in used:
                    notes.append(f"pictures/{slug}/{extra.name} is not used by the story")
        else:
            missing = [name for name in used if name not in library]
            for name in missing:
                errors.append(f"channels/{path.name}: the picture {name!r} is not in the library (or did not pass its checks)")
            bundle["art"] = {name: library[name] for name in used if name in library}
        problems = fmt.validate_bundle(bundle)
        errors += _problems_of(f"channels/{path.name}", problems)
        if not painted:
            used_anywhere.update(used)
        body = json.dumps(bundle, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        built[slug] = (bundle, body)
    for name in sorted(set(library) - used_anywhere):
        notes.append(f"library/{name}.svg is not used by any channel")
    folders = src / "pictures"
    for where in sorted(folders.iterdir()) if folders.is_dir() else []:
        if where.is_dir() and where.name not in built:
            notes.append(f"pictures/{where.name} has no story (channels/{where.name}.json)")
    if errors or out is None:
        return errors, notes
    # Everything is written into a folder beside OUT first and only moved in when it is all there: a write that fails (a full disk, a name the system
    # refuses) leaves OUT exactly as it was, and what an earlier build wrote (every <slug>.json, manifest.json and pics/) is replaced, so a story that was
    # pulled does not stay in the folder the platform reads. Anything else in OUT is left alone.
    stage = None
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f"{out.name}.", dir=out.parent))
        manifest = []
        for slug, (bundle, body) in sorted(built.items()):
            (stage / f"{slug}.json").write_bytes(body)
            entry = {"slug": slug, "title": bundle["title"], "panels": len(bundle["panels"]), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            if bundle["v"] == 2:
                (stage / "pics" / slug).mkdir(parents=True, exist_ok=True)
                for name, data in sorted(pictures[slug].items()):
                    (stage / "pics" / slug / f"{name}.webp").write_bytes(data)
                entry["pictures"] = len(pictures[slug])
                entry["picture_bytes"] = sum(len(data) for data in pictures[slug].values())
            manifest.append(entry)
        (stage / "manifest.json").write_bytes((json.dumps(manifest, indent=1) + "\n").encode("utf-8"))  # bytes, so Windows does not turn the line ends into CRLF
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*.json"):
            old.unlink()
        if (out / "pics").exists():
            shutil.rmtree(out / "pics")
        for item in stage.iterdir():
            shutil.move(str(item), str(out / item.name))
    except OSError as error:
        errors.append(f"could not write {out}: {error}")
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
    return errors, notes


def main(src: str, out: str | None, check: bool = False) -> int:
    if out is None and not check:
        print("ERROR: give an output folder, or --check to only test the folder")
        return 2
    errors, notes = build(Path(src), None if check else Path(out) if out else None)
    for note in notes:
        print(f"note: {note}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"{len(errors)} problem(s); nothing was written")
        return 1
    print("every channel passes" + ("" if check or not out else f"; written to {out}"))
    return 0
