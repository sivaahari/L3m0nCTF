"""Compile a story folder into the checked bundles the platform serves: `python -m l3mon story build SRC OUT`.

SRC holds the author's files (the private story repository):

    library/<id>.svg       every picture, by id (lower-case letters, digits and underscores); comments and an XML declaration are allowed here
    channels/<slug>.json   one script per channel: the bundle without `art` (the build adds exactly the pictures its layers use)

OUT receives `<slug>.json` for each channel (compact, ASCII, the same bytes every time for the same input) and `manifest.json` (slug, title,
panels, size and a checksum of each). Nothing is written unless every channel passes: the checks are the server's own (plugins/l3mon_story/
format.py is loaded from the plugin, so the build and the server can never disagree about what a good story is).

Standard library only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
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
_BETWEEN = re.compile(r">\s+<")


def normalize_svg(text: str) -> str:
    """What an editor adds and the server does not accept (a byte-order mark, an XML declaration, comments), taken out; whitespace between tags dropped."""
    text = text.lstrip("﻿")
    text = _DECLARATION.sub("", text)
    text = _COMMENT.sub("", text)
    return _BETWEEN.sub("><", text).strip()


def _problems_of(label: str, problems) -> list[str]:
    return [f"{label}: {p}" for p in problems]


def build(src: Path, out: Path | None) -> tuple[list[str], list[str]]:
    """-> (errors, notes). Writes OUT only when there are no errors and `out` is given."""
    errors: list[str] = []
    notes: list[str] = []
    library: dict[str, str] = {}
    lib_dir = src / "library"
    if not lib_dir.is_dir():
        return [f"{lib_dir} is not a folder"], notes
    for path in sorted(lib_dir.glob("*.svg")):
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
        if script.get("slug") != slug:
            errors.append(f"channels/{path.name}: the slug is {script.get('slug')!r}; it must be the file's name, {slug!r}")
        used = []
        for panel in script.get("panels", []) if isinstance(script.get("panels"), list) else []:
            for layer in panel.get("layers", []) if isinstance(panel, dict) and isinstance(panel.get("layers"), list) else []:
                name = layer.get("art") if isinstance(layer, dict) else None
                if isinstance(name, str) and name not in used:
                    used.append(name)
        missing = [name for name in used if name not in library]
        for name in missing:
            errors.append(f"channels/{path.name}: the picture {name!r} is not in the library (or did not pass its checks)")
        bundle = dict(script)
        bundle["art"] = {name: library[name] for name in used if name in library}
        problems = fmt.validate_bundle(bundle)
        errors += _problems_of(f"channels/{path.name}", problems)
        used_anywhere.update(used)
        body = json.dumps(bundle, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        built[slug] = (bundle, body)
    for name in sorted(set(library) - used_anywhere):
        notes.append(f"library/{name}.svg is not used by any channel")
    if errors or out is None:
        return errors, notes
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for slug, (bundle, body) in sorted(built.items()):
        (out / f"{slug}.json").write_bytes(body)
        manifest.append({"slug": slug, "title": bundle["title"], "panels": len(bundle["panels"]), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return errors, notes


def main(src: str, out: str | None, check: bool = False) -> int:
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
