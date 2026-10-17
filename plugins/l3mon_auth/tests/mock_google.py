"""Mock Google OIDC provider fixture for testing l3mon_auth (SP8 B5).

Generates RSA key at test time and produces signed tokens, discovery document,
and JWKS without contacting external networks.
"""
import base64
import json
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from CTFd.plugins.l3mon_core.clock import now


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _int_to_b64url(val: int) -> str:
    byte_len = (val.bit_length() + 7) // 8
    data = val.to_bytes(byte_len, byteorder="big")
    return _b64url_encode(data)


class MockGoogleOIDC:
    """Mock Google OIDC identity provider."""

    def __init__(self, kid: str = "mock-key-1", issuer: str = "https://accounts.google.com"):
        self.kid = kid
        self.issuer = issuer
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.public_key = self.private_key.public_key()
        self.public_numbers = self.public_key.public_numbers()

    def get_jwk(self, custom_kid: str | None = None) -> dict:
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": custom_kid or self.kid,
            "n": _int_to_b64url(self.public_numbers.n),
            "e": _int_to_b64url(self.public_numbers.e),
        }

    def get_jwks(self) -> dict:
        return {"keys": [self.get_jwk()]}

    def get_discovery(self, base_url: str = "http://localhost:9999") -> dict:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{base_url}/o/oauth2/v2/auth",
            "token_endpoint": f"{base_url}/token",
            "jwks_uri": f"{base_url}/oauth2/v3/certs",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "email", "profile"],
        }

    def sign_token(
        self,
        claims: dict,
        custom_header: dict | None = None,
        private_key: rsa.RSAPrivateKey | None = None,
    ) -> str:
        header = {
            "alg": "RS256",
            "kid": self.kid,
            "typ": "JWT",
        }
        if custom_header:
            header.update(custom_header)

        header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
        payload_bytes = json.dumps(claims, separators=(",", ":")).encode("utf-8")

        raw_header = _b64url_encode(header_bytes)
        raw_payload = _b64url_encode(payload_bytes)
        signed_data = f"{raw_header}.{raw_payload}".encode("ascii")

        key_to_use = private_key or self.private_key

        if header.get("alg") == "RS256":
            sig = key_to_use.sign(signed_data, padding.PKCS1v15(), hashes.SHA256())
            raw_sig = _b64url_encode(sig)
        elif header.get("alg") == "none":
            raw_sig = ""
        else:
            raw_sig = _b64url_encode(b"forged_signature")

        return f"{raw_header}.{raw_payload}.{raw_sig}"

    def build_valid_claims(
        self,
        client_id: str,
        nonce: str,
        sub: str = "google-sub-12345",
        email: str = "player@gmail.com",
        name: str = "Google Player",
        email_verified: bool = True,
        exp_offset: int = 3600,
        iat_offset: int = 0,
        custom_iss: str | None = None,
    ) -> dict:
        t = int(now())
        return {
            "iss": custom_iss or self.issuer,
            "sub": sub,
            "aud": client_id,
            "exp": t + exp_offset,
            "iat": t + iat_offset,
            "nonce": nonce,
            "email": email,
            "email_verified": email_verified,
            "name": name,
        }
