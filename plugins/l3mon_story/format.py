"""The cold-open comic's file format (versions 1 and 2) and the checks that keep a bad file from ever reaching a browser.

A story is one JSON file per channel, a **bundle**: the script (panels, layers, sound cues) and its pictures. Version 1 draws its pictures in
code (SVG pictures, by id) and writes the speech as bubbles. Version 2 is a comic of painted pictures (WebP files kept beside the bundle,
named in `pics` by size, bytes and checksum, never by a path); the words are part of the picture, and `lines` writes them again as plain text.
The build (`python -m l3mon story build`) and the server (when it loads a file) run the same checks, so a file that passes in one
passes in the other and a file that is edited by hand on the server is checked again before anybody sees it.

Standard library only, so the build tool on a laptop and the plugin in the image import this very file.

Two principles:

- **An allow-list, never a deny-list.** Every key of the script, every element and every attribute of the art must be named here. An
  unknown key is an error, so a typo never passes silently and a feature nobody reviewed never ships.
- **Art can show, never do.** The pictures are shown by the page as images (an SVG used as an image cannot run script, load another
  file or reach the page), and in addition they are held to a small subset: shapes, gradients, patterns, clips, masks, text and a few
  filter effects. No script, no style sheet, no image, no font, no outside address, no event handler, no entity, no DOCTYPE.

`validate_bundle` returns a list of problems, each `(path, message)` with the path as the author wrote it (`panels[2].bubbles[0].text`).
An empty list means the bundle is good.
"""
import json
import math
import re
import xml.etree.ElementTree as ET
from importlib import util as _util
from pathlib import Path

VERSIONS = (1, 2)
SVG_NS = "http://www.w3.org/2000/svg"

LIMITS = {
    "panels": 12, "layers": 12, "bubbles": 6, "cues": 4, "art_pieces": 40,
    "bubble_chars": 160, "alt_chars": 300, "title_chars": 80, "kicker_chars": 60, "who_chars": 24,
    "panel_ms": (2000, 20000), "bundle_bytes": 600_000, "art_bytes": 80_000,
    "pics": 12, "pic_bytes": 400_000, "pic_px": (320, 4096), "lines": 8,
    "svg_elements": 2500, "svg_depth": 24, "svg_text_chars": 120, "path_chars": 20_000,
    "number_chars": 40, "list_chars": 20_000, "svg_cost": 6000, "bundle_cost": 20_000, "filter_cost": 60, "region_percent": 400, "region_units": 20_000,
}
ENTER = ("cut", "static", "slide", "pop")
KINDS = ("say", "think", "shout", "caption", "sfx")
TAILS = ("none", "tl", "tr", "bl", "br")
SIZES = ("s", "m", "l")
ANIMS = ("none", "bob", "sway", "pulse", "shake")
PIVOTS = ("self", "panel")  # a layer scales about its own middle, or about the middle of the panel (a camera push on a backdrop)
CUES = ("whoosh", "pop", "bonk", "tada", "sparkle", "static", "boing", "zap", "thud", "jingle", "sting", "tick")

BUNDLE_KEYS = {1: {"v", "slug", "title", "kicker", "lang", "panels", "art"}, 2: {"v", "slug", "title", "kicker", "lang", "panels", "pics", "flip"}}
PANEL_KEYS = {1: {"id", "ms", "enter", "alt", "layers", "bubbles", "sfx"}, 2: {"id", "ms", "enter", "alt", "layers", "lines", "sfx"}}
_LAYER_SHARED = {"x", "y", "w", "h", "z", "opacity", "flip", "anim", "pivot", "from", "to"}
LAYER_KEYS = {1: _LAYER_SHARED | {"art"}, 2: _LAYER_SHARED | {"pic"}}
LAYER_FIELD = {1: "art", 2: "pic"}  # what a layer shows: a drawn picture (version 1) or a painted one (version 2)
LINE_KEYS = {"kind", "who", "text"}
PIC_KEYS = {"w", "h", "bytes", "sha256"}
MOVE_KEYS = {"x", "y", "s"}
BUBBLE_KEYS = {"kind", "who", "text", "x", "y", "w", "tail", "at", "size"}
CUE_KEYS = {"cue", "at"}

