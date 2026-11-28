"""PDF certificates from the organisers' own SVG design (docs/runbooks/22-verify-portal.md, "PDF certificates").

`python -m l3mon verify build DB OUT --certificate PAR=design.svg` fills the design once per valid code of that category and prints it to
`c/<CODE>.pdf`, which the portal serves at https://verify.<domain>/<CODE>/certificate.pdf (its certificate page links to it).

The design is an ordinary SVG (Figma with "Outline text" OFF, Inkscape, Illustrator...). Where a value goes, it holds a placeholder:

    {{NAME}}      the holder: the organiser's full name, or the player's username
    {{USERNAME}}  {{STUDIO}}  {{RANK}} (1st, 2nd...)  {{CODE}}  {{DATE}} (28 November 2026)  {{EVENT}}  {{ROLE}}  {{URL}} (the code's address)

and one element whose id is `qr` (a rectangle with x, y, width and height) marks where the QR code goes; it may carry data-dark="#000000" and
data-light="#ffffff" (six-digit hex) to choose the code's two colours, otherwise the portal's navy on cream is used. The code and the holder are required:
a certificate nobody can check is not one. The design may hold pictures and fonts only as data: addresses (the build is offline), and no script.

Text that holds a placeholder is shrunk to fit when the value is long (to the page's margins, or to its own data-max-width in the design's
units when it has one), and keeps its alignment: SVG's own text-anchor (Inkscape, hand-written)
is followed; Figma bakes centring into fixed positions, so there the text layer's name says it: a name with "center" (or "centre", "middle")
keeps the placeholder's centre, one with "right" keeps its right edge, anything else keeps its left edge.

The PDF is printed by a local Chrome, Chromium or Edge in headless mode (L3MON_CHROME names one that is not found by itself), with the network cut
off. Its two timestamps are set to the Issue Date, so the same input gives the same bytes; a rebuild reuses every PDF whose input is unchanged.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from . import codes, qr

# the designs that ship with the tool (a starting point; the organisers' own design replaces one with --certificate CAT=file.svg)
BUNDLED = {"PAR": Path(__file__).resolve().parent / "certificates" / "participant.svg"}
# bump when the way a PDF is made changes, so that a rebuild prints every certificate again
RENDER_VERSION = "1"
FIELDS = ("NAME", "USERNAME", "STUDIO", "RANK", "CODE", "DATE", "EVENT", "ROLE", "URL")
TOKEN = re.compile(r"\{\{\s*([A-Za-z_]+)\s*\}\}")
UNITS = {"mm": "mm", "cm": "cm", "in": "in", "pt": "pt", "px": "px", "": "px"}
MAX_TEMPLATE = 8 * 1024 * 1024
SVG_NS = "http://www.w3.org/2000/svg"
# a reference that leaves the design: anything but #fragment and data: (url(), href, src), or CSS that loads from outside
_OUTSIDE_ATTR = re.compile(r"^\s*(?!#|data:)\S", re.I)
_OUTSIDE_CSS = re.compile(r"url\(\s*[\"']?\s*(?!#|data:)|@import", re.I)


class CertificateError(Exception):
    pass


@dataclass(frozen=True)
class Template:
    path: Path
    text: str
    width: str
    height: str
    view_w: float
    view_h: float
    digest: str


def _length(value: str | None) -> tuple[float, str] | None:
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*(mm|cm|in|pt|px|)\s*", value or "")
    return (float(m.group(1)), UNITS[m.group(2)]) if m else None


def load(path: Path) -> Template:
    """Read and check a design; every refusal says what to change."""
    path = Path(path)
    name = path.name
    if not path.is_file():
        raise CertificateError(f"{path}: no such certificate design")
    data = path.read_bytes()
    if len(data) > MAX_TEMPLATE:
        raise CertificateError(f"{name}: {len(data) // 1024} KB is more than {MAX_TEMPLATE // 1024 // 1024} MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CertificateError(f"{name}: a design must be UTF-8 text (save it again as SVG)") from None
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.I):
        raise CertificateError(f"{name}: no DOCTYPE or ENTITY (export it again as plain SVG)")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise CertificateError(f"{name}: not a well-formed SVG ({exc})") from None
    if root.tag not in (f"{{{SVG_NS}}}svg", "svg"):
        raise CertificateError(f"{name}: the top element must be <svg>")
    qr_box = None
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        local = el.tag.rpartition("}")[2]
        if local in ("script", "foreignObject", "iframe", "embed", "object"):
            raise CertificateError(f"{name}: <{local}> is not allowed in a design (no script, no embedded page)")
        for attr, value in el.attrib.items():
            a = attr.rpartition("}")[2].lower()
            if a.startswith("on"):
                raise CertificateError(f"{name}: event handler {a}= is not allowed")
            if a in ("href", "src") and _OUTSIDE_ATTR.match(value):
                raise CertificateError(f"{name}: {a}={value[:60]!r} leaves the design: embed pictures and fonts as data: addresses")
            if _OUTSIDE_CSS.search(value):
                raise CertificateError(f"{name}: {a}= loads something from outside: embed it as a data: address")
        if local == "style" and _OUTSIDE_CSS.search(el.text or ""):
            raise CertificateError(f"{name}: <style> loads something from outside (a web font?): embed the font as a data: address")
        if el.get("id") == "qr":
            box = [_length(el.get(k)) for k in ("x", "y", "width", "height")]
            if local != "rect" or None in box or any(u != "px" for _, u in box) or box[2][0] <= 0 or box[3][0] <= 0:
                raise CertificateError(f"{name}: the element with id 'qr' must be a rectangle with plain x, y, width and height")
            if qr_box is not None:
                raise CertificateError(f"{name}: two elements have the id 'qr'")
            for colour in ("data-dark", "data-light"):
                if el.get(colour) is not None and not _HEX_COLOUR.fullmatch(el.get(colour)):
                    raise CertificateError(f"{name}: the qr rectangle's {colour}= must be a six-digit hex colour such as #000000")
            qr_box = box
    if qr_box is None:
        raise CertificateError(f"{name}: no element with the id 'qr' (name a rectangle 'qr' where the QR code goes)")
    found = {m.group(1) for m in TOKEN.finditer(re.sub(r"<!--.*?-->", "", text, flags=re.S))}
    unknown = sorted(f for f in found if f not in FIELDS)
    if unknown:
        raise CertificateError(f"{name}: unknown placeholder(s) {', '.join('{{' + u + '}}' for u in unknown)}; known: "
                               + ", ".join("{{" + f + "}}" for f in FIELDS))
    if "CODE" not in found or not found & {"NAME", "USERNAME"}:
        raise CertificateError(f"{name}: a certificate must show {{{{CODE}}}} and the holder ({{{{NAME}}}} or {{{{USERNAME}}}})"
                               + (": is the text outlined? Export with 'Outline text' off" if not found else ""))
    vb = re.split(r"[\s,]+", (root.get("viewBox") or "").strip())
    w, h = _length(root.get("width")), _length(root.get("height"))
    if len(vb) == 4 and all(_length(v) for v in vb):
        view_w, view_h = float(vb[2]), float(vb[3])
    elif w and h and w[1] == "px":
        view_w, view_h = w[0], h[0]
    else:
        raise CertificateError(f"{name}: the <svg> needs a viewBox (or a width and height in px)")
    if view_w <= 0 or view_h <= 0:
        raise CertificateError(f"{name}: the viewBox has no size")
    width, height = (f"{w[0]:g}{w[1]}", f"{h[0]:g}{h[1]}") if w and h else (f"{view_w:g}px", f"{view_h:g}px")
    return Template(path=path, text=text, width=width, height=height, view_w=view_w, view_h=view_h, digest=hashlib.sha256(data).hexdigest())


def fields(c, base_url: str, human_date) -> dict[str, str]:
    holder = c.name if c.category == "ORG" else c.username
    return {
        "NAME": holder or "", "USERNAME": c.username or "", "STUDIO": c.studio_name or "",
        "RANK": codes.ordinal(c.studio_rank) if c.studio_rank else "", "CODE": c.verify_code, "DATE": human_date(c.issue_date),
        "EVENT": c.event_name, "ROLE": c.role, "URL": f"{base_url}/{c.verify_code}",
    }


def key(t: Template, values: dict) -> str:
    """What a certificate's PDF depends on: a rebuild reuses the PDF while this is unchanged."""
    return hashlib.sha256(json.dumps([RENDER_VERSION, t.digest, t.width, t.height, values], sort_keys=True).encode("utf-8")).hexdigest()


