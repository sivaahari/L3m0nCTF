"""A stand-in for oauth.ctftime.org for the tests: the same addresses (`/token`, `/user`) with the answers CTFtime documents, and the
ways it is known to fail (a 403 from the Cloudflare in front of it, a server error, something that is not JSON, no token, a slow answer).

Not a test file. It runs on 127.0.0.1 in a thread, and the plugin talks to it with the real `requests` code, so the timeouts, the form
body and the headers are really sent and really read.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


def profile(user_id, name, email, team=None, **extra):
    """What `/user` answers for the scopes profile:read and team:read."""
    out = {"id": user_id, "name": name, "email": email, "country": "IN"}
    if team is not None:
        out["team"] = {"id": team[0], "name": team[1], "country": "IN", "logo": "https://ctftime.example/logo.png"}
    out.update(extra)
    return out


class MockCTFtime:
    def __init__(self, client_id="4242", client_secret="secret-for-the-tests-only", redirect_uri="https://play.example.test/auth/ctftime/callback"):
        self.client_id, self.client_secret, self.redirect_uri = client_id, client_secret, redirect_uri
        self.profiles = {}  # authorization code -> the answer of /user
        self.token_mode = "ok"  # ok | 403 | 500 | not_json | not_object | no_token | bad_token | spaced_token | 500_with_token | redirect | slow
        self.user_mode = "ok"  # ok | 403 | 403_with_profile | not_json | not_object | slow
        self.delay = 3.0  # how long "slow" waits
        self.moved_token = None
        self.seen = []  # every request: {"method", "path", "form", "authorization"}
        self.server = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self):
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _send(self, status, body, content_type="application/json"):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                mock.seen.append({"method": "POST", "path": self.path, "form": form, "authorization": self.headers.get("Authorization")})
                if self.path != "/token":
                    return self._send(404, {"error": "not_found"})
                mode = mock.token_mode
                if mode == "slow":
                    time.sleep(mock.delay)
                if mode == "403":
                    return self._send(403, b"<html><title>Attention Required! | Cloudflare</title></html>", "text/html")
                if mode == "500":
                    return self._send(500, {"error": "server_error"})
                if mode == "not_json":
                    return self._send(200, b"<html>maintenance</html>", "text/html")
                if mode == "not_object":
                    return self._send(200, [1, 2, 3])
                right = (
                    form.get("grant_type") == "authorization_code" and form.get("client_id") == mock.client_id and form.get("client_secret") == mock.client_secret
                    and form.get("redirect_uri") == mock.redirect_uri and form.get("code") in mock.profiles
                )
                if not right:
                    return self._send(400, {"error": "invalid_grant"})
                if mode == "no_token":
                    return self._send(200, {"token_type": "Bearer"})
                if mode == "bad_token":
                    return self._send(200, {"access_token": "abc\r\nX-Evil: 1", "token_type": "Bearer"})
                if mode == "spaced_token":  # a token with a space in it: not what a bearer token is made of (the stand-in's /user takes it anyway)
                    return self._send(200, {"access_token": "abc def", "token_type": "Bearer"})
                if mode == "500_with_token":  # an error status whose body looks like a good answer
                    return self._send(500, {"access_token": "tok-" + form["code"], "token_type": "Bearer"})
                if mode == "redirect":  # a valid token answer exists, but only at the place the redirect points to
                    mock.moved_token = "tok-" + form["code"]
                    self.send_response(302)
                    self.send_header("Location", "/moved-token")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                return self._send(200, {"access_token": "tok-" + form["code"], "token_type": "Bearer", "expires_in": 3600})

            def do_GET(self):
                mock.seen.append({"method": "GET", "path": self.path, "form": {}, "authorization": self.headers.get("Authorization")})
                if self.path == "/moved-token" and mock.moved_token:
                    return self._send(200, {"access_token": mock.moved_token, "token_type": "Bearer"})
                if self.path != "/user":
                    return self._send(404, {"error": "not_found"})
                mode = mock.user_mode
                if mode == "slow":
                    time.sleep(mock.delay)
                if mode == "403":
                    return self._send(403, b"<html>Cloudflare</html>", "text/html")
                if mode == "not_json":
                    return self._send(200, b"<html>maintenance</html>", "text/html")
                if mode == "not_object":
                    return self._send(200, ["a"])
                auth = self.headers.get("Authorization") or ""
                code = auth[len("Bearer tok-"):] if auth.startswith("Bearer tok-") else None
                if mock.token_mode in ("bad_token", "spaced_token"):
                    code = next(iter(mock.profiles), None)  # the stand-in takes whatever token it was given, so only the plugin can refuse it
                if mode == "403_with_profile" and code in mock.profiles:
                    return self._send(403, mock.profiles[code])
                if code not in mock.profiles:
                    return self._send(401, {"error": "invalid_token"})
                return self._send(200, mock.profiles[code])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    def paths(self):
        return [(row["method"], row["path"]) for row in self.seen]