# `\Z`, not `$`: a `$` also matches in front of a final line break, so "p1\n" would be an id (found by the review of 2026-10-16)
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\Z")
ART_ID = re.compile(r"^[a-z0-9][a-z0-9_]{0,39}\Z")  # also the id of a painted picture: its file is <id>.webp
SHA256 = re.compile(r"^[0-9a-f]{64}\Z")
PANEL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,11}\Z")
LANG = re.compile(r"^[a-z]{2}(-[A-Z]{2})?\Z")


class Problem(tuple):
    """(path, message): a tuple so tests can compare, with a readable str."""

    def __new__(cls, path, message):
        return super().__new__(cls, (path, message))

    def __str__(self):
        return f"{self[0]}: {self[1]}" if self[0] else self[1]


def _crew_text():
    """The shared plain-text rule (l3mon_core/text.py, standard library only), loaded by path so this file works on a laptop too."""
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "l3mon_core" / "text.py",):
        if candidate.is_file():
            spec = _util.spec_from_file_location("l3mon_text_rule", candidate)
            module = _util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.crew_text
    raise ImportError("l3mon_core/text.py not found next to this plugin")


crew_text = _crew_text()


# ---- strict JSON ------------------------------------------------------------------------------------------------------------

def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"the key {key!r} appears twice")
        out[key] = value
    return out


def _no_constants(name):
    raise ValueError(f"{name} is not a number")


def load_bundle(raw):
    """-> (object, problems) from the bytes or text of a bundle file: strict JSON (no NaN, no repeated key) and the size limit first."""
    size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
    if size > LIMITS["bundle_bytes"]:
        return None, [Problem("", f"the file is {size} bytes; the limit is {LIMITS['bundle_bytes']}")]
    try:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        obj = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except (ValueError, UnicodeDecodeError) as error:
        return None, [Problem("", f"not valid JSON: {error}")]
    return obj, validate_bundle(obj)


# ---- the script -------------------------------------------------------------------------------------------------------------

def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _keys(problems, path, obj, allowed):
    for key in obj:
        if key not in allowed:
            problems.append(Problem(f"{path}.{key}" if path else key, "is not a known key"))


def _range(problems, path, value, low, high, whole=False):
    ok = _whole(value) if whole else _number(value)
    if not ok:
        problems.append(Problem(path, "must be a whole number" if whole else "must be a number"))
    elif not low <= value <= high:
        problems.append(Problem(path, f"must be between {low} and {high}"))


def _text(problems, path, value, limit, required=True):
    clean, problem = crew_text(value, limit, required=required)
    if problem:
        problems.append(Problem(path, problem))
    elif isinstance(value, str) and clean != value:
        # the bundle keeps the text exactly as written, so what the shared rule would trim (spaces, line and file separators, a next-line mark,
        # an ideographic space) must not be there at all
        problems.append(Problem(path, "may not start or end with a space or an invisible character"))
    return clean


def _choice(problems, path, value, allowed):
    if value not in allowed:
        problems.append(Problem(path, "must be one of: " + ", ".join(allowed)))


def _move(problems, path, move):
    if not isinstance(move, dict):
        problems.append(Problem(path, "must be an object {x, y, s}"))
        return
    _keys(problems, path, move, MOVE_KEYS)
    for key, low, high in (("x", -100, 100), ("y", -100, 100), ("s", 0.2, 4)):
        if key in move:
            _range(problems, f"{path}.{key}", move[key], low, high)


def _layer(problems, path, layer, known, version=1):
    if not isinstance(layer, dict):
        problems.append(Problem(path, "must be an object"))
        return None
    _keys(problems, path, layer, LAYER_KEYS[version])
    field = LAYER_FIELD[version]
    name = layer.get(field)
    if not isinstance(name, str) or not ART_ID.match(name):
        problems.append(Problem(f"{path}.{field}", "must be the id of a picture (lower-case letters, digits and underscores)"))
    elif name not in known:
        problems.append(Problem(f"{path}.{field}", f"there is no picture {name!r} in {'art' if version == 1 else 'pics'}"))
    for key, low, high in (("x", -200, 200), ("y", -200, 200), ("w", 1, 400), ("h", 1, 400)):
        if key not in layer:
            problems.append(Problem(f"{path}.{key}", "is required"))
        else:
            _range(problems, f"{path}.{key}", layer[key], low, high)
    if "z" in layer:
        _range(problems, f"{path}.z", layer["z"], 0, 9, whole=True)
    if "opacity" in layer:
        _range(problems, f"{path}.opacity", layer["opacity"], 0, 1)
    if "flip" in layer and not isinstance(layer["flip"], bool):
        problems.append(Problem(f"{path}.flip", "must be true or false"))
    if "anim" in layer:
        _choice(problems, f"{path}.anim", layer["anim"], ANIMS)
    if "pivot" in layer:
        _choice(problems, f"{path}.pivot", layer["pivot"], PIVOTS)
    for key in ("from", "to"):
        if key in layer:
            _move(problems, f"{path}.{key}", layer[key])
    return name if isinstance(name, str) else None


