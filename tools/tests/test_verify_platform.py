"""The platform's certificate list (tools/l3mon/verify/platform.py) and the platform plugin's copies of the portal's rules.

The plugin (plugins/l3mon_certificates) runs inside the platform image, where tools/ is not present, so it carries its own copy of the code rules
and of the QR encoder. These tests keep the copies equal to the laptop's, so a code the laptop issues is always a code the platform accepts and
the QR code on the studio page is the same symbol as on the portal. Every name here is made up.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from l3mon.verify import codes, issue, platform, qr, store
from l3mon.verify.command import run as run_verify
from l3mon.cli import build_parser

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "l3mon_certificates"
PRELIMS = "user_id,username,studio,prelims_rank\n11,ctf.username,Test,1\n12,neo.sample,Test,1\n13,trinity_x,Byte Bandits,2\n"
FINALS = "username,studio,rank,user_id\nneo.sample,Test,1,12\n"


def _plugin_module(name):
    spec = importlib.util.spec_from_file_location(f"plugin_{name}", PLUGIN / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "c.sqlite"
    conn = store.connect(path, create=True)
    (tmp_path / "p.csv").write_text(PRELIMS, encoding="utf-8")
    (tmp_path / "org.txt").write_text("Jane Example\n", encoding="utf-8")
    issue.issue(conn, issue_date="2026-11-30", org=issue.read_org(tmp_path / "org.txt"), prelims=issue.read_prelims(tmp_path / "p.csv"))
    conn.close()
    return path


def test_the_list_holds_the_valid_qualifier_codes_by_player_id_only(db):
    conn = store.connect(db)
    data = platform.platform_list(conn, "https://verify.l3m0nctf.xyz")
    par = {c.verify_code: c for c in store.all_rows(conn) if c.category == "PAR"}
    conn.close()
    assert data["format"] == "l3mon-certificates-1" and data["base_url"] == "https://verify.l3m0nctf.xyz"
    assert [(e["user_id"], e["username"]) for e in data["codes"]] == [(11, "ctf.username"), (12, "neo.sample"), (13, "trinity_x")]
    assert {e["code"] for e in data["codes"]} == set(par), "no organiser code, only PAR"


def test_a_withdrawn_code_and_a_finalists_replaced_code_leave_the_list(db, tmp_path):
    conn = store.connect(db)
    first = next(c for c in store.all_rows(conn) if c.category == "PAR" and c.username == "trinity_x")
    store.withdraw(conn, first.verify_code, "made-up reason")
    (tmp_path / "f.csv").write_text(FINALS, encoding="utf-8")
    issue.issue(conn, issue_date="2026-12-12", finals=issue.read_finals(tmp_path / "f.csv"))
    data = platform.platform_list(conn, "https://verify.l3m0nctf.xyz")
    conn.close()
    assert [e["user_id"] for e in data["codes"]] == [11], "13 was withdrawn, 12 is a finalist now (ACH, printed)"


@pytest.mark.parametrize("url", ["http://verify.l3m0nctf.xyz", "https://verify.l3m0nctf.xyz/", "https://verify.l3m0nctf.xyz/x", "https://a@b.xyz",
                                 "https://verify.l3m0nctf.xyz:8443", "https://localhost", "https://Verify.l3m0nctf.xyz", ""])
def test_the_address_must_be_the_portals_https_host(db, url):
    conn = store.connect(db)
    with pytest.raises(platform.PlatformError):
        platform.platform_list(conn, url)
    conn.close()


def test_the_command_writes_utf8_json(db, tmp_path, capsys):
    out = tmp_path / "out" / "list.json"
    args = build_parser().parse_args(["verify", "export-platform", str(db), str(out)])
    assert run_verify(args) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["codes"]) == 3 and "private" in capsys.readouterr().out.lower()


# ---- the plugin's copies ---------------------------------------------------------------------------------------------------------------

def test_the_plugins_code_rules_are_the_laptops():
    pc = _plugin_module("codes")
    assert (pc.PREFIX, pc.ALPHABET, pc.SUFFIX_LEN) == (codes.PREFIX, codes.ALPHABET, codes.SUFFIX_LEN)
    assert pc.CATEGORY in codes.CATEGORIES
    assert pc.BASE_URL_RE.pattern == platform.BASE_URL_RE.pattern
    sample = codes.make("PAR", "BCDFG")
    assert pc.CODE_RE.match(sample) and codes.CODE_RE.match(sample)
    for other in ("ORG", "ACH", "APP"):
        assert not pc.CODE_RE.match(codes.make(other, "BCDFG")), "only qualifier codes reach the platform"
    assert not pc.CODE_RE.match(sample + "\n") and not pc.CODE_RE.match("l3m0n26-par-bcdfg")


def test_the_plugins_qr_encoder_is_a_byte_for_byte_copy():
    laptop = (ROOT / "tools" / "l3mon" / "verify" / "qr.py").read_bytes().replace(b"\r\n", b"\n")
    plugin = (PLUGIN / "qr.py").read_bytes().replace(b"\r\n", b"\n")
    assert plugin == laptop, "copy tools/l3mon/verify/qr.py to plugins/l3mon_certificates/qr.py again"
    assert _plugin_module("qr").svg("https://verify.l3m0nctf.xyz/L3M0N26-PAR-BCDFG") == qr.svg("https://verify.l3m0nctf.xyz/L3M0N26-PAR-BCDFG")


def test_the_plugins_loader_reads_the_format_the_laptop_writes():
    text = (PLUGIN / "loader.py").read_text(encoding="utf-8")
    assert re.search(r'^FORMAT = "([^"]+)"', text, re.M).group(1) == platform.FORMAT
