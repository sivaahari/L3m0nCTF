"""Password policy validation for L3m0nCTF 2026 (SP5 / Stream C).

Faithful port of prototype's vPassword, vWeakPattern (src/js/validate.js)
and isCommonPassword (server/validate.mjs).
"""
from __future__ import annotations

import math
import re

V_LIMITS_PASS_MIN = 12
V_LIMITS_PASS_MAX = 128

V_MSG_PASS_EMPTY = "Choose a password."
V_MSG_PASS_SHORT = "Use at least 12 characters."
V_MSG_PASS_LONG = "Use at most 128 characters."
V_MSG_PASS_COMMON = "That password is too common. Try a short sentence."
V_MSG_PASS_USER = "Do not use your username in your password."
V_MSG_PASS_EMAIL = "Do not use your email address in your password."

COMMON_WORDS = set(
    "password pass word qwerty letmein welcome admin login hello iloveyou love secret monkey dragon master "
    "shadow sunshine princess football baseball soccer hockey summer winter freedom trustno test tester changeme default guest "
    "user root lemon lemons ctf hacker hack flag abc abcd qwe asd zxc super star batman superman pokemon computer internet "
    "whatever nothing access mypass".split()
)

LEET = {
    "0": "o",
    "1": "i",
    "3": "e",
    "4": "a",
    "5": "s",
    "7": "t",
    "@": "a",
    "$": "s",
    "!": "i",
}

_RAW_RUNS = [
    "abcdefghijklmnopqrstuvwxyz",
    "0123456789",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
    "qwertyuiopasdfghjklzxcvbnm",
    "1qaz2wsx3edc4rfv5tgb6yhn7ujm8ik9ol0p",
    "qazwsxedcrfvtgbyhnujmikolp",
]

V_RUNS: list[str] = []
for r in _RAW_RUNS:
    V_RUNS.append(r + r)
    rev = "".join(reversed(r))
    V_RUNS.append(rev * 2)


def v_weak_pattern(pw: str) -> bool:
    """Obvious patterns: few distinct characters, repeated short blocks, or keyboard/counting runs."""
    s = re.sub(r"[\s\W_]+", "", str(pw).lower(), flags=re.ASCII)
    raw_s = str(pw)
    if len(s) < 6:
        return len(raw_s) > 0 and len(set(raw_s)) <= 3
    if len(set(s)) <= 3:
        return True
    for n in range(1, 7):
        if len(s) > n:
            reps = math.ceil(len(s) / n)
            if s == (s[:n] * reps)[:len(s)]:
                return True
    if len(s) >= 8:
        for run_seq in V_RUNS:
            if s in run_seq:
                return True
    return False


def common_core(pw: str) -> str:
    """Trim leading/trailing digits & symbols, replace leet speak, retain a-z only."""
    trimmed = re.sub(r"^[\d\W_]+|[\d\W_]+$", "", str(pw).lower(), flags=re.ASCII)
    leet_mapped = "".join(LEET.get(c, c) for c in trimmed)
    return re.sub(r"[^a-z]", "", leet_mapped)


def is_common_password(pw: str) -> bool:
    """True for passwords made of common words or repeats/combinations thereof."""
    core = common_core(pw)
    if not core:
        return False
    if core in COMMON_WORDS:
        return True
    for i in range(3, len(core) - 2):
        a = core[:i]
        b = core[i:]
        if a in COMMON_WORDS and (b in COMMON_WORDS or b == a):
            return True
    for w in COMMON_WORDS:
        if len(w) >= 3 and len(core) % len(w) == 0:
            if core == w * (len(core) // len(w)):
                return True
    return False


def validate_password(password: str | None, *, username: str = "", email: str = "") -> tuple[bool, str | None]:
    """Validate password according to length, context (username, email), patterns, and common words.

    Returns:
        (True, None) if password is acceptable.
        (False, error_message) if password violates policy.
    """
    p = "" if password is None else str(password)
    if not p:
        return False, V_MSG_PASS_EMPTY

    # Character count (codepoints)
    n = len(p)
    if n < V_LIMITS_PASS_MIN:
        return False, V_MSG_PASS_SHORT
    if n > V_LIMITS_PASS_MAX:
        return False, V_MSG_PASS_LONG

    low = p.lower()
    u = str(username).strip().lower()
    if len(u) >= 3 and u in low:
        return False, V_MSG_PASS_USER

    local = str(email).split("@")[0].strip().lower()
    if len(local) >= 3 and local in low:
        return False, V_MSG_PASS_EMAIL

    if v_weak_pattern(p):
        return False, V_MSG_PASS_COMMON

    if is_common_password(p):
        return False, V_MSG_PASS_COMMON

    return True, None