def _bubble(problems, path, bubble, ms):
    if not isinstance(bubble, dict):
        problems.append(Problem(path, "must be an object"))
        return
    _keys(problems, path, bubble, BUBBLE_KEYS)
    _choice(problems, f"{path}.kind", bubble.get("kind"), KINDS)
    _text(problems, f"{path}.text", bubble.get("text"), LIMITS["bubble_chars"])
    if "who" in bubble:
        _text(problems, f"{path}.who", bubble["who"], LIMITS["who_chars"])
    for key, low, high in (("x", 0, 100), ("y", 0, 100), ("w", 10, 90)):
        if key not in bubble:
            problems.append(Problem(f"{path}.{key}", "is required"))
        else:
            _range(problems, f"{path}.{key}", bubble[key], low, high)
    if "tail" in bubble:
        _choice(problems, f"{path}.tail", bubble["tail"], TAILS)
    if "size" in bubble:
        _choice(problems, f"{path}.size", bubble["size"], SIZES)
    if "at" in bubble:
        _range(problems, f"{path}.at", bubble["at"], 0, ms if _whole(ms) else LIMITS["panel_ms"][1], whole=True)


def _line(problems, path, line):
    """One painted line written as text (version 2): who said it, in what way, and the words."""
    if not isinstance(line, dict):
        problems.append(Problem(path, "must be an object"))
        return
    _keys(problems, path, line, LINE_KEYS)
    _choice(problems, f"{path}.kind", line.get("kind"), KINDS)
    _text(problems, f"{path}.text", line.get("text"), LIMITS["bubble_chars"])
    if "who" in line:
        _text(problems, f"{path}.who", line["who"], LIMITS["who_chars"])


def _panel(problems, path, panel, art, seen_ids, used, version=1):
    if not isinstance(panel, dict):
        problems.append(Problem(path, "must be an object"))
        return
    _keys(problems, path, panel, PANEL_KEYS[version])
    pid = panel.get("id")
    if not isinstance(pid, str) or not PANEL_ID.match(pid):
        problems.append(Problem(f"{path}.id", "must be short lower-case letters, digits, - or _"))
    elif pid in seen_ids:
        problems.append(Problem(f"{path}.id", f"{pid!r} is used by another panel"))
    else:
        seen_ids.add(pid)
    ms = panel.get("ms")
    _range(problems, f"{path}.ms", ms, *LIMITS["panel_ms"], whole=True)
    _choice(problems, f"{path}.enter", panel.get("enter", "cut"), ENTER)
    _text(problems, f"{path}.alt", panel.get("alt"), LIMITS["alt_chars"])
    layers = panel.get("layers")
    if not isinstance(layers, list) or not layers:
        problems.append(Problem(f"{path}.layers", "needs at least one layer"))
    elif len(layers) > LIMITS["layers"]:
        problems.append(Problem(f"{path}.layers", f"at most {LIMITS['layers']} layers"))
    else:
        for i, layer in enumerate(layers):
            name = _layer(problems, f"{path}.layers[{i}]", layer, art, version)
            if name:
                used.add(name)
    lists = (("bubbles", LIMITS["bubbles"], "bubbles"), ("sfx", LIMITS["cues"], "cues")) if version == 1 else (("lines", LIMITS["lines"], "lines"), ("sfx", LIMITS["cues"], "cues"))
    for key, limit, name in lists:
        items = panel.get(key, [])
        if not isinstance(items, list):
            problems.append(Problem(f"{path}.{key}", "must be a list"))
        elif len(items) > limit:
            problems.append(Problem(f"{path}.{key}", f"at most {limit} {name}"))
    if version == 1 and isinstance(panel.get("bubbles"), list) and len(panel["bubbles"]) <= LIMITS["bubbles"]:
        for i, bubble in enumerate(panel["bubbles"]):
            _bubble(problems, f"{path}.bubbles[{i}]", bubble, ms)
    if version == 2 and isinstance(panel.get("lines"), list) and len(panel["lines"]) <= LIMITS["lines"]:
        for i, line in enumerate(panel["lines"]):
            _line(problems, f"{path}.lines[{i}]", line)
    if isinstance(panel.get("sfx"), list) and len(panel["sfx"]) <= LIMITS["cues"]:
        for i, cue in enumerate(panel["sfx"]):
            where = f"{path}.sfx[{i}]"
            if not isinstance(cue, dict):
                problems.append(Problem(where, "must be an object"))
                continue
            _keys(problems, where, cue, CUE_KEYS)
            _choice(problems, f"{where}.cue", cue.get("cue"), CUES)
            _range(problems, f"{where}.at", cue.get("at", 0), 0, ms if _whole(ms) else LIMITS["panel_ms"][1], whole=True)


