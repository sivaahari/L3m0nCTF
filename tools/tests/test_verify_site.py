"""The static verification website (tools/l3mon/verify/site.py, site/site.css, site/site.js).

What must hold for every build: one page per code with exactly the agreed fields (the username or name shown once, the rank once), no personal
details on a withdrawn or replaced page, a QR code that an independent reader decodes to the page's own address, nothing inline that the strict
Content-Security-Policy would block, every asset the pages use present, and the same bytes for the same input.
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import zlib
from html.parser import HTMLParser
from pathlib import Path

import pytest

from l3mon.verify import codes, issue, site, store
from qr_reference_reader import decode as qr_read

BASE = "https://verify.example.test"


# ------------------------------------------------------------------ fixtures

def tiny_png(w: int, h: int) -> bytes:
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))  # noqa: E731
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


@pytest.fixture
def sponsors(tmp_path):
    d = tmp_path / "sponsors"
    d.mkdir()
    (d / "wide.png").write_bytes(tiny_png(60, 20))
    (d / "square.png").write_bytes(tiny_png(30, 30))
    (d / "plain.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 100"><rect width="300" height="100" fill="#123"/></svg>', encoding="utf-8")
    (d / "sponsors.json").write_text(json.dumps([{"name": "Wide Co", "file": "wide.png"}, {"name": "Square & Sons", "file": "square.png"},
                                                 {"name": "Vector Ltd", "file": "plain.svg"}, {"name": "Text Only"}]), encoding="utf-8")
    return d


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "c.sqlite"
    conn = store.connect(path, create=True)
    p = tmp_path / "p.csv"
    p.write_text("user_id,username,studio\n11,ctf.username,Test\n12,neo.sample,Test\n13,trinity_x,<b>Byte</b> \"Bandits\"\n14,morpheus,Null Pointers\n", encoding="utf-8")
    issue.issue(conn, issue_date="2026-11-30", org=["Jane Doe", "Alex Example"], prelims=issue.read_prelims(p))
    f = tmp_path / "f.csv"
    f.write_text("username,studio,rank,user_id\nctf.username,Test,1,11\nmorpheus,Null Pointers,16,14\n", encoding="utf-8")
    issue.issue(conn, issue_date="2026-12-12", finals=issue.read_finals(f))
    org = next(c for c in store.all_rows(conn) if c.category == "ORG" and c.name == "Alex Example")
    store.withdraw(conn, org.verify_code, "made-up reason for the test")
    conn.close()
    return path


@pytest.fixture
def built(tmp_path, db, sponsors):
    out = tmp_path / "site"
    summary = site.build(db, out, sponsors_dir=sponsors, base_url=BASE)
    conn = store.connect(db)
    rows = {c.verify_code: c for c in store.all_rows(conn)}
    conn.close()
    return out, summary, rows


class Page(HTMLParser):
    """Collects what the tests look at: visible text of <main>, the rows, tags with attributes, the QR path."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags, self.main_text, self.title = [], [], ""
        self._stack, self._in_title, self._skip = [], False, 0
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        self._stack.append((tag, a))
        if tag == "title":
            self._in_title = True
        if tag in ("svg", "small") and self._skip == 0 and any(t == "main" for t, _ in self._stack):
            self._skip = len(self._stack) if tag == "svg" else 0

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        while self._stack:
            t, _ = self._stack.pop()
            if self._skip and len(self._stack) < self._skip:
                self._skip = 0
            if t == tag:
                break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif any(t == "main" for t, _ in self._stack) and not self._skip:
            self.main_text.append(data)

    @property
    def text(self):
        return " ".join(" ".join(self.main_text).split())


def page(out: Path, code: str) -> str:
    return (out / "c" / f"{code}.html").read_text(encoding="utf-8")


def rows_of(html_text: str) -> list[tuple[str, str]]:
    return [(re.sub("<[^>]+>", "", k), re.sub("<[^>]+>", "", v)) for k, v in re.findall(r'<div class="row"><dt>(.*?)</dt><dd>(.*?)</dd></div>', html_text)]


# ------------------------------------------------------------------ what is built

