import re
import pytest
from services.instancer.flag import derive_flag, is_valid_flag_shape

WRAPPER_PREFIX = "L3m0n" + "{"
WRAPPER_SUFFIX = "}"


def test_flag_derivation_vectors():
    key = "  test-secret-value \n"
    # Team 7, challenge 42
    flag_7_42 = derive_flag(key, team_id=7, challenge_id=42)
    assert flag_7_42 == WRAPPER_PREFIX + "94fa853e968cd402000a376fae89470d" + WRAPPER_SUFFIX
    assert is_valid_flag_shape(flag_7_42)

    # Team 8, challenge 42
    flag_8_42 = derive_flag(key, team_id=8, challenge_id=42)
    assert flag_8_42 == WRAPPER_PREFIX + "d6c753d8dea03a34b65ee9e4676ce3c3" + WRAPPER_SUFFIX
    assert is_valid_flag_shape(flag_8_42)

    # Team 7, challenge 43
    flag_7_43 = derive_flag(key, team_id=7, challenge_id=43)
    assert flag_7_43 == WRAPPER_PREFIX + "435b00a57ad252e91c15db1238e489f6" + WRAPPER_SUFFIX
    assert is_valid_flag_shape(flag_7_43)


def test_flag_shape_validation():
    # Valid shape
    good = WRAPPER_PREFIX + "0123456789abcdef0123456789abcdef" + WRAPPER_SUFFIX
    assert is_valid_flag_shape(good)

    # Invalid shapes: uppercase, short, long, special characters, whitespace, newlines
    assert not is_valid_flag_shape(WRAPPER_PREFIX + "0123456789ABCDEF0123456789abcdef" + WRAPPER_SUFFIX)
    assert not is_valid_flag_shape(WRAPPER_PREFIX + "0123456789abcdef" + WRAPPER_SUFFIX)
    assert not is_valid_flag_shape(WRAPPER_PREFIX + "0123456789abcdef0123456789abcdef0" + WRAPPER_SUFFIX)
    assert not is_valid_flag_shape(WRAPPER_PREFIX + "0123456789abcdef0123456789abcdeg" + WRAPPER_SUFFIX)
    assert not is_valid_flag_shape(good + "\n")
    assert not is_valid_flag_shape(" " + good)
    assert not is_valid_flag_shape("other_flag_format")