def _le(data, at, size):
    return int.from_bytes(data[at : at + size], "little")


def webp_info(data):
    """-> ((width, height), None) for a plain, still WebP, or (None, why not). The whole file is walked, not only its first bytes: the RIFF length must be
    the file's, every chunk must lie inside it, and the only chunks allowed are an optional `VP8X` header (never with the animation flag), an optional
    `ALPH` and exactly one picture, `VP8 ` (lossy) or `VP8L` (lossless). Metadata, animation frames, junk and a second picture are refused, a header whose
    canvas disagrees with the picture is refused, and a chunk that claims more than the file holds is refused. The build tool and the server both read
    pictures with this one function."""
    n = len(data)
    if n < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None, "it is not a WebP file"
    if _le(data, 4, 4) != n - 8:
        return None, "its length does not match its header (it was cut short, or something was added after it)"
    chunks, pos = [], 12
    while pos < n:
        if pos + 8 > n:
            return None, "a chunk header is cut short"
        kind, size = data[pos : pos + 4], _le(data, pos + 4, 4)
        end = pos + 8 + size + (size & 1)
        if end > n:
            return None, "a chunk runs past the end of the file"
        chunks.append((kind, pos + 8, size))
        pos = end
    kinds = [c[0] for c in chunks]
    if b"ANIM" in kinds or b"ANMF" in kinds:
        return None, "it is an animation (only still pictures are used)"
    if any(k not in (b"VP8X", b"ALPH", b"VP8 ", b"VP8L") for k in kinds):
        return None, "it holds a kind of WebP data that is not used here (metadata or something else)"
    pictures = [c for c in chunks if c[0] in (b"VP8 ", b"VP8L")]
    if len(pictures) != 1 or kinds.count(b"VP8X") > 1 or (b"VP8X" in kinds and kinds[0] != b"VP8X") or kinds.count(b"ALPH") > 1:
        return None, "it must hold exactly one picture"
    kind, at, size = pictures[0]
    if kind == b"VP8 ":
        if size < 10 or data[at + 3 : at + 6] != b"\x9d\x01\x2a":
            return None, "its picture data is damaged (no start code)"
        width, height = _le(data, at + 6, 2) & 0x3FFF, _le(data, at + 8, 2) & 0x3FFF
    else:
        if size < 5 or data[at] != 0x2F:
            return None, "its picture data is damaged (no signature)"
        bits = _le(data, at + 1, 4)
        width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if b"VP8X" in kinds:
        _, head, hsize = chunks[0]
        if hsize < 10:
            return None, "its extended header is cut short"
        if data[head] & 0x02:
            return None, "it is an animation (only still pictures are used)"
        if (_le(data, head + 4, 3) + 1, _le(data, head + 7, 3) + 1) != (width, height):
            return None, "its canvas and its picture disagree about the size"
    return (width, height), None


def _pics(problems, pics):
    """The painted pictures a version 2 bundle names: id -> {w, h, bytes, sha256}. The file is `<id>.webp` in the channel's folder, so nothing
    here is ever a path. -> the dict to check the layers against ({} when it is not usable)."""
    if not isinstance(pics, dict) or not pics:
        problems.append(Problem("pics", "must be an object of pictures by id"))
        return {}
    if len(pics) > LIMITS["pics"]:
        problems.append(Problem("pics", f"at most {LIMITS['pics']} pictures"))
        return {}
    low, high = LIMITS["pic_px"]
    for name, info in pics.items():
        where = f"pics.{name}"
        if not isinstance(name, str) or not ART_ID.match(name):
            problems.append(Problem(where, "the id must be lower-case letters, digits and underscores"))
            continue
        if not isinstance(info, dict):
            problems.append(Problem(where, "must be an object {w, h, bytes, sha256}"))
            continue
        _keys(problems, where, info, PIC_KEYS)
        for key in ("w", "h"):
            if key not in info:
                problems.append(Problem(f"{where}.{key}", "is required"))
            else:
                _range(problems, f"{where}.{key}", info[key], low, high, whole=True)
        if "bytes" not in info:
            problems.append(Problem(f"{where}.bytes", "is required"))
        else:
            _range(problems, f"{where}.bytes", info["bytes"], 1, LIMITS["pic_bytes"], whole=True)
        digest = info.get("sha256")
        if not isinstance(digest, str) or not SHA256.match(digest):
            problems.append(Problem(f"{where}.sha256", "must be the 64 lower-case hexadecimal digits of the file's SHA-256"))
    return pics