def test_one_page_per_code_and_nothing_unexpected(built):
    out, summary, rows = built
    pages = sorted(p.name[:-5] for p in (out / "c").glob("*.html"))
    assert pages == sorted(rows)
    top = sorted(p.name for p in out.iterdir())
    assert top == ["404.html", "429.html", "assets", "build.json", "c", "index.html", "robots.txt"]
    assert summary["counts"]["valid"] == {"ORG": 1, "PAR": 2, "ACH": 1, "APP": 1} and summary["counts"]["withdrawn"] == 1 and summary["counts"]["replaced"] == 2


@pytest.mark.parametrize("category", ["ORG", "PAR", "ACH", "APP"])
def test_each_certificate_shows_exactly_the_agreed_fields_once(built, category):
    out, _, rows = built
    c = next(c for c in rows.values() if c.category == category and c.status == "valid")
    text = page(out, c.verify_code)
    labels = [k for k, _ in rows_of(text)]
    # the name or username is the large line, the rank is the rank card: neither is repeated as a row
    assert labels == (["Event Name", "Role", "Issue Date"] if category == "ORG" else ["Event Name", "Role", "Studio Name", "Issue Date"])
    values = dict(rows_of(text))
    assert values["Event Name"] == store.EVENT[category] and values["Role"] == store.ROLE[category]
    assert values["Issue Date"] == site.human_date(c.issue_date)
    who = c.name if category == "ORG" else c.username
    assert re.findall(r'<p class="who"><small>(.*?)</small>(.*?)</p>', text) == [("Name" if category == "ORG" else "Username", who)]
    visible = Page(text).text
    assert visible.count(who) == 1, "shown once on the page"
    if category in ("ACH", "APP"):
        # the rank card: "Studio rank" and the ordinal are real text for screen readers; the big flip digits are decoration
        assert re.search(rf'<div class="rank"><small>Studio Rank</small><span class="sr">{codes.ordinal(c.studio_rank)}</span><div class="flip" aria-hidden="true">', text)
        assert text.count('class="rank"') == 1
    else:
        assert 'class="rank"' not in text


def test_the_withdrawn_page_names_nobody(built):
    out, _, rows = built
    w = next(c for c in rows.values() if c.status == "withdrawn")
    text = page(out, w.verify_code)
    assert "withdrawn" in text and w.verify_code in text
    assert w.name not in text and "made-up reason" not in text, "no personal detail and never the private reason"
    assert "Signal<br>verified" not in text


def test_the_replaced_page_points_to_the_holders_current_certificate_without_details(built):
    out, _, rows = built
    for old in (c for c in rows.values() if c.status == "replaced"):
        text = page(out, old.verify_code)
        assert f'href="/{old.replaced_by}"' in text
        assert old.username not in text and old.studio_name not in text


def test_text_is_escaped(built):
    out, _, rows = built
    c = next(c for c in rows.values() if c.username == "trinity_x")
    text = page(out, c.verify_code)
    assert "&lt;b&gt;Byte&lt;/b&gt; &quot;Bandits&quot;" in text and "<b>Byte</b>" not in text


def test_every_certificate_carries_a_qr_code_an_independent_reader_decodes_to_its_own_address(built):
    out, _, rows = built
    checked = 0
    for c in rows.values():
        if c.status != "valid":
            continue
        svg = re.search(r'<div class="qr"><svg[^>]*viewBox="0 0 (\d+) \1"[^>]*>.*?<path d="([^"]+)"', page(out, c.verify_code))
        assert svg, c.verify_code
        width, path = int(svg.group(1)), svg.group(2)
        n = width - 8
        grid = [["0"] * n for _ in range(n)]
        runs = re.findall(r"M(\d+),(\d+)h(\d+)v1h-\3z", path)  # one rectangle per run of dark modules in a row
        assert "".join(f"M{x},{y}h{w}v1h-{w}z" for x, y, w in runs) == path, "the path is nothing but such runs"
        for x, y, w in runs:
            for i in range(int(w)):
                grid[int(y) - 4][int(x) - 4 + i] = "1"
        res = qr_read(["".join(r) for r in grid])
        assert res["format_ok"] and res["rs_ok"] and not res["errors"], res["errors"]
        assert res["text"] == f"{BASE}/{c.verify_code}"
        checked += 1
    assert checked == 5