_TEXT = re.compile(r"(<text\b)([^>]*)>(.*?)</text>", re.S)
_QR = re.compile(r"<rect\b[^>]*\bid\s*=\s*[\"']qr[\"'][^>]*?(?:/>|>.*?</rect>)", re.S)
# the QR rectangle may name its own colours (data-dark, data-light); the portal's navy on cream is used when it does not
_HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{6}")


def _align(attrs: str) -> str:
    hint = " ".join(re.findall(r'\b(?:id|class|data-align|inkscape:label)\s*=\s*"([^"]*)"', attrs)).lower()
    if re.search(r"cent(?:er|re)|middle", hint):
        return "center"
    if re.search(r"(?:^|[\s_\-])(?:right|end)(?:$|[\s_\-\d])", hint):
        return "right"
    return "left"


def fill(t: Template, values: dict) -> str:
    """The design with this certificate's values in it, as inline SVG for the print page."""
    def sub(s: str) -> str:
        return TOKEN.sub(lambda m: html.escape(values[m.group(1)], quote=True) if m.group(1) in values else m.group(0), s)

    def text_el(m: re.Match) -> str:
        if not TOKEN.search(m.group(3)):
            return sub(m.group(0))
        # the placeholder markup is kept (braces as character references, so nothing fills it) for the fit script to measure
        orig = html.escape(m.group(3), quote=True).replace("{", "&#123;").replace("}", "&#125;")
        return f'{m.group(1)}{sub(m.group(2))} data-l3fit="{_align(m.group(2))}" data-l3orig="{orig}">{sub(m.group(3))}</text>'

    # one pass over the design: a value that itself looks like a placeholder (a username "{{CODE}}") stays as typed
    # the XML declaration has no place inside HTML, and comments are never drawn (load() ignores placeholders in them too)
    src = re.sub(r"<!--.*?-->", "", re.sub(r"^\s*<\?xml[^>]*\?>", "", t.text), flags=re.S)
    parts, pos = [], 0
    for m in _TEXT.finditer(src):
        parts += [sub(src[pos:m.start()]), text_el(m)]
        pos = m.end()
    svg_text = "".join(parts) + sub(src[pos:])

    def qr_el(m: re.Match) -> str:
        attrs = dict(re.findall(r'(?<![\w-])(x|y|width|height|data-dark|data-light)\s*=\s*"([^"]*)"', m.group(0)))
        colours = {k: attrs[f"data-{k}"] for k in ("dark", "light") if f"data-{k}" in attrs}
        sym = qr.svg(values["URL"], **colours)
        return sym.replace("<svg ", f'<svg x="{attrs["x"]}" y="{attrs["y"]}" width="{attrs["width"]}" height="{attrs["height"]}" ', 1)
    return _QR.sub(qr_el, svg_text, count=1)


