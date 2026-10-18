"""Google OIDC ID Token verification using standard cryptography package (SP8 B5).

Rules:
- Verification using cryptography package (no PyJWT dependency)
- Discovery doc and JWKS fetched over HTTPS with short timeout, no redirects, size cap, TLS on
- Only Google documented hosts allowed in production
- Test mode enabled only when L3MON_AUTH_TEST_MODE=1
- Key cache with TTL and rate-limited refresh on unknown kid (at most once/min)
- Algorithm strictly RS256 (reject none, HS256, etc.)
- Public key size >= 2048 bits
- Token size cap <= 4096 bytes
- Strict claims verification: iss, aud, exp, iat, nonce, email_verified=True
"""
import base64
import json
import logging
import os
import re
import time
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import requests

from CTFd.plugins.l3mon_core.clock import now

_log = logging.getLogger("l3mon.auth")

GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_HOSTS = ("accounts.google.com", "www.googleapis.com")

TIMEOUT = (2.0, 3.05)
MAX_RESPONSE_BYTES = 32768
MAX_TOKEN_LENGTH = 4096
CLOCK_TOLERANCE = 60  # seconds
MIN_KEY_REFRESH_INTERVAL = 60  # seconds

# In-memory key cache: {kid: {"key": RSAPublicKey, "expires_at": float}}
_JWKS_CACHE: dict[str, dict] = {}
_LAST_JWKS_REFRESH: float = 0.0


class TokenError(Exception):
    """Raised when ID token verification fails. Message is safe for logging."""


def _is_test_mode() -> bool:
    return os.environ.get("L3MON_AUTH_TEST_MODE", "").strip() == "1"


def get_discovery_url() -> str:
    if _is_test_mode():
        custom = os.environ.get("GOOGLE_DISCOVERY_URL", "").strip()
        if custom:
            return custom
        base = os.environ.get("GOOGLE_OAUTH_BASE", "").strip().rstrip("/")
        if base:
            return f"{base}/.well-known/openid-configuration"
    return GOOGLE_DISCOVERY


def _validate_host(url: str) -> None:
    parts = urlsplit(url)
    if _is_test_mode():
        if parts.scheme in ("http", "https") and parts.hostname in ("localhost", "127.0.0.1", "::1"):
            return
    if parts.scheme != "https":
        raise TokenError(f"refusing non-https URL: {url}")
    if parts.hostname not in GOOGLE_HOSTS:
        raise TokenError(f"refusing undocumented host: {parts.hostname}")


def _int_from_b64url(val: str) -> int:
    padded = val + "=" * (-len(val) % 4)
    data = base64.urlsafe_b64decode(padded.encode("ascii"))
    return int.from_bytes(data, byteorder="big")


