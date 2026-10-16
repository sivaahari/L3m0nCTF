"""The load probe (tools/load_probe.py): the parts that can be tested without a stack. It makes players, challenges and a plan and replaces the plan that is
there, so the thing that matters most is that it will not talk to a platform that is not a throwaway one."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import load_probe  # noqa: E402


@pytest.mark.parametrize("base", [
    "http://ctfd:8000", "http://localhost:8080", "http://127.0.0.1:8000", "http://10.1.2.3:8000", "http://192.168.1.20", "http://172.20.0.5:8000", "http://[::1]:8000",
])
def test_a_throwaway_stack_is_accepted(base):
    assert load_probe.check_target(base) == base


@pytest.mark.parametrize("base", [
    "https://play.l3m0nctf.xyz", "http://play.l3m0nctf.xyz", "http://ctfd.example.org:8000", "http://8.8.8.8:8000", "http://1.1.1.1","https://ctfd:8000",
    "ctfd:8000", "", "http://", "http://ctfd.evil.example", "http://localhost.evil.example", "ftp://localhost",
])
def test_anything_else_is_refused(base):
    with pytest.raises(SystemExit):
        load_probe.check_target(base)


def test_the_address_is_judged_by_its_host_and_not_by_what_comes_before_it():
    for base in ("http://ctfd@play.l3m0nctf.xyz", "http://localhost:80@play.l3m0nctf.xyz", "http://play.l3m0nctf.xyz/ctfd", "http://play.l3m0nctf.xyz#ctfd"):
        with pytest.raises(SystemExit):
            load_probe.check_target(base)


def test_the_probe_never_changes_the_event_window_itself():
    """The window is the run script's job, on a stack it restarts; a probe that wrote the settings of whatever it was pointed at could not be made safe."""
    source = (ROOT / "tools" / "load_probe.py").read_text(encoding="utf-8")
    assert '"/api/v1/configs"' not in source


def test_the_report_gives_percentiles_and_counts_a_bad_answer_once():
    numbers = load_probe.Numbers()
    for n in range(100):
        numbers.add("ticks", 200, (n + 1) / 1000)
    numbers.add("ticks", 500, 0.5, body=b'{"error": "x"}')
    report = numbers.report()["ticks"]
    assert report["n"] == 101 and report["errors"] == 1 and report["statuses"] == {200: 100, 500: 1}
    assert 49 <= report["p50_ms"] <= 52 and report["p99_ms"] >= 99 and report["max_ms"] == 500.0
    assert numbers.samples == {("ticks", 500): '{"error": "x"}'}


# ---- it replaces the plan, so it must only ever touch what it made ----------------------------------------------------------------------------------

@pytest.mark.parametrize("prefix", ["zzprobe", "probe1", "abcd", "lpbench99"])
def test_a_prefix_of_letters_and_digits_that_nothing_real_starts_with_is_accepted(prefix):
    assert load_probe.check_prefix(prefix) == prefix


@pytest.mark.parametrize("prefix", ["", "lp", "abc", "Admin", "admin", "user", "test", "demo", "street", "snack", "has-dash", "1abcd", "abcdefghijklm", "ab cd", "abcd" + chr(10)])
def test_any_other_prefix_is_refused(prefix):
    with pytest.raises(SystemExit):
        load_probe.check_prefix(prefix)


VIEW = [
    {"id": 1, "slug": "street", "programmes": [{"id": 10}, {"id": 11}]},
    {"id": 2, "slug": "zzprobe-street", "programmes": [{"id": 20}, {"id": 21}]},
    {"id": 3, "slug": "zzprobe-snack", "programmes": []},
    {"id": 4, "slug": "zzprobe", "programmes": [{"id": 40}]},
    {"id": 5, "slug": "zzprobes-x", "programmes": [{"id": 50}]},
]


def test_the_channels_it_did_not_make_are_found_by_the_dash_after_the_prefix():
    assert load_probe.foreign_channels(VIEW, "zzprobe") == ["street", "zzprobe", "zzprobes-x"]
    assert load_probe.foreign_channels([], "zzprobe") == []


def test_it_releases_its_own_channels_and_their_programmes_and_nothing_else():
    assert load_probe.release_changes(VIEW, "zzprobe") == [
        {"kind": "channel", "id": 2, "mode": "release"}, {"kind": "programme", "id": 20, "mode": "release"}, {"kind": "programme", "id": 21, "mode": "release"},
        {"kind": "channel", "id": 3, "mode": "release"},
    ]


class FakeAdmin:
    def __init__(self, channels, accounts, own=0, status=200):
        self.channels, self.accounts, self.own, self.status = channels, accounts, own, status

    def json(self, method, path, body=None):
        if "admin/release" in path:
            return self.status, {"data": {"channels": self.channels}}
        if "q=" in path:
            return 200, {"meta": {"pagination": {"total": self.own}}}
        return 200, {"meta": {"pagination": {"total": self.accounts}}}


class Args:
    prefix = "zzprobe"
    max_other_accounts = 25


def test_the_preflight_passes_on_an_empty_throwaway_stack_and_on_its_own_leftovers():
    load_probe.preflight(Args, FakeAdmin([], 3), lambda *_: None)
    load_probe.preflight(Args, FakeAdmin([VIEW[1], VIEW[2]], 123, own=120), lambda *_: None)


def test_the_preflight_refuses_a_platform_with_a_channel_it_did_not_make():
    with pytest.raises(SystemExit, match="channels the probe did not make"):
        load_probe.preflight(Args, FakeAdmin(VIEW, 3), lambda *_: None)


def test_the_preflight_refuses_a_platform_with_many_accounts_it_did_not_make():
    with pytest.raises(SystemExit, match="not a throwaway"):
        load_probe.preflight(Args, FakeAdmin([], 400, own=0), lambda *_: None)
    with pytest.raises(SystemExit, match="not a throwaway"):
        load_probe.preflight(Args, FakeAdmin([], 400, own=300), lambda *_: None)  # 100 are not its own


def test_the_preflight_refuses_when_the_plan_cannot_be_read():
    with pytest.raises(SystemExit, match="could not be read"):
        load_probe.preflight(Args, FakeAdmin([], 3, status=403), lambda *_: None)
