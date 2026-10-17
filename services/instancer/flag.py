import hashlib
import hmac
import re

_WRAPPER_PREFIX = "L3m0n" + "{"
_WRAPPER_SUFFIX = "}"
_FLAG_PATTERN = re.compile(re.escape("L3m0n") + r"\{[0-9a-f]{32}\}\Z")


def derive_flag(key: str | bytes, team_id: int, challenge_id: int) -> str:
    """Derive the per-team flag using HMAC-SHA256, returning the 32-hex character digest wrapped in the standard flag wrapper."""
    if isinstance(key, str):
        key_bytes = key.strip().encode("utf-8")
    else:
        key_bytes = key.strip()

    msg = f"{int(team_id)}:{int(challenge_id)}".encode("utf-8")
    digest = hmac.new(key_bytes, msg, hashlib.sha256).hexdigest()[:32].lower()
    return f"{_WRAPPER_PREFIX}{digest}{_WRAPPER_SUFFIX}"


def is_valid_flag_shape(flag: str) -> bool:
    """Check if the given string matches the exact per-team flag pattern."""
    if not isinstance(flag, str):
        return False
    return bool(_FLAG_PATTERN.fullmatch(flag))