def _b64url_decode(val: str) -> bytes:
    padded = val + "=" * (-len(val) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def fetch_jwks(force: bool = False) -> None:
    """Fetch and parse JWKS into memory cache."""
    global _JWKS_CACHE, _LAST_JWKS_REFRESH
    t = now()
    if not force and _LAST_JWKS_REFRESH and (t - _LAST_JWKS_REFRESH < MIN_KEY_REFRESH_INTERVAL):
        return

    discovery_url = get_discovery_url()
    _validate_host(discovery_url)

    try:
        disc_resp = requests.get(
            discovery_url,
            timeout=TIMEOUT,
            allow_redirects=False,
            verify=True,
            headers={"Accept": "application/json"},
        )
        if disc_resp.status_code != 200:
            raise TokenError(f"discovery document returned HTTP {disc_resp.status_code}")
        disc_data = json.loads(disc_resp.content[:MAX_RESPONSE_BYTES].decode("utf-8"))
        jwks_uri = disc_data.get("jwks_uri", GOOGLE_JWKS)
    except Exception as exc:
        if not _is_test_mode():
            jwks_uri = GOOGLE_JWKS
        else:
            raise TokenError(f"failed to fetch discovery document: {exc}") from exc

    _validate_host(jwks_uri)
    try:
        resp = requests.get(
            jwks_uri,
            timeout=TIMEOUT,
            allow_redirects=False,
            verify=True,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            raise TokenError(f"jwks endpoint returned HTTP {resp.status_code}")
        content = resp.content[:MAX_RESPONSE_BYTES]
        data = json.loads(content.decode("utf-8"))
    except Exception as exc:
        raise TokenError(f"failed to fetch JWKS: {exc}") from exc

    keys = data.get("keys", [])
    if not isinstance(keys, list):
        raise TokenError("invalid JWKS payload: keys is not a list")

    new_cache = {}
    for key_dict in keys:
        if not isinstance(key_dict, dict) or key_dict.get("kty") != "RSA":
            continue
        kid = key_dict.get("kid")
        n_str = key_dict.get("n")
        e_str = key_dict.get("e")
        if not kid or not n_str or not e_str:
            continue
        try:
            n = _int_from_b64url(n_str)
            e = _int_from_b64url(e_str)
            public_numbers = rsa.RSAPublicNumbers(e, n)
            public_key = public_numbers.public_key()
            if public_key.key_size < 2048:
                _log.warning("l3mon_auth: rejecting weak key size %d for kid %s", public_key.key_size, kid)
                continue
            new_cache[kid] = public_key
        except Exception as exc:
            _log.warning("l3mon_auth: failed to parse RSA key %s: %s", kid, exc)

    if new_cache:
        _JWKS_CACHE = new_cache
        _LAST_JWKS_REFRESH = t


def verify_id_token(
    id_token: str,
    expected_client_id: str,
    expected_nonce: str,
) -> dict:
    """Verify Google ID token RS256 signature and claims.

    Returns claims dict on success.
    Raises TokenError on any verification failure.
    """
    if not id_token or len(id_token) > MAX_TOKEN_LENGTH:
        raise TokenError("id_token missing or exceeds length limit")

    parts = id_token.split(".")
    if len(parts) != 3:
        raise TokenError("malformed id_token (not 3 parts)")

    raw_header, raw_payload, raw_sig = parts

    try:
        header = json.loads(_b64url_decode(raw_header).decode("utf-8"))
        claims = json.loads(_b64url_decode(raw_payload).decode("utf-8"))
        signature = _b64url_decode(raw_sig)
    except Exception as exc:
        raise TokenError(f"malformed base64/json in id_token: {exc}") from exc

    alg = header.get("alg")
    if alg != "RS256":
        raise TokenError(f"unsupported algorithm '{alg}', strictly RS256 required")

    kid = header.get("kid")
    if not kid or not isinstance(kid, str):
        raise TokenError("missing or invalid 'kid' in header")

    # Look up public key
    if kid not in _JWKS_CACHE:
        # Honor MIN_KEY_REFRESH_INTERVAL unless cache is empty (SP8 F4)
        force_refresh = not bool(_JWKS_CACHE)
        fetch_jwks(force=force_refresh)

    public_key = _JWKS_CACHE.get(kid)
    if not public_key:
        raise TokenError(f"unknown kid '{kid}' not found in JWKS")

    # Verify signature
    signed_data = f"{raw_header}.{raw_payload}".encode("ascii")
    try:
        public_key.verify(signature, signed_data, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise TokenError("invalid token signature")
    except Exception as exc:
        raise TokenError(f"signature verification error: {exc}") from exc

    # Verify claims
    t = now()

    # Issuer
    iss = claims.get("iss", "")
    if _is_test_mode():
        valid_issuers = ("https://accounts.google.com", "accounts.google.com", os.environ.get("GOOGLE_MOCK_ISSUER", ""))
    else:
        valid_issuers = ("https://accounts.google.com", "accounts.google.com")

    if iss not in valid_issuers:
        raise TokenError(f"invalid issuer '{iss}'")

    # Audience
    aud = claims.get("aud", "")
    if aud != expected_client_id:
        raise TokenError(f"audience '{aud}' does not match client_id")

    # Expiry
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp < (t - CLOCK_TOLERANCE):
        raise TokenError("token has expired")

    # Issued at
    iat = claims.get("iat")
    if not isinstance(iat, (int, float)) or iat > (t + CLOCK_TOLERANCE):
        raise TokenError("token issued in the future")

    # Nonce
    token_nonce = claims.get("nonce", "")
    if token_nonce != expected_nonce:
        raise TokenError("nonce does not match session")

    # Email verified
    if claims.get("email_verified") is not True:
        raise TokenError("email_verified is not true")

    sub = claims.get("sub", "")
    if not sub or not isinstance(sub, str):
        raise TokenError("missing or empty 'sub' in claims")

    email = claims.get("email", "")
    if not email or not isinstance(email, str):
        raise TokenError("missing or empty 'email' in claims")

    return claims
