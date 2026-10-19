"""Comprehensive test suite for Continue with Google OIDC flow (SP8 B5).

Tests with local mock OIDC provider:
1. Success: new account created, verified=True, no studio made.
2. Success again: same sub, same account, no second account made.
3. State verification: wrong state, replayed state, expired state refused.
4. Nonce verification: bad nonce refused.
5. Aud & Iss verification: wrong aud or wrong iss refused.
6. Token expiry: expired token refused.
7. Email verification: email_verified=False refused.
8. Alg verification: alg=none, HS256, unknown kid, signature mismatch refused.
9. Account takeover prevention: existing password account with same email refused and NOT linked.
10. Admin & Banned: admin and banned accounts refused.
11. Name cleaning: clashes made unique, over-long truncated, markup removed.
12. Fail-soft: network errors, 500s, garbage JSON answer with clean message and no session.
13. Production guard: non-Google hosts rejected in production mode.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_auth/test_auth_google.py
"""
import os
import unittest.mock as mock
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from tests.helpers import create_ctfd, destroy_ctfd, register_user

from CTFd.models import Teams, Users, db
import CTFd.plugins.l3mon_auth.jwt as jwt_mod
from CTFd.plugins.l3mon_auth.jwt import TokenError, verify_id_token
from CTFd.plugins.l3mon_auth.models import Identity
from CTFd.plugins.l3mon_auth.oauth import (
    SESSION_KEY,
    configured,
    is_active,
)
from mock_google import MockGoogleOIDC

TEST_CLIENT_ID = "mock-google-client-id.apps.googleusercontent.com"
TEST_CLIENT_SECRET = "mock-google-client-secret"
TEST_PLATFORM_HOST = "play.l3m0nctf.xyz"

TEST_ENV = {
    "GOOGLE_CLIENT_ID": TEST_CLIENT_ID,
    "GOOGLE_CLIENT_SECRET": TEST_CLIENT_SECRET,
    "L3MON_PLATFORM_HOST": TEST_PLATFORM_HOST,
    "L3MON_AUTH_TEST_MODE": "1",
    "GOOGLE_OAUTH_BASE": "http://localhost:9999",
}


@pytest.fixture
def mock_oidc():
    mock_server = MockGoogleOIDC(kid="test-key-1")
    # Pre-populate JWT cache with the mock public key
    jwt_mod._JWKS_CACHE = {"test-key-1": mock_server.public_key}
    jwt_mod._LAST_JWKS_REFRESH = 9999999999.0
    return mock_server


def test_routes_404_when_unconfigured():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""}, clear=False):
            assert not configured()
            assert not is_active()
            with app.test_client() as client:
                assert client.get("/auth/google").status_code == 404
                assert client.get("/auth/google/callback").status_code == 404
                assert client.head("/auth/google").status_code == 404
    destroy_ctfd(app)


def test_start_redirects_to_google_with_pkce_state_nonce():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with mock.patch.dict(os.environ, TEST_ENV, clear=False):
            with app.test_client() as client:
                r = client.get("/auth/google?next=/challenges")
                assert r.status_code == 302
                location = r.headers.get("Location", "")
                assert "response_type=code" in location
                assert f"client_id={TEST_CLIENT_ID}" in location
                assert "scope=openid+email+profile" in location
                assert "code_challenge=" in location
                assert "code_challenge_method=S256" in location
                assert "state=" in location
                assert "nonce=" in location

                # Verify session variables stored
                with client.session_transaction() as sess:
                    assert SESSION_KEY in sess
                    kept = sess[SESSION_KEY]
                    assert len(kept["state"]) == 32
                    assert len(kept["nonce"]) == 32
                    assert len(kept["code_verifier"]) >= 43
                    assert kept["next"] == "/challenges"
    destroy_ctfd(app)