def test_nothing_inline_that_the_strict_csp_would_block(built):
    out, _, _ = built
    for f in [*out.glob("*.html"), *(out / "c").glob("*.html")]:
        text = f.read_text(encoding="utf-8")
        p = Page(text)
        for tag, attrs in p.tags:
            assert "style" not in attrs, f"{f.name}: inline style on <{tag}>"
            assert not any(k.startswith("on") for k in attrs), f"{f.name}: event handler on <{tag}>"
            if tag == "script":
                assert attrs.get("src", "").startswith("/assets/"), f"{f.name}: inline or outside script"
            for k in ("href", "src", "action"):
                v = attrs.get(k)
                if v:
                    assert not v.lower().startswith(("javascript:", "data:", "http:", "//")), f"{f.name}: {k}={v}"
        assert "<style" not in text
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text)


def test_every_asset_a_page_uses_is_in_the_build(built):
    out, _, _ = built
    for f in [*out.glob("*.html"), *(out / "c").glob("*.html")]:
        for ref in re.findall(r'(?:src|href)="(/assets/[^"]+)"', f.read_text(encoding="utf-8")):
            assert (out / ref.lstrip("/")).is_file(), f"{f.name} uses {ref}"
    css = next((out / "assets").glob("site.*.css")).read_text(encoding="utf-8")
    assert "url(" not in re.sub(r'url\("data:[^"]*"\)', "", css), "the stylesheet loads nothing from elsewhere (data: pictures only)"


def test_sponsors_appear_in_order_on_light_plates_with_their_names(built):
    out, _, _ = built
    text = (out / "index.html").read_text(encoding="utf-8")
    first = text.split('<ul aria-hidden="true">')[0]
    names = re.findall(r'alt="([^"]+)"|<span class="plate txt">([^<]+)</span>', first)
    assert [a or b for a, b in names] == ["Wide Co", "Square &amp; Sons", "Vector Ltd", "Text Only"]
    assert re.search(r'alt="Square &amp; Sons" class="squarish"', text) and not re.search(r'alt="Wide Co" class="squarish"', text)
    dup = text.split('<ul aria-hidden="true">')[1].split("</ul>")[0]
    assert 'alt=""' in dup and "Wide Co" not in dup, "the looping copy is hidden from screen readers"


def test_the_same_input_gives_the_same_bytes(tmp_path, db, sponsors):
    a = site.build(db, tmp_path / "a", sponsors_dir=sponsors, base_url=BASE)
    b = site.build(db, tmp_path / "b", sponsors_dir=sponsors, base_url=BASE)
    assert a["files"] == b["files"]


def test_a_rebuild_replaces_the_previous_build_as_a_whole(tmp_path, db, sponsors):
    out = tmp_path / "site"
    site.build(db, out, sponsors_dir=sponsors, base_url=BASE)
    (out / "c" / "stale.html").write_text("old", encoding="utf-8")
    site.build(db, out, sponsors_dir=sponsors, base_url=BASE)
    assert not (out / "c" / "stale.html").exists()
    assert not (tmp_path / "site.new").exists() and not (tmp_path / "site.old").exists()


