"""Build the static verification website: `python -m l3mon verify build DB OUT --sponsors DIR [--base-url URL]`.

OUT receives (and nothing else; the same input gives the same bytes):

    index.html                 the lookup page: type a code, go to its certificate
    404.html                   "No signal": no certificate has that code (nginx serves it for any unknown address)
    c/<CODE>.html              one page per code: the certificate, or a short notice for a withdrawn or replaced code (no personal details)
    c/<CODE>.pdf               with --certificate CAT=design.svg: the printable certificate of every valid code of CAT (certificate.py), served at
                               /<CODE>/certificate.pdf and linked from its page
    assets/site.<hash>.css     the styles, assets/site.<hash>.js the one script, assets/favicon.svg, assets/sponsors/<hash>-<file> the logos
    robots.txt, build.json     no indexing of certificate pages; a summary of the build (counts and file checksums, no personal data)

nginx (deploy/nginx/verify/verify.conf) maps https://verify.<domain>/<CODE> to c/<CODE>.html. Every page works without JavaScript and under a
strict Content-Security-Policy (no inline script, no inline style). The sponsor folder holds `sponsors.json` ([{"name": ..., "file": ...}, ...]
in the order of the crawl) and the logo files (PNG, SVG, WebP or JPEG, 300 KB at most; an SVG must be a plain drawing).

Standard library only.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import secrets
import shutil
import struct
import tempfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from . import certificate, codes, qr, store

HERE = Path(__file__).resolve().parent / "site"
BASE_URL = "https://verify.l3m0nctf.xyz"
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
LOGO_TYPES = {".png": "image/png", ".svg": "image/svg+xml", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
LOGO_MAX = 300 * 1024

CERT = {
    "ORG": ("Certificate of Recognition", "For running the broadcast behind the scenes.", "Crew"),
    "PAR": ("Certificate of Participation", "For going on air in the online qualifiers and scoring TRP.", "Qualifiers"),
    "ACH": ("Certificate of Achievement", "For finishing on the podium of the finals.", "Finals"),
    "APP": ("Certificate of Appreciation", "For reaching the finals of L3m0n CTF 2026.", "Finals"),
}

SPRITE = (
    '<svg class="sprite" aria-hidden="true" focusable="false"><defs>'
    '<symbol id="mc-body" viewBox="0 -6 128 118"><g transform="rotate(-10 64 60)"><path d="M4 58c1-3 5-4 9-8C24 33 42 22 64 22s40 11 51 28c4 4 8 5 9 8-1 3-5 4-9 8-11 17-29 28-51 28S24 75 13 66c-4-4-8-5-9-8z" fill="#ffd23f" stroke="#2a1d00" stroke-width="4.5" stroke-linejoin="round"/><path d="M24 46c6-10 18-16 32-17" fill="none" stroke="#fff6bf" stroke-width="6" stroke-linecap="round" opacity=".9"/><path d="M30 80c10 7 24 9 38 6" fill="none" stroke="#e0a000" stroke-width="5" stroke-linecap="round" opacity=".55"/><path d="M62 22c1-8 7-13 15-14" fill="none" stroke="#2a1d00" stroke-width="4.5" stroke-linecap="round"/><path d="M77 8c8-8 24-6 28 0-4 10-20 12-28 0z" fill="#5be37d" stroke="#0b3a1a" stroke-width="3.5" stroke-linejoin="round"/></g><circle cx="36" cy="74" r="5" fill="#ff8a5c" opacity=".55"/><circle cx="92" cy="70" r="5" fill="#ff8a5c" opacity=".55"/></symbol>'
    '<symbol id="mc-eyes" viewBox="0 -6 128 118"><ellipse cx="48" cy="58" rx="10.5" ry="12.5" fill="#fff" stroke="#2a1d00" stroke-width="3.2"/><circle cx="50" cy="60" r="5.2" fill="#2a1d00"/><ellipse cx="78" cy="55" rx="10.5" ry="12.5" fill="#fff" stroke="#2a1d00" stroke-width="3.2"/><circle cx="80" cy="57" r="5.2" fill="#2a1d00"/></symbol>'
    '<symbol id="mc-smile" viewBox="0 -6 128 118"><path d="M46 79c9 10 24 9 34-1" fill="#7a1e00" stroke="#2a1d00" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M55 87c4 3 11 2 15-1" fill="none" stroke="#ff7a7a" stroke-width="3" stroke-linecap="round"/></symbol>'
    '</defs></svg>'
)
LEMON = '<svg viewBox="0 -6 128 118" aria-hidden="true"><use href="#mc-body"/><g class="lm-eyes"><use href="#mc-eyes"/></g><use href="#mc-smile"/></svg>'
FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -6 128 118"><g transform="rotate(-10 64 60)"><path d="M4 58c1-3 5-4 9-8C24 33 42 22 64 22s40 11 51 28c4 4 8 5 9 8-1 3-5 4-9 8-11 17-29 28-51 28S24 75 13 66c-4-4-8-5-9-8z" '
           'fill="#ffd23f" stroke="#2a1d00" stroke-width="4.5"/><path d="M77 8c8-8 24-6 28 0-4 10-20 12-28 0z" fill="#5be37d" stroke="#0b3a1a" stroke-width="3.5"/></g>'
           '<ellipse cx="48" cy="58" rx="10" ry="12" fill="#fff" stroke="#2a1d00" stroke-width="3"/><circle cx="50" cy="60" r="5" fill="#2a1d00"/>'
           '<ellipse cx="78" cy="55" rx="10" ry="12" fill="#fff" stroke="#2a1d00" stroke-width="3"/><circle cx="80" cy="57" r="5" fill="#2a1d00"/></svg>\n')
EARS = ('<div class="ears" aria-hidden="true">'
        '<span class="ear ear-l"><svg viewBox="0 0 60 150"><path class="o" d="M30 146L8 10"/><path class="m" d="M30 146L8 10"/><circle cx="8" cy="9" r="6"/><circle class="b" cx="30" cy="146" r="9"/></svg></span>'
        '<span class="ear ear-r"><svg viewBox="0 0 60 150"><path class="o" d="M30 146L52 10"/><path class="m" d="M30 146L52 10"/><circle cx="52" cy="9" r="6"/><circle class="b" cx="30" cy="146" r="9"/></svg></span></div>')
TICK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12.5l5 5L20 6.5" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>'
CROSS = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>'
POWER = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v8M7.5 6.3a7 7 0 1 0 9 0" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/></svg>'


class SiteError(Exception):
    pass


def e(text) -> str:
    return html.escape(str(text), quote=True)


def human_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


# ------------------------------------------------------------------ sponsors

def _logo_size(data: bytes, suffix: str) -> tuple[float, float] | None:
    if suffix == ".png" and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    if suffix == ".svg":
        text = data.decode("utf-8", "replace")
        m = re.search(r'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)\s*"', text)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


def _check_logo(path: Path, data: bytes) -> None:
    suffix = path.suffix.lower()
    if suffix not in LOGO_TYPES:
        raise SiteError(f"{path.name}: a logo must be PNG, SVG, WebP or JPEG")
    if len(data) > LOGO_MAX:
        raise SiteError(f"{path.name}: {len(data) // 1024} KB is more than {LOGO_MAX // 1024} KB")
    magic = {".png": data[:8] == b"\x89PNG\r\n\x1a\n", ".webp": data[:4] == b"RIFF" and data[8:12] == b"WEBP",
             ".jpg": data[:3] == b"\xff\xd8\xff", ".jpeg": data[:3] == b"\xff\xd8\xff"}
    if suffix in magic and not magic[suffix]:
        raise SiteError(f"{path.name}: the file is not really a {suffix[1:].upper()}")
    if suffix == ".svg":
        _check_svg(path.name, data)


# An SVG logo must be a plain drawing: only these elements (any namespace prefix is resolved first), no event handler, no link or reference that
# leaves the drawing, and a <style> without @import or outside url(). Anything else is refused: an allow-list, so a trick the list does not know fails.
SVG_NS = "http://www.w3.org/2000/svg"
SVG_ELEMENTS = {"svg", "g", "defs", "title", "desc", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
                "linearGradient", "radialGradient", "stop", "clipPath", "mask", "pattern", "symbol", "use", "style", "metadata"}
# a CSS escape (a backslash) is refused outright: it could spell @import or url( in a way a simple search cannot see
_OUTSIDE = re.compile(r"url\(\s*(?![\"']?#)|@import|@font-face|image-set|\bsrc\s*\(|expression\s*\(|javascript:|\\", re.I)


def _check_svg(name: str, data: bytes) -> None:
    bad = f"{name}: an SVG must be a plain drawing (no script, picture, link, event handler, animation or outside reference)"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SiteError(f"{name}: an SVG must be UTF-8 text") from None
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.I) or re.search(r"<\?(?!xml\s)", text):
        raise SiteError(bad)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise SiteError(f"{name}: not a well-formed SVG ({exc})") from None
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        ns, _, local = el.tag.rpartition("}")
        if ns.lstrip("{") not in (SVG_NS, "") or local not in SVG_ELEMENTS:
            raise SiteError(f"{bad}: <{local}> is not allowed")
        for attr, value in el.attrib.items():
            a_local = attr.rpartition("}")[2].lower()
            if a_local.startswith("on") or (a_local == "href" and not value.strip().startswith("#")) or _OUTSIDE.search(value):
                raise SiteError(f"{bad}: attribute {a_local}={value[:40]!r}")
        if local == "style" and _OUTSIDE.search(el.text or ""):
            raise SiteError(f"{bad}: <style> loads something from outside")
    if root.tag not in (f"{{{SVG_NS}}}svg", "svg"):
        raise SiteError(f"{name}: the top element must be <svg>")


def load_sponsors(folder: Path | None) -> list[dict]:
    if folder is None:
        return []
    folder = Path(folder)
    listing = folder / "sponsors.json"
    if not listing.is_file():
        raise SiteError(f"{folder}: no sponsors.json (a list of {{\"name\": ..., \"file\": ...}} in the order of the crawl)")
    try:
        items = json.loads(listing.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SiteError(f"{listing}: {exc}") from None
    if not isinstance(items, list):
        raise SiteError(f"{listing}: must be a list")
    out = []
    for n, item in enumerate(items, start=1):
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            raise SiteError(f"{listing} item {n}: needs a name")
        entry = {"name": str(item["name"]).strip()}
        if item.get("file"):
            name = str(item["file"])
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,80}", name) or ".." in name:
                raise SiteError(f"{listing} item {n}: file names are lowercase letters, digits, dots, dashes and underscores")
            path = folder / name
            if not path.is_file():
                raise SiteError(f"{listing} item {n}: {name} is not in {folder}")
            data = path.read_bytes()
            _check_logo(path, data)
            size = _logo_size(data, path.suffix.lower())
            entry.update(file=name, data=data, squarish=bool(size and size[1] and size[0] / size[1] < 1.6))
        out.append(entry)
    return out


# ------------------------------------------------------------------ page parts

def crawl(sponsors: list[dict], logo_urls: dict) -> str:
    if not sponsors:
        return ""
    items = []
    for s in sponsors:
        if s.get("file"):
            cls = ' class="squarish"' if s["squarish"] else ""
            items.append(f'<li><span class="plate"><img src="{e(logo_urls[s["file"]])}" alt="{e(s["name"])}"{cls} decoding="async"></span></li>')
        else:
            items.append(f'<li><span class="plate txt">{e(s["name"])}</span></li>')
    lst = "".join(items)
    # the second copy only makes the loop seamless: hidden from screen readers, and its pictures carry no text of their own
    dup = re.sub(r' alt="[^"]*"', ' alt=""', lst)
    return ('<div class="crawl" role="region" aria-label="Our sponsors"><div class="tag">Sponsored by</div>'
            f'<div class="lane"><div class="track"><ul>{lst}</ul><ul aria-hidden="true">{dup}</ul></div></div></div>')


def osd(ch: str, live: str, caption: str = "Verified broadcast") -> str:
    return (f'<header class="osd"><a class="brand" href="/" aria-label="L3m0n-TV verification portal: home">{LEMON}<b>L3m0n<i>-TV</i></b></a>'
            f'<div class="live"><span class="dot">{e(live)}</span><span class="hide-sm">{e(caption)}</span><span class="ch">{e(ch)}</span></div></header>')


def words(title: str) -> str:
    return " ".join(f'<span class="w">{e(w)}</span>' for w in title.split())


def panel(lcd: str, print_button: bool, pdf_url: str | None = None) -> str:
    if pdf_url:  # the organisers' own certificate document: a plain link, so it works without the script too
        pb = f'<a class="pbtn" href="{e(pdf_url)}" title="Download the certificate (PDF)">DOWNLOAD PDF</a>'
    else:
        pb = '<button type="button" class="pbtn js-only" id="print" title="Save this certificate as a PDF, or print it">SAVE PDF</button>' if print_button else ""
    return ('<aside class="panel" aria-label="TV controls">'
            f'<button type="button" class="pbtn pwr js-only" id="power" aria-label="Power: switch on again and replay">{POWER}</button>'
            '<div class="knobs" aria-hidden="true"><div class="knob-w"><div class="knob vol"><i></i></div><small>VOL</small></div>'
            '<div class="knob-w"><div class="knob tune"><i></i></div><small>TUNE</small></div></div>'
            f'<div class="lcd-w"><div class="lcd">{e(lcd)}</div><small aria-hidden="true">CHANNEL</small></div>'
            f'<div class="chb"><a class="pbtn" href="/">CHECK ANOTHER</a>{pb}</div>'
            '<div class="grill" aria-hidden="true"></div><span class="badge" aria-hidden="true">L3MON<small>ELECTRONICS</small></span></aside>')


def page(*, title: str, description: str, body_class: str, screen: str, lcd: str, print_button: bool, assets: dict, index: bool, crawl_html: str,
         pdf_url: str | None = None) -> str:
    robots = "" if index else '<meta name="robots" content="noindex, nofollow">\n'
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"{robots}<title>{e(title)}</title>\n<meta name=\"description\" content=\"{e(description)}\">\n"
        '<meta name="color-scheme" content="dark">\n<meta name="referrer" content="no-referrer">\n'
        f'<link rel="icon" href="{e(assets["favicon"])}" type="image/svg+xml">\n'
        f'<link rel="stylesheet" href="{e(assets["css"])}">\n<script src="{e(assets["js"])}" defer></script>\n</head>\n'
        f'<body class="{" ".join(["play", *body_class.split()])}">\n<a class="skip" href="#main">Skip to the content</a>\n{SPRITE}\n'
        '<div class="wrap"><div class="stage"><div class="set">'
        f'{EARS}<div class="peek" aria-hidden="true">{LEMON}</div>'
        f'<div class="bez"><div class="screen"><main id="main" class="prog">{screen}</main>{crawl_html}'
        '<div class="fx scan"></div><div class="fx vignette"></div><div class="fx glass"></div><div class="fx static"></div>'
        '<div class="fx bars"><span>L3M0N-TV · TUNING</span></div><div class="fx power"></div></div></div>'
        f'{panel(lcd, print_button, pdf_url)}<div class="legs" aria-hidden="true"></div></div></div>'
        '<p class="foot">L3m0n CTF 2026 · TIFAC-CORE in Cyber Security, Amrita Vishwa Vidyapeetham, Coimbatore</p></div>\n'
        "</body>\n</html>\n"
    )


def cert_screen(c: store.Credential, base_url: str, pdf_url: str | None = None) -> str:
    title, sub, round_name = CERT[c.category]
    if c.category == "ORG":
        hero_label, hero = "Name", c.name
        rows = [("Event Name", c.event_name), ("Role", c.role), ("Issue Date", human_date(c.issue_date))]
        ch = "CH 00"
    else:
        hero_label, hero = "Username", c.username
        rows = [("Event Name", c.event_name), ("Role", c.role), ("Studio Name", c.studio_name), ("Issue Date", human_date(c.issue_date))]
        ch = "CH 26" if c.category == "PAR" else f"CH {c.studio_rank:02d}"[:6]
    rows_html = "".join(f'<div class="row"><dt>{e(k)}</dt><dd><span class="type">{e(v)}</span></dd></div>' for k, v in rows)
    rank = ""
    if c.category in ("ACH", "APP"):
        txt = codes.ordinal(c.studio_rank)
        num, sfx = str(c.studio_rank), txt[len(str(c.studio_rank)):]
        digits = "".join(f"<span>{d}</span>" for d in num)
        rank = (f'<div class="rank"><small>Studio Rank</small><span class="sr">{e(txt)}</span>'
                f'<div class="flip" aria-hidden="true">{digits}<span class="sfx">{e(sfx)}</span></div></div>')
    url = f"{base_url}/{c.verify_code}"
    return (
        osd(ch, "Live")
        + '<div class="body"><div>'
        f'<div class="kicker">L3m0n CTF 2026 · {e(round_name)}</div><h1 class="title" aria-label="{e(title)}">{words(title)}</h1>'
        f'<p class="sub">{e(sub)}</p><p class="who"><small>{e(hero_label)}</small>{e(hero)}</p><dl class="rows">{rows_html}</dl></div>'
        f'<div class="side">{rank}<div class="stamp" role="img" aria-label="Signal verified: this certificate is genuine"><div>{TICK}Signal<br>verified<em>L3M0N · 26</em></div></div></div></div>'
        f'<footer class="verify"><div class="left"><div class="qr">{qr.svg(url, label="QR code for " + c.verify_code)}</div>'
        f'<div class="code"><span>Check this certificate at</span>{e(base_url.split("://", 1)[-1])}/<b>{e(c.verify_code)}</b>'
        + (f'<a class="dl" href="{e(pdf_url)}">Download the certificate (PDF)</a>' if pdf_url else "")
        + '</div></div>'
        '<div class="org">TIFAC-CORE in Cyber Security<br>Amrita Vishwa Vidyapeetham, Coimbatore</div></footer>'
    )


def notice_screen(*, kicker: str, title: str, message: str, stamp: tuple[str, str] | None, extra: str = "", lookup: bool = False) -> str:
    stamp_html = ""
    if stamp:
        stamp_html = f'<div class="stamp bad" role="img" aria-label="{e(stamp[0])}"><div>{CROSS}{stamp[1]}<em>L3M0N · 26</em></div></div>'
    form = lookup_form() if lookup else ""
    return (osd("CH --", "Off air", "No signal")
            + f'<div class="body one"><div><div class="kicker">{e(kicker)}</div><h1 class="title" aria-label="{e(title)}">{words(title)}</h1>'
            f'<p class="msg">{message}</p>{extra}{form}</div>{stamp_html}</div><div></div>')


def lookup_form() -> str:
    return ('<form class="lookup" id="lookup" action="/check" method="get" role="search">'
            '<label for="code">Certificate code</label><div class="field">'
            '<input id="code" name="code" type="text" inputmode="text" autocomplete="off" autocapitalize="characters" spellcheck="false" maxlength="80" '
            'placeholder="L3M0N26-PAR-BKXQT" aria-describedby="lookup-hint lookup-err" required>'
            '<button type="submit">Check</button></div>'
            '<p class="hint" id="lookup-hint">The code is printed on the certificate, next to its QR code. Capital letters and dashes as printed.</p>'
            '<p class="err" id="lookup-err" aria-live="polite"></p></form>')


def index_screen() -> str:
    return (osd("CH 26", "Live", "Verification desk")
            + '<div class="body one"><div><div class="kicker">L3m0n CTF 2026 · Verification portal</div>'
            f'<h1 class="title" aria-label="Check a certificate">{words("Check a certificate")}</h1>'
            '<p class="msg">Every L3m0n CTF 2026 certificate carries a code like <code>L3M0N26-PAR-BKXQT</code>. Type it here, or scan the QR code on the certificate, '
            'to see who it was issued to and confirm that it is genuine.</p>'
            f'{lookup_form()}</div></div><div></div>')


# ------------------------------------------------------------------ the build

def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdf_path(code: str) -> str:
    """The address of a code's PDF certificate (deploy/nginx/verify/verify.conf serves c/<CODE>.pdf there)."""
    return f"/{code}/certificate.pdf"


