"""The reviewed list of accepted advisories must match the page that explains them."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ACCEPTED = ROOT / "docker" / "ctfd" / "accepted-advisories.txt"
ACCEPTED_STACK = ROOT / "deploy" / "compose" / "accepted-advisories.txt"
PAGE = ROOT / "docs" / "security" / "dependency-bumps.md"
ID = re.compile(r"(?:CVE-\d{4}-\d{4,}|GHSA(?:-[0-9a-z]{4}){3}|PYSEC-\d{4}-\d+)")


def accepted(path: Path = ACCEPTED) -> list[str]:
    ids = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ids.append(line)
    return ids


def python_section() -> str:
    text = PAGE.read_text(encoding="utf-8")
    start = text.index("## The advisories that remain (Python)")
    end = text.index("## The advisories that remain (Debian base image)")
    return text[start:end]


@pytest.mark.parametrize("path", [ACCEPTED, ACCEPTED_STACK], ids=["platform image", "third-party images"])
def test_every_line_is_one_plain_identifier_and_none_repeats(path):
    ids = accepted(path)
    assert ids, "the list must not be empty"
    for one in ids:
        assert ID.fullmatch(one), f"not a bare advisory identifier: {one!r}"
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("path", [ACCEPTED, ACCEPTED_STACK], ids=["platform image", "third-party images"])
def test_every_accepted_advisory_is_explained_on_the_page(path):
    page = PAGE.read_text(encoding="utf-8")
    missing = [one for one in accepted(path) if one not in page]
    assert not missing, f"accepted but not explained in docs/security/dependency-bumps.md: {missing}"


def test_the_page_lists_no_python_advisory_that_is_not_accepted():
    # the other direction: an advisory described as "remaining" must be on the list CI uses, or CI would fail on it
    in_page = set(ID.findall(python_section()))
    assert in_page, "the page's Python table was not found"
    extra = sorted(in_page - set(accepted()))
    assert not extra, f"described on the page but missing from accepted-advisories.txt: {extra}"