def validate_bundle(obj):
    """The problems of a parsed bundle (see the module docstring). An empty list means it may be served."""
    problems = []
    if not isinstance(obj, dict):
        return [Problem("", "must be a JSON object")]
    version = obj.get("v")
    if version not in VERSIONS or isinstance(version, bool):
        problems.append(Problem("v", "must be " + " or ".join(str(v) for v in VERSIONS)))
        version = 1  # the rest is still checked, so one run lists everything that is wrong
    _keys(problems, "", obj, BUNDLE_KEYS[version])
    if not isinstance(obj.get("slug"), str) or not SLUG.match(obj["slug"]):
        problems.append(Problem("slug", "must be lower-case letters, digits and hyphens"))
    _text(problems, "title", obj.get("title"), LIMITS["title_chars"])
    _text(problems, "kicker", obj.get("kicker"), LIMITS["kicker_chars"])
    if not isinstance(obj.get("lang"), str) or not LANG.match(obj["lang"]):
        problems.append(Problem("lang", "must look like en or en-IN"))
    if version == 2 and "flip" in obj and not isinstance(obj["flip"], bool):
        problems.append(Problem("flip", "must be true or false"))
    if version == 1:
        art = obj.get("art")
        if not isinstance(art, dict) or not art:
            problems.append(Problem("art", "must be an object of pictures by id"))
            art = {}
        elif len(art) > LIMITS["art_pieces"]:
            problems.append(Problem("art", f"at most {LIMITS['art_pieces']} pictures"))
        else:
            for name, svg in art.items():
                if not isinstance(name, str) or not ART_ID.match(name):
                    problems.append(Problem(f"art.{name}", "the id must be lower-case letters, digits and underscores"))
                    continue
                for message in check_svg(svg):
                    problems.append(Problem(f"art.{name}", message))
        known, where = art, "art"
    else:
        known, where = _pics(problems, obj.get("pics")), "pics"
    panels = obj.get("panels")
    used, seen_ids = set(), set()
    if not isinstance(panels, list) or not panels:
        problems.append(Problem("panels", "needs at least one panel"))
    elif len(panels) > LIMITS["panels"]:
        problems.append(Problem("panels", f"at most {LIMITS['panels']} panels"))
    else:
        for i, panel in enumerate(panels):
            _panel(problems, f"panels[{i}]", panel, known if isinstance(known, dict) else {}, seen_ids, used, version)
    for name in sorted(set(known) - used) if isinstance(known, dict) else []:
        problems.append(Problem(f"{where}.{name}", "is not used by any layer"))
    try:
        size = len(json.dumps(obj, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        size = 0
    if size > LIMITS["bundle_bytes"]:
        problems.append(Problem("", f"the bundle is {size} bytes; the limit is {LIMITS['bundle_bytes']}"))
    if version == 1 and not problems:  # the strip shown for reduced motion paints every layer of every panel at once, so the whole script has a limit too
        costs = {name: art_cost(svg) for name, svg in obj["art"].items()}
        total = sum(costs[layer["art"]] for panel in panels for layer in panel["layers"])
        if total > LIMITS["bundle_cost"]:
            problems.append(Problem("", f"the pictures of all the panels together cost {total} to draw; the limit is {LIMITS['bundle_cost']}"))
    return problems


# ---- the art ----------------------------------------------------------------------------------------------------------------

ALLOWED_TAGS = {
    "svg", "g", "defs", "title", "desc", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text", "tspan", "use",
    "linearGradient", "radialGradient", "stop", "pattern", "clipPath", "mask", "filter",
    "feGaussianBlur", "feColorMatrix", "feOffset", "feMerge", "feMergeNode", "feFlood", "feComposite", "feBlend", "feTurbulence", "feDisplacementMap",
}
_GEOMETRY = {"x", "y", "width", "height", "cx", "cy", "r", "rx", "ry", "x1", "y1", "x2", "y2", "fx", "fy", "fr", "offset", "dx", "dy"}
_NUMBER_LISTS = {"points", "viewBox", "stroke-dasharray", "values", "baseFrequency", "stdDeviation", "kernelMatrix"}
_PAINT = {"fill", "stroke", "stop-color", "flood-color"}
_REFERENCES = {"clip-path", "mask", "filter"}
_PLAIN_NUMBERS = {
    "stroke-width", "stroke-miterlimit", "stroke-dashoffset", "opacity", "fill-opacity", "stroke-opacity", "stop-opacity", "flood-opacity",
    "font-size", "letter-spacing", "numOctaves", "seed", "scale", "k1", "k2", "k3", "k4",
}
_WORDS = {
    "stroke-linecap", "stroke-linejoin", "fill-rule", "clip-rule", "font-weight", "font-style", "text-anchor", "dominant-baseline",
    "gradientUnits", "spreadMethod", "patternUnits", "patternContentUnits", "filterUnits", "primitiveUnits", "maskUnits", "maskContentUnits",
    "clipPathUnits", "in", "in2", "result", "mode", "type", "operator", "xChannelSelector", "yChannelSelector", "preserveAspectRatio", "stitchTiles",
}
_TRANSFORMS = {"transform", "gradientTransform", "patternTransform"}
ALLOWED_ATTRS = _GEOMETRY | _NUMBER_LISTS | _PAINT | _REFERENCES | _PLAIN_NUMBERS | _WORDS | _TRANSFORMS | {"d", "id", "href", "font-family", "xmlns"}
ROOT_ATTRS = {"viewBox", "width", "height", "preserveAspectRatio", "xmlns"}

_NUM = r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"  # a digit run can be read in one way only, so a failing match is linear, not quadratic
_NUMBER = re.compile(rf"^{_NUM}(?:%|px)?\Z")
_NUMBER_LIST = re.compile(rf"^\s*{_NUM}(?:[\s,]+{_NUM})*\s*\Z")
_PATH = re.compile(r"^[MmZzLlHhVvCcSsQqTtAa0-9eE+\-.,\s]*\Z")
_TRANSFORM = re.compile(r"^[A-Za-z0-9 ,.()+\-]*\Z")
_WORD = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}\Z")
_FONT = re.compile(r"^[A-Za-z0-9 ,_'-]{0,80}\Z")  # plain font names, a single quote around a name with a space is fine
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}\Z")
_COLOUR = re.compile(r"^(?:none|currentColor|transparent|#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20}|(?:rgb|rgba|hsl|hsla)\([0-9 ,.%]+\))\Z")
_URL = re.compile(r"^url\(#([A-Za-z][A-Za-z0-9_-]{0,39})\)\Z")
_TEXT = re.compile(r"^[^\x00-\x1f\x7f-\x9f<>&  ‪-‮⁦-⁩﻿]*\Z")
_BAD_MARKUP = re.compile(r"<!DOCTYPE|<!ENTITY|<\?|<!\[CDATA\[|<!--", re.IGNORECASE)