def build(db_path: Path, out: Path, *, sponsors_dir: Path | None = None, base_url: str = BASE_URL,
          certificates: dict | None = None, browser: str | None = None, log=None) -> dict:
    """certificates: category -> certificate.Template; every valid code of those categories also gets c/<CODE>.pdf."""
    base_url = base_url.rstrip("/")
    if not re.fullmatch(r"https://[a-z0-9.-]+", base_url):
        raise SiteError(f"--base-url must be https://<host> with no path, got {base_url!r}")
    out = Path(out).resolve()
    if out.exists() and not (out / "build.json").is_file():
        raise SiteError(f"{out} exists and is not a previous build (no build.json): refusing to replace it")
    conn = store.connect(Path(db_path))
    try:
        creds = sorted(store.all_rows(conn), key=lambda c: c.verify_code)
    finally:
        conn.close()
    sponsors = load_sponsors(sponsors_dir)
    certificates = certificates or {}
    for cat in certificates:
        if cat not in codes.CATEGORIES:
            raise SiteError(f"--certificate: unknown category {cat!r} (one of {', '.join(codes.CATEGORIES)})")
    previous = {}
    if (out / "build.json").is_file():
        try:
            previous = json.loads((out / "build.json").read_text(encoding="utf-8"))
        except ValueError:
            previous = {}

    files: dict[str, bytes] = {}
    css, js = (HERE / "site.css").read_bytes(), (HERE / "site.js").read_bytes()
    assets = {"css": f"/assets/site.{_digest(css)[:10]}.css", "js": f"/assets/site.{_digest(js)[:10]}.js", "favicon": "/assets/favicon.svg"}
    files[assets["css"][1:]] = css
    files[assets["js"][1:]] = js
    files["assets/favicon.svg"] = FAVICON.encode("utf-8")
    logo_urls = {}
    for s in sponsors:
        if s.get("file"):
            rel = f"assets/sponsors/{_digest(s['data'])[:8]}-{s['file']}"
            files[rel] = s["data"]
            logo_urls[s["file"]] = "/" + rel
    crawl_html = crawl(sponsors, logo_urls)

    def put(rel: str, text: str) -> None:
        files[rel] = text.encode("utf-8")

    put("index.html", page(title="Check a certificate · L3m0n CTF 2026", description="Verify an L3m0n CTF 2026 certificate by its code.",
                           body_class="", screen=index_screen(), lcd="CHECK", print_button=False, assets=assets, index=True, crawl_html=crawl_html))
    put("404.html", page(title="No signal · L3m0n CTF 2026", description="No L3m0n CTF 2026 certificate has this code.", body_class="st-missing",
                         screen=notice_screen(kicker="No signal", title="No certificate found",
                                              message="No L3m0n CTF 2026 certificate has this code. Check it against the certificate: it reads like "
                                                      "<code>L3M0N26-PAR-BKXQT</code>, a category and five letters or digits.",
                                              stamp=("Not verified", "Not<br>verified"), lookup=True),
                         lcd="NO SIGNAL", print_button=False, assets=assets, index=False, crawl_html=crawl_html))
    put("429.html", page(title="Please wait · L3m0n CTF 2026", description="Too many checks from this address in a short time.", body_class="st-missing",
                         screen=notice_screen(kicker="Please stand by", title="Too many checks",
                                              message="This address checked a lot of certificates in a short time. Wait a minute, then try again.",
                                              stamp=None),
                         lcd="STAND BY", print_button=False, assets=assets, index=False, crawl_html=crawl_html))
    counts = {"valid": {c: 0 for c in codes.CATEGORIES}, "withdrawn": 0, "replaced": 0}
    pdfs, pdf_keys, jobs = {}, {}, {}
    for c in creds:
        t = certificates.get(c.category)
        if c.status != "valid" or t is None:
            continue
        values = certificate.fields(c, base_url, human_date)
        k = pdf_keys[c.verify_code] = certificate.key(t, values)
        rel = f"c/{c.verify_code}.pdf"
        old = out / rel
        # an unchanged certificate is not printed again: the previous build's PDF is reused when its input and its bytes are the ones recorded
        if previous.get("certificates", {}).get(c.verify_code) == k and old.is_file():
            data = old.read_bytes()
            if previous.get("files", {}).get(rel) == _digest(data):
                pdfs[c.verify_code] = data
                continue
        jobs[c.verify_code] = (certificate.print_page(t, certificate.fill(t, values), f"{CERT[c.category][0]} · L3m0n CTF 2026 · {c.verify_code}"),
                                  c.issue_date)
    if jobs and log:
        log(f"Printing {len(jobs)} PDF certificate(s) ({len(pdfs)} unchanged ones reused)...")
    try:
        pdfs.update(certificate.render(jobs, browser=browser))
    except certificate.CertificateError as exc:
        raise SiteError(str(exc)) from None
    for code, data in pdfs.items():
        files[f"c/{code}.pdf"] = data
    for c in creds:
        if not codes.CODE_RE.match(c.verify_code):
            raise SiteError(f"not a valid code in the database: {c.verify_code!r}")
        if c.status == "valid":
            counts["valid"][c.category] += 1
            who = c.name if c.category == "ORG" else c.username
            pdf = pdf_path(c.verify_code) if c.verify_code in pdfs else None
            put(f"c/{c.verify_code}.html", page(
                title=f"{CERT[c.category][0]} · {who} · L3m0n CTF 2026", description=f"A genuine L3m0n CTF 2026 certificate ({c.role}).",
                body_class=f"cat-{c.category.lower()}", screen=cert_screen(c, base_url, pdf),
                lcd=c.category, print_button=True, assets=assets, index=False, crawl_html=crawl_html, pdf_url=pdf))
        elif c.status == "replaced":
            counts["replaced"] += 1
            put(f"c/{c.verify_code}.html", page(
                title="Certificate replaced · L3m0n CTF 2026", description="This L3m0n CTF 2026 code was replaced by a newer certificate.",
                body_class="st-replaced", screen=notice_screen(
                    kicker="Replaced", title="This code was replaced",
                    message=f"The holder of <code>{e(c.verify_code)}</code> was given a newer L3m0n CTF 2026 certificate, which replaces this one.",
                    stamp=("Replaced by a newer certificate", "Replaced"),
                    extra=f'<a class="goto" href="/{e(c.replaced_by)}">Open the current certificate</a>'),
                lcd="REPLACED", print_button=False, assets=assets, index=False, crawl_html=crawl_html))
        else:
            counts["withdrawn"] += 1
            put(f"c/{c.verify_code}.html", page(
                title="Certificate withdrawn · L3m0n CTF 2026", description="This L3m0n CTF 2026 certificate was withdrawn by the organisers.",
                body_class="st-withdrawn", screen=notice_screen(
                    kicker="Signal lost", title="This certificate was withdrawn",
                    message=f"The certificate with the code <code>{e(c.verify_code)}</code> was withdrawn by the organisers of L3m0n CTF 2026 and is no longer valid.",
                    stamp=("Withdrawn: not valid", "Not<br>valid")),
                lcd="WITHDRAWN", print_button=False, assets=assets, index=False, crawl_html=crawl_html))
    put("robots.txt", "User-agent: *\nAllow: /$\nDisallow: /\n")
    summary = {"schema": 1, "base_url": base_url, "counts": counts, "pages": sum(1 for k in files if k.endswith(".html")),
               "files": {k: _digest(v) for k, v in sorted(files.items())}}
    if pdfs:
        summary["certificates"] = dict(sorted(pdf_keys.items()))
        summary["counts"]["pdf"] = len(pdfs)
    files["build.json"] = (json.dumps(summary, indent=1, sort_keys=True) + "\n").encode("utf-8")

    # the new build is written into a fresh folder of our own next to OUT and swapped in; nothing that existed before is deleted except OUT itself
    # (which holds a previous build, checked above)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}.new-", dir=out.parent))
    old = out.parent / f".{out.name}.old-{secrets.token_hex(6)}"
    for rel, data in files.items():
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.chmod(path, 0o644)
    for d in [tmp, *(p for p in tmp.rglob("*") if p.is_dir())]:
        os.chmod(d, 0o755)
    if out.exists():
        out.rename(old)
    tmp.rename(out)
    if old.exists():
        shutil.rmtree(old)
    return summary
