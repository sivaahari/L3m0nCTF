"""What a code on the platform may look like: a qualifier participant's portal code, `L3M0N26-PAR-<5 of 21 consonants>`.

The same prefix and alphabet as the laptop tool (tools/l3mon/verify/codes.py; tools/tests/test_verify_platform.py keeps the two equal). Only
PAR codes ever reach the platform: organisers and finalists get theirs on paper.
"""
import re

PREFIX = "L3M0N26"
CATEGORY = "PAR"
ALPHABET = "BCDFGHJKLMNPQRSTVWXYZ"
SUFFIX_LEN = 5

CODE_RE = re.compile(rf"\A{PREFIX}-{CATEGORY}-[{ALPHABET}]{{{SUFFIX_LEN}}}\Z")
# the portal's own address: https, a host name only (no path, port, user or query), because the code is appended to it
BASE_URL_RE = re.compile(r"\Ahttps://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z")
DEFAULT_BASE_URL = "https://verify.l3m0nctf.xyz"