def _local(tag):
    if not isinstance(tag, str):
        return None
    if tag.startswith("{"):
        namespace, _, name = tag[1:].partition("}")
        return name if namespace == SVG_NS else None
    return None


def _attribute_problem(tag, name, value):
    """A message when an attribute of the art is not allowed; None when it is fine."""
    if "{" in name or ":" in name:
        return f"the attribute {name} is not allowed (use plain href; no other namespaces)"
    if name not in ALLOWED_ATTRS:
        return f"the attribute {name} is not allowed on <{tag}>"
    if name == "href":
        if tag != "use":
            return "href is only allowed on <use>"
        return None if re.fullmatch(r"#[A-Za-z][A-Za-z0-9_-]{0,39}", value) else "href must point at an id inside the same picture (#name)"
    if name == "id":
        return None if _ID.match(value) else "an id must be letters, digits, - or _"
    if name == "d":
        if len(value) > LIMITS["path_chars"]:
            return f"a path is at most {LIMITS['path_chars']} characters"
        return None if _PATH.match(value) else "a path may only hold path commands and numbers"
    if name in _GEOMETRY:
        return None if len(value) <= LIMITS["number_chars"] and _NUMBER.match(value) else f"{name} must be a number"
    if name in _NUMBER_LISTS:
        return None if len(value) <= LIMITS["list_chars"] and _NUMBER_LIST.match(value) else f"{name} must be a list of numbers"
    if name in _PLAIN_NUMBERS:
        return None if len(value) <= LIMITS["number_chars"] and re.fullmatch(_NUM, value) else f"{name} must be a number"
    if name in _TRANSFORMS:
        return None if _TRANSFORM.match(value) else "a transform may only hold transform functions and numbers"
    if name in _PAINT:
        return None if (_COLOUR.match(value) or _URL.match(value)) else f"{name} must be a colour, none or url(#id)"
    if name in _REFERENCES:
        return None if (value == "none" or _URL.match(value)) else f"{name} must be none or url(#id)"
    if name == "font-family":
        return None if _FONT.match(value) else "font-family may only hold plain font names"
    if name == "xmlns":
        return None if value == SVG_NS else "xmlns must be the SVG namespace"
    if name in _WORDS:
        return None if (_WORD.match(value) or re.fullmatch(r"[A-Za-z0-9 ]{1,40}", value)) else f"{name} must be a plain word"
    return f"the attribute {name} is not allowed"