def test_a_folder_that_is_not_a_previous_build_is_never_replaced(tmp_path, db):
    out = tmp_path / "precious"
    out.mkdir()
    (out / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(site.SiteError, match="not a previous build"):
        site.build(db, out, base_url=BASE)
    assert (out / "keep.txt").read_text(encoding="utf-8") == "mine"


@pytest.mark.parametrize("bad", ["http://verify.example.test", "https://verify.example.test/path", "verify.example.test", "https://x y"])
def test_the_base_url_must_be_a_plain_https_origin(tmp_path, db, bad):
    with pytest.raises(site.SiteError):
        site.build(db, tmp_path / "s", base_url=bad)


@pytest.mark.parametrize("name, data, msg", [
    ("bad.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "plain drawing"),
    ("bad.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://x/y.png"/></svg>', "plain drawing"),
    ("bad.svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"></svg>', "plain drawing"),
    ("bad.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(https://x/y)"/></svg>', "plain drawing"),
    ("fake.png", b"<html>not a png</html>", "not really"),
    ("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (310 * 1024), "KB"),
    ("logo.gif", b"GIF89a", "PNG, SVG"),
], ids=["svg-script", "svg-image", "svg-handler", "svg-outside-url", "fake-png", "too-big", "gif"])
def test_a_bad_logo_is_refused(tmp_path, db, name, data, msg):
    d = tmp_path / "sp"
    d.mkdir()
    (d / name).write_bytes(data)
    (d / "sponsors.json").write_text(json.dumps([{"name": "X", "file": name}]), encoding="utf-8")
    with pytest.raises(site.SiteError, match=msg):
        site.build(db, tmp_path / "s", sponsors_dir=d, base_url=BASE)


@pytest.mark.parametrize("listing", ['[{"name": "X", "file": "../c.sqlite"}]', '[{"name": "X", "file": "missing.png"}]', '[{"file": "a.png"}]', '{"name": "X"}'])
def test_a_bad_sponsor_list_is_refused(tmp_path, db, listing):
    d = tmp_path / "sp"
    d.mkdir()
    (d / "sponsors.json").write_text(listing, encoding="utf-8")
    with pytest.raises(site.SiteError):
        site.build(db, tmp_path / "s", sponsors_dir=d, base_url=BASE)


def test_robots_allow_only_the_lookup_page_and_certificates_say_noindex(built):
    out, _, rows = built
    assert (out / "robots.txt").read_text(encoding="utf-8") == "User-agent: *\nAllow: /$\nDisallow: /\n"
    code = next(iter(rows))
    assert '<meta name="robots" content="noindex, nofollow">' in page(out, code)
    assert "noindex" not in (out / "index.html").read_text(encoding="utf-8")


def test_the_build_summary_holds_no_personal_data(built):
    out, _, rows = built
    summary = (out / "build.json").read_text(encoding="utf-8")
    for c in rows.values():
        for value in (c.name, c.username, c.studio_name):
            if value:
                assert value not in summary


# ------------------------------------------------------------------ the page's script agrees with the tool

CASES = ["L3M0N26-PAR-BKXQT", "  l3m0n26-par-bkxqt ", "L3M0N26PARBKXQT", "l3m0n26 ach zmpld", "L3MON26-APP-QNWSC",
         "https://verify.l3m0nctf.xyz/L3M0N26-ORG-HRVTQ", "verify.l3m0nctf.xyz/l3m0n26-org-hrvtq/", "L3M0N26_PAR_BKXQT", "", "hello",
         "L3M0N26-XYZ-BKXQT", "L3M0N26-PAR-BKXQA", "L3M0N26-PAR-BKXQ", "L3M0N25-PAR-BKXQT", "L3M0N26-PAR-BKX0T", "x" * 300]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_the_pages_script_reads_codes_exactly_like_the_tool():
    js = (Path(site.HERE) / "site.js").read_text(encoding="utf-8")
    harness = ("var window={},document={documentElement:{classList:{add:function(){}}},readyState:'complete',getElementById:function(){return null},"
               "body:{classList:{contains:function(){return false}}}};" + js +
               ";process.stdout.write(JSON.stringify(" + json.dumps(CASES) + ".map(function(c){return window.l3monNormalize(c)})))")
    res = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout) == [codes.normalize(c) for c in CASES]


# ------------------------------------------------------------------ review findings of 26 Nov 2026

@pytest.mark.parametrize("svg", [
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:s="http://www.w3.org/2000/svg"><s:script>alert(1)</s:script></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:s="http://www.w3.org/2000/svg"><s:a href="#x"><s:set attributeName="href" to="https://evil.example/"/></s:a></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><style>@import "https://evil.example/x.css";</style></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><style>rect{fill:url(https://evil.example/p)}</style><rect/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="http://www.w3.org/1999/xlink"><use x:href="https://evil.example/a.svg#b"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><animate attributeName="x" to="1"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><rect ONLOAD="x()"/></svg>',
    '<html xmlns="http://www.w3.org/1999/xhtml"></html>',
    '<svg xmlns="http://www.w3.org/2000/svg"><rect',
    r'<svg xmlns="http://www.w3.org/2000/svg"><style>@im\port "https://e/x.css";</style></svg>',
    r'<svg xmlns="http://www.w3.org/2000/svg"><style>rect{fill:u\rl(https://e/x)}</style><rect/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><style>rect{fill:image-set("https://e/x" 1x)}</style><rect/></svg>',
    '<?xml-stylesheet href="https://e/x.css"?><svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><style>@font-face{font-family:x}</style></svg>',
], ids=["prefixed-script", "prefixed-a-set", "style-import", "style-outside-url", "xlink-outside", "animate", "foreignobject", "upper-handler", "not-svg", "broken", "escaped-import", "escaped-url", "image-set", "xml-stylesheet-pi", "font-face"])
def test_svg_logos_pass_only_an_allow_list(tmp_path, db, svg):
    d = tmp_path / "sp"
    d.mkdir()
    (d / "x.svg").write_text(svg, encoding="utf-8")
    (d / "sponsors.json").write_text(json.dumps([{"name": "X", "file": "x.svg"}]), encoding="utf-8")
    with pytest.raises(site.SiteError):
        site.build(db, tmp_path / "s", sponsors_dir=d, base_url=BASE)


def test_a_plain_svg_logo_with_its_own_style_and_title_is_accepted(tmp_path, db):
    d = tmp_path / "sp"
    d.mkdir()
    (d / "x.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 298 173"><defs><style>.a{fill:#491d55;}</style>'
                             '<linearGradient id="g"><stop offset="0"/></linearGradient></defs><title>logo</title>'
                             '<path class="a" d="M0 0h10v10z" fill="url(#g)"/></svg>', encoding="utf-8")
    (d / "sponsors.json").write_text(json.dumps([{"name": "X", "file": "x.svg"}]), encoding="utf-8")
    site.build(db, tmp_path / "s", sponsors_dir=d, base_url=BASE)


def test_folders_next_to_the_output_are_never_touched(tmp_path, db):
    for name in ("site.new", "site.old", ".site.new", "notes"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "precious.txt").write_text("mine", encoding="utf-8")
    site.build(db, tmp_path / "site", base_url=BASE)
    site.build(db, tmp_path / "site", base_url=BASE)
    for name in ("site.new", "site.old", ".site.new", "notes"):
        assert (tmp_path / name / "precious.txt").read_text(encoding="utf-8") == "mine"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".site")) == [".site.new"], "no temporary folder is left behind"


def test_only_a_valid_certificate_says_verified_broadcast(built):
    out, _, rows = built
    for c in rows.values():
        text = page(out, c.verify_code)
        assert ("Verified broadcast" in text) == (c.status == "valid"), c.verify_code
    assert "Verified broadcast" not in (out / "404.html").read_text(encoding="utf-8")


def test_the_skip_link_and_the_print_buttons_name(built):
    out, _, rows = built
    code = next(c.verify_code for c in rows.values() if c.status == "valid")
    text = page(out, code)
    assert '<a class="skip" href="#main">Skip to the content</a>' in text and '<main id="main"' in text
    assert re.search(r'<button[^>]*id="print"[^>]*>SAVE PDF</button>', text) and 'aria-label="Save' not in text


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
@pytest.mark.parametrize("path, target", [("/L3M0N26-ACH-RXMRS/", "/L3M0N26-ACH-RXMRS"), ("/l3m0n26-ach-rxmrs", "/L3M0N26-ACH-RXMRS"),
                                          ("/L3M0N26-ACH-RXMRS", None), ("/nothing", None), ("/%E0%A4%A", None)])
def test_the_404_page_sends_other_spellings_to_the_canonical_address_once(path, target):
    js = (Path(site.HERE) / "site.js").read_text(encoding="utf-8")
    harness = ("var went=null;var window={location:{pathname:" + json.dumps(path) + ",replace:function(u){went=u},assign:function(){}}};"
               "var document={documentElement:{classList:{add:function(){}}},readyState:'complete',getElementById:function(){return null},"
               "body:{classList:{contains:function(c){return c==='st-missing'}}}};" + js + ";process.stdout.write(JSON.stringify(went))")
    res = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout) == target