# Runs in the print page before printing: shrinks a filled-in text that has grown past the room it has, then keeps it where the design put the
# placeholder (centre, right edge or left edge), unless the SVG aligns it itself with text-anchor.
FIT_JS = r"""
(function () {
  var root = document.querySelector("svg"), vb = root.viewBox.baseVal, W = vb && vb.width ? vb.width : root.getBBox().width, pad = W * 0.04;
  document.querySelectorAll("[data-l3fit]").forEach(function (el) {
    var filled = el.innerHTML, align = el.getAttribute("data-l3fit");
    var first = el.querySelector("tspan") || el, anchor = getComputedStyle(first).textAnchor;
    el.innerHTML = el.getAttribute("data-l3orig");
    var o = el.getBBox();
    el.innerHTML = filled;
    var n = el.getBBox(), cx = o.x + o.width / 2;
    var room = align === "center" ? 2 * Math.min(cx, W - cx) - 2 * pad : align === "right" ? o.x + o.width - pad : W - o.x - pad;
    if (anchor === "middle") room = 2 * Math.min(cx, W - cx) - 2 * pad;
    var most = parseFloat(el.getAttribute("data-max-width"));
    if (most > 0) room = Math.min(room, most);
    if (n.width > room && room > 0) {
      var k = room / n.width, all = [el].concat(Array.prototype.slice.call(el.querySelectorAll("tspan")));
      // every size is read before any is set: a tspan that inherits its size must be scaled once, not twice
      var sizes = all.map(function (t) { return parseFloat(getComputedStyle(t).fontSize); });
      all.forEach(function (t, i) { t.style.fontSize = (sizes[i] * k) + "px"; });
      n = el.getBBox();
    }
    if (anchor === "middle" || anchor === "end") return;
    var dx = align === "center" ? cx - (n.x + n.width / 2) : align === "right" ? (o.x + o.width) - (n.x + n.width) : o.x - n.x;
    if (Math.abs(dx) > 0.01) el.setAttribute("transform", "translate(" + dx + " 0) " + (el.getAttribute("transform") || ""));
  });
  document.documentElement.setAttribute("data-l3ready", "1");
})();
"""