def _numbers_in(value):
    return [float(x) for x in re.findall(_NUM, value)]


def check_svg(text):
    """The problems (plain sentences) of one picture; an empty list means it may be shown."""
    if not isinstance(text, str):
        return ["must be SVG text"]
    if len(text.encode("utf-8")) > LIMITS["art_bytes"]:
        return [f"is larger than {LIMITS['art_bytes']} bytes"]
    # The standard library's parser is safe here because nothing that makes it unsafe can be in the text: external entities and
    # "billion laughs" both need an entity declaration, which needs a DOCTYPE (or an ENTITY), and both are refused before parsing. The
    # text is a Python string, so no encoding declaration can change how it is read either (a processing instruction is refused).
    if _BAD_MARKUP.search(text):
        return ["may not hold a DOCTYPE, an entity, a processing instruction, a comment or CDATA"]
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        return [f"is not well-formed SVG: {error}"]
    problems, ids, refs, count, by_id, uses, kinds = [], set(), [], 0, {}, [], {}
    if _local(root.tag) != "svg":
        return ["the root element must be <svg> in the SVG namespace"]
    stack = [(root, 1, False)]
    while stack:
        element, depth, inside = stack.pop()
        count += 1
        if count > LIMITS["svg_elements"]:
            problems.append(f"has more than {LIMITS['svg_elements']} elements")
            break
        if depth > LIMITS["svg_depth"]:
            problems.append(f"is nested more than {LIMITS['svg_depth']} levels deep")
            break
        tag = _local(element.tag)
        if tag is None or tag not in ALLOWED_TAGS:
            problems.append(f"the element <{element.tag.split('}')[-1]}> is not allowed")
            continue
        kinds[tag] = kinds.get(tag, 0) + 1
        if tag == "use":
            uses.append(element)
            if inside:
                problems.append("<use> may not be used inside a pattern, mask or clip path")
        for name, value in element.attrib.items():
            if element is root:
                if name not in ROOT_ATTRS:
                    problems.append(f"the attribute {name} is not allowed on the root <svg>")
                    continue
            message = _attribute_problem(tag, name, value)
            if message:
                problems.append(message)
                continue
            if name == "id":
                ids.add(value)
                by_id[value] = element
            elif name == "href":
                refs.append(("href", value[1:]))
            elif name in _PAINT | _REFERENCES:
                found = _URL.match(value)
                if found:
                    refs.append((name, found.group(1)))
                    if inside:  # what a pattern, mask or clip path draws never draws anything else, so cost cannot multiply through them
                        problems.append(f"{name} may not point at anything from inside a pattern, mask or clip path")
            if tag in _REGIONS and name in ("width", "height") and _region_too_big(value):
                problems.append(f"a {tag} region is at most {LIMITS['region_percent']}% or {LIMITS['region_units']} units")
            if name in {"stdDeviation"} and any(n > 40 for n in _numbers_in(value)):
                problems.append("stdDeviation is at most 40")
            if name == "numOctaves" and float(value) > 4:
                problems.append("numOctaves is at most 4")
            if name == "scale" and abs(float(value)) > 100:
                problems.append("scale is at most 100")
        if element is root:
            box = element.attrib.get("viewBox", "")
            numbers = _numbers_in(box)
            if len(numbers) != 4 or numbers[2] <= 0 or numbers[3] <= 0 or max(abs(n) for n in numbers) > 10000:
                problems.append("the root <svg> needs a viewBox of four numbers (width and height above 0, none beyond 10000)")
        if tag in ("text", "tspan", "title", "desc"):
            if element.text and (not _TEXT.match(element.text) or len(element.text) > LIMITS["svg_text_chars"]):
                problems.append(f"text inside <{tag}> must be plain and at most {LIMITS['svg_text_chars']} characters")
        elif element.text and element.text.strip():
            problems.append(f"<{tag}> may not hold text")
        if element.tail and element.tail.strip():
            problems.append("text outside an element is not allowed")
        for child in reversed(list(element)):
            stack.append((child, depth + 1, inside or tag in _CONTENT_ONLY))
    for name in sorted({target for _, target in refs} - ids):
        problems.append(f"refers to #{name}, which is not defined in the same picture")
    for attribute, target in refs:
        wanted = _TARGETS.get(attribute)
        found = by_id.get(target)
        if wanted is not None and found is not None and _local(found.tag) not in wanted:
            problems.append(f"{attribute} must point at {_TARGET_WORDS[attribute]}")
        elif attribute in ("stop-color", "flood-color"):
            problems.append(f"{attribute} must be a colour")
    # one level of <use> only: a <use> that points at something holding another <use> can multiply into millions of copies
    for element in uses:
        target = by_id.get(element.attrib.get("href", "#")[1:])
        if target is not None and any(_local(e.tag) == "use" for e in target.iter()):
            problems.append("a <use> may not point at something that holds another <use>")
            break
    for tag, limit in (("use", 400), ("filter", 12), ("feTurbulence", 2), ("mask", 12), ("pattern", 12)):
        if kinds.get(tag, 0) > limit:
            problems.append(f"at most {limit} <{tag}> elements")
    if not problems:
        cost = _cost(root, by_id, {})
        if cost > LIMITS["svg_cost"]:
            problems.append(f"is too expensive to draw (cost {cost}; the limit is {LIMITS['svg_cost']}): too many copies, filters or patterns")
    return problems


