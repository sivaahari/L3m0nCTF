"""PDF certificates from the organisers' SVG design (tools/l3mon/verify/certificate.py and its use in site.py).

What must hold: a design is refused, with a reason, unless it can make a checkable certificate (the code, the holder, a QR place) without script
or anything from outside; every value is filled in as text (escaped, never read as a placeholder or markup); only valid codes of a category with a
design get a PDF, and their pages link to it; a rebuild prints only what changed; and a real browser prints one page, the same bytes every time.
Every name here is made up.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from l3mon import cli
from l3mon.verify import certificate, issue, site, store
from qr_reference_reader import decode as qr_read

BASE = "https://verify.example.test"
DESIGN = """<svg xmlns="http://www.w3.org/2000/svg" width="297mm" height="210mm" viewBox="0 0 1123 794">
<!-- a comment that mentions {{NOTHING}} is ignored -->
<rect width="1123" height="794" fill="#fff"/>
<text id="holder center" x="400" y="300" font-size="40">{{USERNAME}}</text>
<text x="561" y="360" text-anchor="middle" font-size="20">of <tspan font-weight="700">{{STUDIO}}</tspan></text>
<text x="60" y="760" font-size="14">{{CODE}} · {{DATE}} · {{EVENT}} · {{ROLE}}</text>
<rect id="qr" x="900" y="600" width="120" height="120"/>
</svg>
"""


def design(tmp_path: Path, text: str = DESIGN, name: str = "d.svg") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def cred(username="neo.sample", studio="Byte Bandits", code="L3M0N26-PAR-BCDFG") -> store.Credential:
    return store.Credential(verify_code=code, category="PAR", issue_date="2026-11-28", source_ref="t", username=username, studio_name=studio)


# ------------------------------------------------------------------ the design

def test_the_bundled_qualifier_design_is_a_valid_a4_landscape_design():
    t = certificate.load(certificate.BUNDLED["PAR"])
    assert (t.width, t.height, t.view_w, t.view_h) == ("297mm", "210mm", 1123, 794)


@pytest.mark.parametrize("change, msg", [
    (lambda s: s.replace('id="qr"', 'id="code-box"'), "no element with the id 'qr'"),
    (lambda s: s.replace("{{CODE}}", "code"), "must show {{CODE}}"),
    (lambda s: s.replace("{{USERNAME}}", "someone"), "and the holder"),
    (lambda s: s.replace("{{DATE}}", "{{BIRTHDAY}}"), "unknown placeholder"),
    (lambda s: s.replace("<rect width", '<script>alert(1)</script><rect width'), "<script> is not allowed"),
    (lambda s: s.replace('<rect width', '<rect onload="x()" width'), "event handler onload"),
    (lambda s: s.replace('<rect width', '<image href="https://evil.example/x.png" width="1" height="1"/><rect width'), "leaves the design"),
    (lambda s: s.replace('<rect width', '<style>@import url(https://fonts.example/f.css);</style><rect width'), "loads something from outside"),
    (lambda s: s.replace('fill="#fff"', 'fill="url(https://evil.example/p)"'), "loads something from outside"),
    (lambda s: s.replace('<rect width', '<foreignObject width="1" height="1"/><rect width'), "<foreignObject> is not allowed"),
    (lambda s: s.replace('<rect id="qr" x="900" y="600" width="120" height="120"/>', '<circle id="qr" cx="5" cy="5" r="5"/>'), "must be a rectangle"),
    (lambda s: s.replace('</svg>', '<rect id="qr" x="1" y="1" width="2" height="2"/></svg>'), "two elements have the id 'qr'"),
    (lambda s: '<!DOCTYPE svg [<!ENTITY x "y">]>' + s, "no DOCTYPE or ENTITY"),
    (lambda s: s.replace('viewBox="0 0 1123 794"', "").replace('width="297mm" height="210mm"', ""), "needs a viewBox"),
    (lambda s: s.replace("<svg ", "<html ").replace("</svg>", "</html>"), "top element must be <svg>"),
    (lambda s: s[:-20], "not a well-formed SVG"),
])
def test_a_design_that_cannot_make_a_safe_checkable_certificate_is_refused_with_the_reason(tmp_path, change, msg):
    with pytest.raises(certificate.CertificateError, match=re.escape(msg)):
        certificate.load(design(tmp_path, change(DESIGN)))


def test_outlined_text_gets_a_hint(tmp_path):
    outlined = re.sub(r"<text.*?</text>", '<path d="M0 0h1v1z"/>', DESIGN, flags=re.S)
    with pytest.raises(certificate.CertificateError, match="Outline text"):
        certificate.load(design(tmp_path, outlined))


def test_pictures_and_fonts_embedded_as_data_addresses_are_fine(tmp_path):
    embedded = DESIGN.replace('<rect width', '<style>@font-face{font-family:X;src:url(data:font/woff2;base64,AAAA)}</style>'
                                             '<image href="data:image/png;base64,iVBORw0KGgo=" width="1" height="1"/><rect width')
    certificate.load(design(tmp_path, embedded))


# ------------------------------------------------------------------ filling it in

def values(c=None):
    return certificate.fields(c or cred(), BASE, site.human_date)


def test_every_value_is_filled_in_as_escaped_text_and_never_read_as_a_placeholder(tmp_path):
    t = certificate.load(design(tmp_path))
    hostile = cred(username='{{CODE}}<script>alert(1)</script>&"x"', studio="</text><svg onload=alert(1)>")
    out = certificate.fill(t, values(hostile))
    assert "<script>" not in out and "<svg onload" not in out
    assert "{{CODE}}&lt;script&gt;alert(1)&lt;/script&gt;&amp;&quot;x&quot;" in out, "a username that looks like a placeholder stays as typed"
    assert "&lt;/text&gt;&lt;svg onload=alert(1)&gt;" in out
    assert "L3M0N26-PAR-BCDFG · 28 November 2026 · L3m0n CTF 2026 Qualifiers · Participant" in out
    # outside the kept originals (braces as character references) no placeholder is left
    assert "{{" not in re.sub(r'data-l3orig="[^"]*"', "", out).replace("{{CODE}}&lt;", "")


def test_the_fit_script_gets_the_placeholder_markup_and_the_alignment_from_the_layer_name(tmp_path):
    out = certificate.fill(certificate.load(design(tmp_path)), values())
    holder = re.search(r'<text id="holder center"[^>]*>', out).group(0)
    assert 'data-l3fit="center"' in holder and 'data-l3orig="&#123;&#123;USERNAME&#125;&#125;"' in holder
    studio = re.search(r'<text x="561"[^>]*>', out).group(0)
    assert 'data-l3fit="left"' in studio and "&lt;tspan font-weight=&quot;700&quot;&gt;&#123;&#123;STUDIO&#125;&#125;&lt;/tspan&gt;" in studio


@pytest.mark.parametrize("name, align", [("username center", "center"), ("Studio_Centre", "center"), ("code-middle", "center"),
                                         ("code right", "right"), ("date_end", "right"), ("legend", "left"), ("username", "left")])
def test_the_alignment_hint(name, align):
    assert certificate._align(f' id="{name}"') == align


def test_the_qr_code_takes_the_rectangles_place_and_reads_back_as_the_codes_address(tmp_path):
    out = certificate.fill(certificate.load(design(tmp_path)), values())
    assert 'id="qr"' not in out
    m = re.search(r'<svg x="900" y="600" width="120" height="120" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 (\d+) \1"[^>]*>.*?<path d="([^"]+)"', out)
    assert m
    n = int(m.group(1)) - 8
    grid = [["0"] * n for _ in range(n)]
    for x, y in re.findall(r"M(\d+) (\d+)h1v1h-1z", m.group(2)):
        grid[int(y) - 4][int(x) - 4] = "1"
    res = qr_read(["".join(r) for r in grid])
    assert res["format_ok"] and res["rs_ok"] and res["text"] == f"{BASE}/L3M0N26-PAR-BCDFG"


def test_the_print_page_sizes_the_paper_from_the_design_and_names_the_pdf(tmp_path):
    t = certificate.load(design(tmp_path))
    page = certificate.print_page(t, certificate.fill(t, values()), "Certificate <x> · L3M0N26-PAR-BCDFG")
    assert "@page{size:297mm 210mm;margin:0}" in page and "<title>Certificate &lt;x&gt; · L3M0N26-PAR-BCDFG</title>" in page


def test_the_reuse_key_changes_with_the_design_or_a_value(tmp_path):
    t = certificate.load(design(tmp_path))
    t2 = certificate.load(design(tmp_path, DESIGN.replace("#fff", "#ffe"), "d2.svg"))
    k = certificate.key(t, values())
    assert k == certificate.key(t, values())
    assert k != certificate.key(t2, values()) and k != certificate.key(t, values(cred(studio="Other")))


def test_the_timestamps_become_the_issue_date_without_moving_a_byte():
    pdf = b"%PDF-1.4\n<</CreationDate (D:20261128085826+00'00') /ModDate (D:20261128085826+00'00')>>\nxref"
    out = certificate._stamp(pdf, "2026-11-28")
    assert len(out) == len(pdf) and out.count(b"(D:20261128000000+00'00')") == 2


def test_a_missing_browser_is_named(monkeypatch, tmp_path):
    monkeypatch.setenv("L3MON_CHROME", str(tmp_path / "nope.exe"))
    with pytest.raises(certificate.CertificateError, match="L3MON_CHROME"):
        certificate.find_browser()


# ------------------------------------------------------------------ in the build

@pytest.fixture
def db(tmp_path):
    path = tmp_path / "c.sqlite"
    conn = store.connect(path, create=True)
    p = tmp_path / "p.csv"
    p.write_text("user_id,username,studio\n11,ctf.username,Test\n12,neo.sample,Test\n13,trinity_x,Byte Bandits\n14,gone.player,Byte Bandits\n",
                 encoding="utf-8")
    issue.issue(conn, issue_date="2026-11-28", org=["Jane Doe"], prelims=issue.read_prelims(p))
    f = tmp_path / "f.csv"
    f.write_text("username,studio,rank,user_id\nctf.username,Test,1,11\n", encoding="utf-8")
    issue.issue(conn, issue_date="2026-12-12", finals=issue.read_finals(f))
    gone = next(c for c in store.all_rows(conn) if c.username == "gone.player")
    store.withdraw(conn, gone.verify_code, "made-up reason")
    rows = {c.verify_code: c for c in store.all_rows(conn)}
    conn.close()
    return path, rows


@pytest.fixture
def printer(monkeypatch):
    """Stands in for the browser: records what it is asked to print and returns a small fake PDF naming the code."""
    calls = []

    def fake(jobs, *, browser=None, workers=None):
        calls.append(sorted(jobs))
        return {code: f"%PDF-fake {code} {iso}".encode() for code, (_html, iso) in jobs.items()}
    monkeypatch.setattr(certificate, "render", fake)
    return calls


def test_only_valid_qualifier_codes_get_a_pdf_and_their_pages_link_to_it(tmp_path, db, printer):
    path, rows = db
    out = tmp_path / "site"
    summary = site.build(path, out, base_url=BASE, certificates={"PAR": certificate.load(certificate.BUNDLED["PAR"])})
    want = sorted(c.verify_code for c in rows.values() if c.category == "PAR" and c.status == "valid")
    assert len(want) == 2, "neo.sample and trinity_x: ctf.username's code was replaced by the finals one, gone.player's withdrawn"
    assert sorted(p.stem for p in (out / "c").glob("*.pdf")) == want
    assert printer == [want]
    assert summary["counts"]["pdf"] == 2 and sorted(summary["certificates"]) == want
    for code, c in rows.items():
        html_text = (out / "c" / f"{code}.html").read_text(encoding="utf-8")
        link = f'href="/{code}/certificate.pdf"'
        if code in want:
            assert html_text.count(link) == 2 and "DOWNLOAD PDF" in html_text and 'id="print"' not in html_text
        else:
            assert "certificate.pdf" not in html_text
            assert ('id="print"' in html_text) == (c.status == "valid"), "other valid certificates keep the print button"


def test_a_rebuild_prints_only_what_changed(tmp_path, db, printer):
    path, rows = db
    out = tmp_path / "site"
    par = {"PAR": certificate.load(certificate.BUNDLED["PAR"])}
    site.build(path, out, base_url=BASE, certificates=par)
    site.build(path, out, base_url=BASE, certificates=par)
    assert printer[1] == [], "nothing changed: every PDF is reused"
    one = sorted(p.stem for p in (out / "c").glob("*.pdf"))[0]
    (out / "c" / f"{one}.pdf").write_bytes(b"%PDF-tampered")
    site.build(path, out, base_url=BASE, certificates=par)
    assert printer[2] == [one], "a PDF whose bytes differ from the recorded ones is printed again"
    site.build(path, out, base_url=BASE, certificates={"PAR": certificate.load(design(tmp_path))})
    assert len(printer[3]) == 2, "a new design prints them all"
    site.build(path, out, base_url=BASE)
    assert not list((out / "c").glob("*.pdf")) and "certificates" not in json.loads((out / "build.json").read_text(encoding="utf-8"))


def test_an_unknown_category_is_refused(tmp_path, db, printer):
    with pytest.raises(site.SiteError, match="unknown category"):
        site.build(db[0], tmp_path / "site", base_url=BASE, certificates={"XYZ": certificate.load(certificate.BUNDLED["PAR"])})


def test_the_command_line(tmp_path, db, printer, capsys):
    path, _ = db
    out = tmp_path / "site"
    assert cli.main(["verify", "build", str(path), str(out), "--base-url", BASE, "--certificate", "par"]) == 0
    assert "2 PDF certificate(s)" in capsys.readouterr().out
    assert cli.main(["verify", "build", str(path), str(out), "--certificate", "ORG"]) == 1
    assert "needs a design" in capsys.readouterr().out
    assert cli.main(["verify", "build", str(path), str(out), "--certificate", "PAR", "--certificate", f"PAR={design(tmp_path)}"]) == 1
    assert "given twice" in capsys.readouterr().out
    assert cli.main(["verify", "build", str(path), str(out), "--certificate", f"PAR={tmp_path / 'missing.svg'}"]) == 1
    assert "no such certificate design" in capsys.readouterr().out


# ------------------------------------------------------------------ a real browser

def browser_or_skip() -> str:
    try:
        return certificate.find_browser()
    except certificate.CertificateError:
        pytest.skip("needs Chrome, Chromium or Edge")


def test_a_real_browser_prints_one_page_with_the_same_bytes_every_time(tmp_path):
    browser = browser_or_skip()
    t = certificate.load(certificate.BUNDLED["PAR"])
    long_name = cred(username="a_really_extremely_long_username_" * 3, studio="A studio with a very long name indeed " * 2)
    jobs = {"a": (certificate.print_page(t, certificate.fill(t, values())), "2026-11-28"),
            "b": (certificate.print_page(t, certificate.fill(t, values(long_name))), "2026-11-28")}
    first = certificate.render(jobs, browser=browser)
    again = certificate.render({"a": jobs["a"]}, browser=browser)
    for pdf in first.values():
        assert pdf.startswith(b"%PDF") and re.search(rb"/Count 1\b", pdf), "one page, however long the name"
        assert b"(D:20261128000000+00'00')" in pdf
    assert again["a"] == first["a"], "the same input gives the same bytes"


def test_the_fit_script_waits_for_the_designs_own_fonts_before_it_measures(tmp_path):
    """Found on 28 Nov 2026 with the supervisor's design (fonts embedded as data: addresses, as the runbook tells designers to): the script measured at once, with a
    stand-in font of other widths, so a long name looked as if it fitted and was printed at full size into the QR code. It must load every font first."""
    t = certificate.load(certificate.BUNDLED["PAR"])
    script = certificate.print_page(t, certificate.fill(t, values())).split("<script>")[-1]
    assert "document.fonts" in script, "the print page never waits for the fonts"
    assert script.index("document.fonts") < script.index("getBBox"), "the fonts are awaited before the first measurement"
    assert ".load()" in script and ".then(" in script


# ------------------------------------------------------------------ the colours of the QR code (28 Nov 2026: the supervisor's design wants black on white)

QR_RECT = '<rect id="qr" x="900" y="600" width="120" height="120"/>'


def test_the_qr_rectangle_can_name_the_colours_of_the_code(tmp_path):
    text = DESIGN.replace(QR_RECT, '<rect id="qr" x="900" y="600" width="120" height="120" data-dark="#000000" data-light="#ffffff"/>')
    out = certificate.fill(certificate.load(design(tmp_path, text)), values())
    assert 'fill="#ffffff"' in out and 'fill="#000000"' in out
    assert "#070b1e" not in out and "#f4ead4" not in out


def test_without_colours_the_qr_keeps_the_portals_own(tmp_path):
    out = certificate.fill(certificate.load(design(tmp_path)), values())
    assert 'fill="#f4ead4"' in out and 'fill="#070b1e"' in out


@pytest.mark.parametrize("bad", ["red", "#fff", "#12345g", "#00000000", "rgb(0,0,0)", "url(https://evil.example/x)"])
def test_a_qr_colour_that_is_not_six_digit_hex_is_refused(tmp_path, bad):
    text = DESIGN.replace(QR_RECT, f'<rect id="qr" x="900" y="600" width="120" height="120" data-dark="{bad}"/>')
    with pytest.raises(certificate.CertificateError, match="data-dark|loads something from outside"):
        certificate.load(design(tmp_path, text))


def test_a_qr_with_chosen_colours_still_reads_back_as_the_codes_address(tmp_path):
    text = DESIGN.replace(QR_RECT, '<rect id="qr" x="900" y="600" width="120" height="120" data-dark="#000000" data-light="#ffffff"/>')
    out = certificate.fill(certificate.load(design(tmp_path, text)), values())
    m = re.search(r'<svg x="900" y="600" width="120" height="120" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 (\d+) \1"[^>]*>.*?<path d="([^"]+)"', out)
    n = int(m.group(1)) - 8
    grid = [["0"] * n for _ in range(n)]
    for x, y in re.findall(r"M(\d+) (\d+)h1v1h-1z", m.group(2)):
        grid[int(y) - 4][int(x) - 4] = "1"
    res = qr_read(["".join(r) for r in grid])
    assert res["format_ok"] and res["rs_ok"] and res["text"] == f"{BASE}/L3M0N26-PAR-BCDFG"