# The fit script must not measure before the design's own fonts (embedded as data: addresses, as the runbook tells designers to) have loaded: until then the
# browser measures with a stand-in font of other widths, a long value looks as if it fits, and it is printed too wide. Found on 28 Nov 2026 with the
# supervisor's design: a long username ran into the QR code. Every declared font is loaded first; a design with no embedded font runs the script at once.
FIT_AFTER_FONTS_JS = ("Promise.all(Array.from(document.fonts || []).map(function (f) { return f.load().catch(function () {}); }))"
                      ".then(function () {" + FIT_JS + "});")


def print_page(t: Template, svg_text: str, title: str = "L3m0n CTF 2026 certificate") -> str:
    """The page the browser prints; its title becomes the PDF's title."""
    size = f"{t.width} {t.height}"
    return (f"<!doctype html><html><head><meta charset=\"utf-8\"><title>{html.escape(title)}</title><style>"
            f"@page{{size:{size};margin:0}}html,body{{margin:0;padding:0;width:{t.width};height:{t.height};overflow:hidden}}"
            f"body>svg{{display:block;width:{t.width};height:{t.height}}}</style></head><body>{svg_text}<script>{FIT_AFTER_FONTS_JS}</script></body></html>")


# ------------------------------------------------------------------ printing

CANDIDATES = ("chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "msedge", "microsoft-edge")
WINDOWS = (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
           r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
MAC = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium",
       "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")


def find_browser() -> str:
    env = os.environ.get("L3MON_CHROME")
    if env:
        if not Path(env).is_file():
            raise CertificateError(f"L3MON_CHROME={env} is not a file")
        return env
    for name in CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    for p in (*WINDOWS, *MAC):
        if Path(p).is_file():
            return p
    raise CertificateError("printing the PDF certificates needs Chrome, Chromium or Edge on this machine (or L3MON_CHROME=<path to one>)")


_DATE = re.compile(rb"/(CreationDate|ModDate) \(D:[^)]*\)")


def _stamp(pdf: bytes, iso_date: str) -> bytes:
    """Both timestamps become the Issue Date, keeping every byte offset of the file (same-length replacement only)."""
    def rep(m: re.Match) -> bytes:
        new = b"/" + m.group(1) + b" (D:" + iso_date.replace("-", "").encode() + b"000000+00'00')"
        return new if len(new) == len(m.group(0)) else m.group(0)
    return _DATE.sub(rep, pdf)


def _print_one(browser: str, work: Path, n: int, page_html: str, iso_date: str) -> bytes:
    src, out, profile = work / f"p{n}.html", work / f"p{n}.pdf", work / f"u{n}"
    src.write_text(page_html, encoding="utf-8")
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
           "--disable-background-networking", "--disable-sync", "--host-resolver-rules=MAP * ~NOTFOUND",
           "--no-pdf-header-footer", "--print-to-pdf-no-header", f"--user-data-dir={profile}", f"--print-to-pdf={out}", src.as_uri()]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        raise CertificateError("the browser took more than 2 minutes to print one certificate") from None
    if not out.is_file() or not out.read_bytes().startswith(b"%PDF"):
        raise CertificateError(f"the browser ({browser}) made no PDF")
    pdf = out.read_bytes()
    pages = re.findall(rb"/Type\s*/Pages\b[^>]*?/Count (\d+)|/Count (\d+)[^>]*?/Type\s*/Pages\b", pdf)
    count = max((int(a or b) for a, b in pages), default=1)
    if count != 1:
        raise CertificateError(f"the design printed on {count} pages: check its width and height (one page per certificate)")
    shutil.rmtree(profile, ignore_errors=True)
    return _stamp(pdf, iso_date)


def render(jobs: dict[str, tuple[str, str]], *, browser: str | None = None, workers: int | None = None) -> dict[str, bytes]:
    """jobs: code -> (print page HTML, Issue Date). Returns code -> PDF bytes. Several browsers print at once."""
    if not jobs:
        return {}
    browser = browser or find_browser()
    workers = workers or max(1, min(4, (os.cpu_count() or 2) - 1))
    with tempfile.TemporaryDirectory(prefix="l3mon-cert-") as tmp:
        work = Path(tmp)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {code: pool.submit(_print_one, browser, work, n, page_html, iso)
                       for n, (code, (page_html, iso)) in enumerate(sorted(jobs.items()))}
            return {code: f.result() for code, f in futures.items()}