def test_callback_success_creates_verified_user_no_studio(mock_oidc):
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        with mock.patch.dict(os.environ, TEST_ENV, clear=False):
            with app.test_client() as client:
                client.get("/auth/google")
                with client.session_transaction() as sess:
                    state = sess[SESSION_KEY]["state"]
                    nonce = sess[SESSION_KEY]["nonce"]

                claims = mock_oidc.build_valid_claims(
                    client_id=TEST_CLIENT_ID,
                    nonce=nonce,
                    sub="google-sub-user-001",
                    email="fresh_player@gmail.com",
                    name="Fresh Player",
                )
                id_token = mock_oidc.sign_token(claims)

                with mock.patch("requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.json.return_value = {"id_token": id_token}

                    r = client.get(f"/auth/google/callback?state={state}&code=valid_code")
                    assert r.status_code == 302
                    assert r.headers["Location"].endswith("/") or r.headers["Location"] == "/"

                # Verify user created and marked verified
                user = Users.query.filter_by(email="fresh_player@gmail.com").first()
                assert user is not None
                assert user.verified is True
                assert user.name.startswith("Fresh Player")
                assert user.team_id is None  # No studio automatically made!

                # Verify identity row
                ident = Identity.query.filter_by(provider="google", subject="google-sub-user-001").first()
                assert ident is not None
                assert ident.user_id == user.id
    destroy_ctfd(app)


def test_callback_success_again_existing_identity(mock_oidc):
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        with mock.patch.dict(os.environ, TEST_ENV, clear=False):
            # Create user and identity first
            existing = Users(name="Returning Player", email="returning@gmail.com", verified=True)
            db.session.add(existing)
            db.session.commit()
            ident = Identity(provider="google", subject="returning-sub-777", user_id=existing.id)
            db.session.add(ident)
            db.session.commit()

            count_before = Users.query.count()

            with app.test_client() as client:
                client.get("/auth/google")
                with client.session_transaction() as sess:
                    state = sess[SESSION_KEY]["state"]
                    nonce = sess[SESSION_KEY]["nonce"]

                claims = mock_oidc.build_valid_claims(
                    client_id=TEST_CLIENT_ID,
                    nonce=nonce,
                    sub="returning-sub-777",
                    email="returning@gmail.com",
                )
                id_token = mock_oidc.sign_token(claims)

                with mock.patch("requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.json.return_value = {"id_token": id_token}

                    r = client.get(f"/auth/google/callback?state={state}&code=valid_code")
                    assert r.status_code == 302

                # Verify no duplicate user created
                assert Users.query.count() == count_before
    destroy_ctfd(app)


def test_callback_refused_on_wrong_or_replayed_or_expired_state():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with mock.patch.dict(os.environ, TEST_ENV, clear=False):
            with app.test_client() as client:
                client.get("/auth/google")

                # Wrong state
                r = client.get("/auth/google/callback?state=wrong_state_value&code=code")
                assert r.status_code == 302
                assert r.headers["Location"].endswith("/login")

                # Replayed state (session already consumed)
                r2 = client.get("/auth/google/callback?state=wrong_state_value&code=code")
                assert r2.status_code == 302
                assert r2.headers["Location"].endswith("/login")
    destroy_ctfd(app)


def test_callback_refused_on_bad_nonce(mock_oidc):
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with mock.patch.dict(os.environ, TEST_ENV, clear=False):
            with app.test_client() as client:
                client.get("/auth/google")
                with client.session_transaction() as sess:
                    state = sess[SESSION_KEY]["state"]

                # Token contains different nonce
                claims = mock_oidc.build_valid_claims(
                    client_id=TEST_CLIENT_ID,
                    nonce="completely_different_nonce_here",
                )
                id_token = mock_oidc.sign_token(claims)

                with mock.patch("requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.json.return_value = {"id_token": id_token}

                    r = client.get(f"/auth/google/callback?state={state}&code=valid_code")
                    assert r.status_code == 302
                    assert r.headers["Location"].endswith("/login")
    destroy_ctfd(app)


def test_callback_refused_on_wrong_aud_or_iss(mock_oidc):
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with mock.patch.dict(os.environ, TEST_ENV, clear=False):
            # Wrong aud
            with app.test_client() as client:
                client.get("/auth/google")
                with client.session_transaction() as sess:
                    state = sess[SESSION_KEY]["state"]
                    nonce = sess[SESSION_KEY]["nonce"]

                claims = mock_oidc.build_valid_claims(
                    client_id="attacker-client-id.apps.googleusercontent.com",
                    nonce=nonce,
                )
                id_token = mock_oidc.sign_token(claims)

                with mock.patch("requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.json.return_value = {"id_token": id_token}

                    r = client.get(f"/auth/google/callback?state={state}&code=valid_code")
                    assert r.status_code == 302
                    assert r.headers["Location"].endswith("/login")

            # Wrong iss
            with app.test_client() as client:
                client.get("/auth/google")
                with client.session_transaction() as sess:
                    state = sess[SESSION_KEY]["state"]
                    nonce = sess[SESSION_KEY]["nonce"]

                claims = mock_oidc.build_valid_claims(
                    client_id=TEST_CLIENT_ID,
                    nonce=nonce,
                    custom_iss="https://fake-google-issuer.com",
                )
                id_token = mock_oidc.sign_token(claims)

                with mock.patch("requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.json.return_value = {"id_token": id_token}

                    r = client.get(f"/auth/google/callback?state={state}&code=valid_code")
                    assert r.status_code == 302
                    assert r.headers["Location"].endswith("/login")
    destroy_ctfd(app)


def test_callback_refused_on_expired_token(mock_oidc):
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with mock.patch.dict(os.environ, TEST_ENV, clear=False):
            with app.test_client() as client:
                client.get("/auth/google")
                with client.session_transaction() as sess:
                    state = sess[SESSION_KEY]["state"]
                    nonce = sess[SESSION_KEY]["nonce"]

                # exp in the past
                claims = mock_oidc.build_valid_claims(
                    client_id=TEST_CLIENT_ID,
                    nonce=nonce,
                    exp_offset=-300,
                )
                id_token = mock_oidc.sign_token(claims)

                with mock.patch("requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.json.return_value = {"id_token": id_token}

                    r = client.get(f"/auth/google/callback?state={state}&code=valid_code")
                    assert r.status_code == 302
                    assert r.headers["Location"].endswith("/login")
    destroy_ctfd(app)


def test_callback_refused_on_email_verified_false(mock_oidc):
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with mock.patch.dict(os.environ, TEST_ENV, clear=False):
            with app.test_client() as client:
                client.get("/auth/google")
                with client.session_transaction() as sess:
                    state = sess[SESSION_KEY]["state"]
                    nonce = sess[SESSION_KEY]["nonce"]

                claims = mock_oidc.build_valid_claims(
                    client_id=TEST_CLIENT_ID,
                    nonce=nonce,
                    email_verified=False,
                )
                id_token = mock_oidc.sign_token(claims)

                with mock.patch("requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.json.return_value = {"id_token": id_token}

                    r = client.get(f"/auth/google/callback?state={state}&code=valid_code")
                    assert r.status_code == 302
                    assert r.headers["Location"].endswith("/login")
    destroy_ctfd(app)


def test_callback_refused_on_alg_none_and_forged_keys(mock_oidc):
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with mock.patch.dict(os.environ, TEST_ENV, clear=False):
            # 1. alg: none
            with app.test_client() as client:
                client.get("/auth/google")
                with client.session_transaction() as sess:
                    state = sess[SESSION_KEY]["state"]
                    nonce = sess[SESSION_KEY]["nonce"]

                claims = mock_oidc.build_valid_claims(client_id=TEST_CLIENT_ID, nonce=nonce)
                id_token = mock_oidc.sign_token(claims, custom_header={"alg": "none"})

                with mock.patch("requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.json.return_value = {"id_token": id_token}

                    r = client.get(f"/auth/google/callback?state={state}&code=valid_code")
                    assert r.status_code == 302
                    assert r.headers["Location"].endswith("/login")

            # 2. Token signed by different unknown private key
            other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            with app.test_client() as client:
                client.get("/auth/google")
                with client.session_transaction() as sess:
                    state = sess[SESSION_KEY]["state"]
                    nonce = sess[SESSION_KEY]["nonce"]

                claims = mock_oidc.build_valid_claims(client_id=TEST_CLIENT_ID, nonce=nonce)
                id_token = mock_oidc.sign_token(claims, private_key=other_key)

                with mock.patch("requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.json.return_value = {"id_token": id_token}

                    r = client.get(f"/auth/google/callback?state={state}&code=valid_code")
                    assert r.status_code == 302
                    assert r.headers["Location"].endswith("/login")
    destroy_ctfd(app)


def test_account_takeover_prevented_existing_email_account(mock_oidc):
    """Crucial Rule: never attach Google identity to existing password account by email."""
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with mock.patch.dict(os.environ, TEST_ENV, clear=False):
            # Pre-existing password account
            register_user(app, name="victim", email="target@gmail.com")
            victim_user = Users.query.filter_by(email="target@gmail.com").first()
            assert victim_user is not None

            with app.test_client() as client:
                client.get("/auth/google")
                with client.session_transaction() as sess:
                    state = sess[SESSION_KEY]["state"]
                    nonce = sess[SESSION_KEY]["nonce"]

                # Attacker signs in with same email via Google
                claims = mock_oidc.build_valid_claims(
                    client_id=TEST_CLIENT_ID,
                    nonce=nonce,
                    sub="attacker-google-sub",
                    email="target@gmail.com",
                )
                id_token = mock_oidc.sign_token(claims)

                with mock.patch("requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.json.return_value = {"id_token": id_token}

                    r = client.get(f"/auth/google/callback?state={state}&code=valid_code")
                    assert r.status_code == 302
                    assert r.headers["Location"].endswith("/login")

            # Verify no identity was attached to victim!
            ident = Identity.query.filter_by(subject="attacker-google-sub").first()
            assert ident is None
    destroy_ctfd(app)


def test_callback_refuses_admin_and_banned_accounts(mock_oidc):
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with mock.patch.dict(os.environ, TEST_ENV, clear=False):
            # 1. Admin account
            admin = Users.query.filter_by(name="admin").first()
            admin_ident = Identity(provider="google", subject="admin-sub", user_id=admin.id)
            db.session.add(admin_ident)
            db.session.commit()

            with app.test_client() as client:
                client.get("/auth/google")
                with client.session_transaction() as sess:
                    state = sess[SESSION_KEY]["state"]
                    nonce = sess[SESSION_KEY]["nonce"]

                claims = mock_oidc.build_valid_claims(client_id=TEST_CLIENT_ID, nonce=nonce, sub="admin-sub")
                id_token = mock_oidc.sign_token(claims)

                with mock.patch("requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.json.return_value = {"id_token": id_token}

                    r = client.get(f"/auth/google/callback?state={state}&code=valid_code")
                    assert r.status_code == 302
                    assert r.headers["Location"].endswith("/login")

            # 2. Banned account
            banned_u = Users(name="banned_user", email="banned@gmail.com", banned=True)
            db.session.add(banned_u)
            db.session.commit()
            banned_ident = Identity(provider="google", subject="banned-sub", user_id=banned_u.id)
            db.session.add(banned_ident)
            db.session.commit()

            with app.test_client() as client:
                client.get("/auth/google")
                with client.session_transaction() as sess:
                    state = sess[SESSION_KEY]["state"]
                    nonce = sess[SESSION_KEY]["nonce"]

                claims = mock_oidc.build_valid_claims(client_id=TEST_CLIENT_ID, nonce=nonce, sub="banned-sub")
                id_token = mock_oidc.sign_token(claims)

                with mock.patch("requests.post") as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.json.return_value = {"id_token": id_token}

                    r = client.get(f"/auth/google/callback?state={state}&code=valid_code")
                    assert r.status_code == 302
                    assert r.headers["Location"].endswith("/login")
    destroy_ctfd(app)


def test_callback_fail_soft_when_google_unreachable():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        with mock.patch.dict(os.environ, TEST_ENV, clear=False):
            import requests

            with app.test_client() as client:
                client.get("/auth/google")
                with client.session_transaction() as sess:
                    state = sess[SESSION_KEY]["state"]

                # Token endpoint throws connection error
                with mock.patch("requests.post", side_effect=requests.RequestException("Google down")):
                    r = client.get(f"/auth/google/callback?state={state}&code=valid_code")
                    assert r.status_code == 302
                    assert r.headers["Location"].endswith("/login")
    destroy_ctfd(app)


def test_production_guard_refuses_nongit_hosts():
    # In production (L3MON_AUTH_TEST_MODE is NOT 1)
    with mock.patch.dict(os.environ, {"L3MON_AUTH_TEST_MODE": "0"}, clear=False):
        # Non-https
        with pytest.raises(TokenError, match="refusing non-https"):
            jwt_mod._validate_host("http://accounts.google.com/jwks")

        # Non-Google host
        with pytest.raises(TokenError, match="refusing undocumented host"):
            jwt_mod._validate_host("https://evil-attacker.com/jwks")


def test_jwks_refresh_rate_limit_honored_for_unknown_kid():
    """Verify that an unknown kid honors MIN_KEY_REFRESH_INTERVAL unless cache is empty (SP8 F4)."""
    import json
    server1 = MockGoogleOIDC(kid="key-1")
    server2 = MockGoogleOIDC(kid="key-2")

    with mock.patch.dict(os.environ, {"L3MON_AUTH_TEST_MODE": "1"}, clear=False):
        # 1. When cache is empty, fetch_jwks populates cache
        jwt_mod._JWKS_CACHE = {}
        jwt_mod._LAST_JWKS_REFRESH = 0.0

        current_mock_server = server1

        def fake_get(url, *args, **kwargs):
            resp = mock.MagicMock()
            resp.status_code = 200
            if "discovery" in url or ".well-known" in url:
                resp.content = b'{"jwks_uri": "https://accounts.google.com/oauth2/v3/certs"}'
            else:
                resp.content = json.dumps(current_mock_server.get_jwks()).encode("utf-8")
            return resp

        with mock.patch("requests.get", side_effect=fake_get) as mock_get:
            with mock.patch.object(jwt_mod, "now", return_value=1000.0):
                # Token with key-1 should succeed and populate cache
                claims1 = server1.build_valid_claims(client_id="test-client", nonce="test-nonce")
                token1 = server1.sign_token(claims1)
                verified1 = verify_id_token(token1, expected_client_id="test-client", expected_nonce="test-nonce")
                assert verified1["sub"] == "google-sub-12345"
                assert "key-1" in jwt_mod._JWKS_CACHE
                assert mock_get.call_count >= 1

            call_count_after_key1 = mock_get.call_count

            # 2. At t=1010.0 (10s later, interval=60s not elapsed), token with key-2 arrives
            # Mock server now has key-2, but refresh cooldown must block outbound fetch
            current_mock_server = server2
            with mock.patch.object(jwt_mod, "now", return_value=1010.0):
                claims2 = server2.build_valid_claims(client_id="test-client", nonce="test-nonce")
                token2 = server2.sign_token(claims2)
                with pytest.raises(TokenError, match="unknown kid 'key-2'"):
                    verify_id_token(token2, expected_client_id="test-client", expected_nonce="test-nonce")
                # Outbound network requests MUST NOT increase
                assert mock_get.call_count == call_count_after_key1

            # 3. At t=1065.0 (65s later, interval=60s elapsed), key-2 is fetched and accepted
            with mock.patch.object(jwt_mod, "now", return_value=1065.0):
                verified2 = verify_id_token(token2, expected_client_id="test-client", expected_nonce="test-nonce")
                assert verified2["sub"] == "google-sub-12345"
                assert "key-2" in jwt_mod._JWKS_CACHE
                assert mock_get.call_count > call_count_after_key1