# What the browser has to do to draw a picture: every element once, a `use` again for everything it points at, a filtered element
# for the filter, and an element that is painted with a pattern, masked or clipped again for what that draws. The checks above keep a
# pattern, mask or clip path from pointing at or using anything, so this cannot loop and the numbers cannot multiply through a chain.
_CONTENT_ONLY = {"pattern", "mask", "clipPath"}
_REGIONS = {"filter", "mask", "pattern"}
_TARGETS = {"fill": {"linearGradient", "radialGradient", "pattern"}, "stroke": {"linearGradient", "radialGradient", "pattern"},
            "clip-path": {"clipPath"}, "mask": {"mask"}, "filter": {"filter"}}
_TARGET_WORDS = {"fill": "a gradient or a pattern", "stroke": "a gradient or a pattern", "clip-path": "a clipPath", "mask": "a mask", "filter": "a filter"}


def _region_too_big(value):
    found = re.fullmatch(rf"({_NUM})(%|px)?", value)
    if not found:
        return False  # the attribute check has already refused it
    number = abs(float(found.group(1)))
    return number > (LIMITS["region_percent"] if found.group(2) == "%" else LIMITS["region_units"])


def _cost(element, by_id, memo):
    key = id(element)
    if key in memo:
        return memo[key]
    total = 1
    attrs = element.attrib
    if attrs.get("filter", "none") != "none":
        total += LIMITS["filter_cost"]
    for name in ("fill", "stroke", "clip-path", "mask"):
        found = _URL.match(attrs.get(name, ""))
        target = by_id.get(found.group(1)) if found else None
        if target is not None and _local(target.tag) in _CONTENT_ONLY:
            total += _cost(target, by_id, memo)
    if _local(element.tag) == "use":
        target = by_id.get(attrs.get("href", "#")[1:])
        if target is not None:
            total += _cost(target, by_id, memo)
    for child in element:
        total += _cost(child, by_id, memo)
    memo[key] = total
    return total


def art_cost(text):
    """The cost of a picture that passes the other checks (see _cost); 0 when it does not parse. Used by the tests and the build tool."""
    if _BAD_MARKUP.search(text):  # the same guard as check_svg: nothing that makes the standard parser unsafe ever reaches it
        return 0
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return 0
    by_id = {e.attrib["id"]: e for e in root.iter() if "id" in e.attrib}
    return _cost(root, by_id, {})
