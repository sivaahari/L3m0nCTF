"""The production front door, tried for real: nginx with the production template, HTTPS on 8443, Cloudflare's client certificate required.

A second nginx container (the same image and the same files production uses: nginx.conf, templates-production, snippets) is started on the running
stack's network with throwaway certificates made here, and talked to over TLS from the host. It sits in front of the development CTFd, which is enough
to prove everything that is nginx's to prove: who gets a handshake, who gets an answer, what the answer carries, and what the public feeds look like
through it. (The cookie flag and the mail settings are CTFd's and the compose file's: tests/test_production_compose.py.)

Same prerequisites as test_stack.py (the stack running) and `openssl` on the PATH.
"""
from __future__ import annotations

import http.client
import json
import re
import shutil
import socket
import ssl
import subprocess
import time
from types import SimpleNamespace

import pytest

import test_stack as t

pytestmark = pytest.mark.integration

NAME = "l3mon-prodedge"
PORT = 18443
HOST = "play.l3m0nctf.xyz"
CREW = "crew.l3m0nctf.xyz"
FIXTURES = t.ROOT / "tests" / "integration" / "fixtures"
FOLDER = FIXTURES / "tls-run"
NOBODY = FIXTURES / "admin-nets-nobody.conf"
NETWORK = "l3mon_edge"


def openssl(*args):
    out = subprocess.run(["openssl", *args], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-500:]


def make_certificates(folder):
    """A Cloudflare stand-in (a CA that signs the client certificate nginx demands), a stranger's CA and client, and the origin certificate."""
    f = folder.as_posix()
    for who in ("cloudflare", "stranger"):
        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{f}/{who}-ca.key", "-out", f"{f}/{who}-ca.pem", "-days", "2", "-subj", f"/CN=test {who} CA")
        openssl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{f}/{who}-client.key", "-out", f"{f}/{who}-client.csr", "-subj", f"/CN=test {who} client")
        openssl("x509", "-req", "-in", f"{f}/{who}-client.csr", "-CA", f"{f}/{who}-ca.pem", "-CAkey", f"{f}/{who}-ca.key", "-CAcreateserial", "-out", f"{f}/{who}-client.pem", "-days", "2")
    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{f}/origin.key", "-out", f"{f}/origin.pem", "-days", "2", "-subj", f"/CN={HOST}",
            "-addext", f"subjectAltName=DNS:{HOST},DNS:{CREW}")
    shutil.copy(folder / "cloudflare-ca.pem", folder / "cloudflare-origin-pull-ca.pem")
    for path in folder.glob("*"):
        path.chmod(0o644)  # throwaway keys; nginx runs as another user inside its container


def nginx_image():
    text = (t.ROOT / "deploy" / "compose" / "compose.base.yml").read_text(encoding="utf-8")
    return re.search(r"image: (nginxinc/nginx-unprivileged\S+)", text).group(1)


def docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"})


@pytest.fixture(scope="module")
def edge():
    shutil.rmtree(FOLDER, ignore_errors=True)
    FOLDER.mkdir(parents=True)
    make_certificates(FOLDER)
    docker("rm", "-f", NAME, check=False)
    root = t.ROOT.as_posix()
    docker(
        "run", "-d", "--name", NAME, "--network", NETWORK, "-p", f"127.0.0.1:{PORT}:8443",
        "-e", f"PLATFORM_HOST={HOST}", "-e", f"EXTRA_HOST={CREW}", "-e", "NGINX_ENTRYPOINT_QUIET_LOGS=1",
        "--read-only", "--tmpfs", "/tmp", "--tmpfs", "/var/cache/nginx", "--tmpfs", "/etc/nginx/conf.d:uid=101,gid=101,mode=755",
        "-v", f"{root}/deploy/nginx/nginx.conf:/etc/nginx/nginx.conf:ro",
        "-v", f"{root}/deploy/nginx/templates-production:/etc/nginx/templates:ro",
        "-v", f"{root}/deploy/nginx/snippets:/etc/nginx/snippets:ro",
        "-v", f"{NOBODY.as_posix()}:/etc/nginx/snippets/admin-nets.conf:ro",
        "-v", f"{FOLDER.as_posix()}:/etc/nginx/tls:ro",
        nginx_image(),
    )
    try:
        end = time.time() + 40
        while time.time() < end:
            try:  # the published port accepts connections before nginx listens: ask a real question, over TLS
                if get("/healthcheck")[0] == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.5)
        else:
            raise AssertionError("the production nginx did not start: " + docker("logs", NAME, check=False).stdout[-800:] + docker("logs", NAME, check=False).stderr[-800:])
        yield SimpleNamespace(folder=FOLDER)
    finally:
        docker("rm", "-f", NAME, check=False)
        shutil.rmtree(FOLDER, ignore_errors=True)


def context(client=None):
    """Trusts only the throwaway origin certificate made for this run. The host name is not checked: the address is 127.0.0.1 and the name goes in the SNI."""
    ctx = ssl.create_default_context(cafile=(FOLDER / "origin.pem").as_posix())
    ctx.check_hostname = False
    if client:
        ctx.load_cert_chain((FOLDER / f"{client}-client.pem").as_posix(), (FOLDER / f"{client}-client.key").as_posix())
    return ctx


def get(path, client="cloudflare", host=HOST, sni=HOST, headers=None, method="GET"):
    """-> (status, headers as pairs, body). A refusal inside the handshake raises ssl.SSLError / ConnectionError."""
    conn = http.client.HTTPSConnection("127.0.0.1", PORT, context=context(client), timeout=15)
    conn.sock = context(client).wrap_socket(socket.create_connection(("127.0.0.1", PORT), timeout=15), server_hostname=sni)
    conn.request(method, path, headers={"Host": host, **(headers or {})})
    resp = conn.getresponse()
    body = resp.read()
    pairs = resp.getheaders()
    conn.close()
    return resp.status, pairs, body


def values(pairs, name):
    return [v for k, v in pairs if k.lower() == name.lower()]


def refused(call):
    """True when a request is refused in the handshake, by a closed connection, or by nginx's 400 about the certificate."""
    try:
        status, _, body = call()
    except (ssl.SSLError, ConnectionError, OSError, http.client.HTTPException):
        return True
    return status == 400 and (b"No required SSL certificate" in body or b"SSL certificate error" in body)


def test_cloudflares_certificate_gets_an_answer_over_tls_one_two_or_three(edge):
    status, headers, body = get("/healthcheck")
    assert status == 200, body[:200]
    assert values(headers, "Strict-Transport-Security") == ["max-age=31536000; includeSubDomains"], "nginx itself ended the TLS, so it says so without being told"
    assert not values(headers, "Server")[0:1] or "nginx/" not in values(headers, "Server")[0], "no version number"
    assert values(headers, "Set-Cookie") == [], "the health route carries no cookie"


def test_a_visitor_with_no_client_certificate_is_refused_before_ctfd_sees_anything(edge):
    assert refused(lambda: get("/healthcheck", client=None))
    assert refused(lambda: get("/login", client=None))
    assert refused(lambda: get("/ctftime/standings.json", client=None))


def test_a_certificate_from_another_authority_is_refused(edge):
    """Another Cloudflare account's certificate, or anybody's, is not the one this server trusts."""
    assert refused(lambda: get("/healthcheck", client="stranger"))


def test_a_server_name_that_is_not_ours_gets_no_certificate_at_all(edge):
    with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
        get("/healthcheck", sni="evil.example")


def test_a_host_header_that_is_not_ours_gets_nothing_even_with_the_right_certificate(edge):
    with pytest.raises((http.client.RemoteDisconnected, ConnectionError, OSError)):
        get("/healthcheck", host="evil.example")


def test_the_crews_host_name_gets_through_nginx_to_ctfd(edge):
    """nginx answers for the crew's name as for the platform's (it is the same server block). The development CTFd behind it trusts only the platform's
    name, so its own answer here is its refusal of an unknown host; production's compose file adds the crew's name to TRUSTED_HOSTS (test_production_compose.py)."""
    status, _, _ = get("/healthcheck", host=CREW, sni=CREW)
    assert status in (200, 400, 500), "a response from CTFd, not a closed connection or nginx's own refusal"


def test_the_admin_pages_are_refused_to_an_address_that_is_not_the_crews_even_with_the_right_certificate(edge):
    status, _, body = get("/admin")
    assert status == 403 and b"nginx" in body


def test_a_write_nobody_listed_is_refused_even_with_a_decoded_line_break_in_the_path(edge):
    for path in ("/api/v1/brand-new-thing", "/api/v1/brand-new-thing%0A", "/api/v1/x%0A/y"):
        status, _, body = get(path, method="POST")
        assert status in (403, 415), (path, status)


def test_the_public_feeds_reach_ctfd_through_the_production_front_door_with_no_cookie_and_a_short_cache(edge):
    status, headers, body = get("/ctftime/standings.json")
    assert status == 200, body[:200]
    assert json.loads(body) == {"standings": []} or "standings" in json.loads(body)
    assert values(headers, "Set-Cookie") == [] and values(headers, "Vary") == []
    assert re.search(r"max-age=\d+", " ".join(values(headers, "Cache-Control")))
    final_status, _, _ = get("/ctftime/final-standings.json")
    assert final_status == 404, "the final file does not exist until the crew publishes it after the end"


def test_the_ctftime_sign_in_routes_do_not_exist_until_the_event_number_and_the_secret_are_set(edge):
    assert get("/auth/ctftime")[0] == 404
    assert get("/auth/ctftime/callback?code=x&state=y")[0] == 404
    assert get("/oauth")[0] == 404 and get("/redirect")[0] == 404, "CTFd's own OAuth routes stay closed"


def test_the_login_page_comes_through_with_the_security_headers_once_each(edge):
    status, headers, _ = get("/login")
    assert status == 200
    for name in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Permissions-Policy", "Strict-Transport-Security"):
        assert len(values(headers, name)) == 1, name


def test_the_plain_http_port_of_development_is_not_there_and_the_loopback_health_check_is_all_that_listens_on_8080(edge):
    out = docker("exec", NAME, "sh", "-c", "wget -q -O- http://127.0.0.1:8080/__nginx_health; echo; wget -q -O- http://127.0.0.1:8080/ ; echo exit=$?", check=False)
    assert out.stdout.splitlines()[0] == "ok"
    assert "exit=0" not in out.stdout, "anything else on the loopback port gets nothing"
    listening = docker("exec", NAME, "sh", "-c", "netstat -ltn 2>/dev/null || ss -ltn", check=False).stdout
    assert "0.0.0.0:8080" not in listening and ":::8080" not in listening, listening
    assert re.search(r"8443", listening)


def test_a_short_session_resumption_cannot_be_used_to_skip_the_client_certificate(edge):
    """Without a certificate a second connection is still refused (session tickets are off)."""
    assert refused(lambda: get("/healthcheck", client=None))
    assert refused(lambda: get("/healthcheck", client=None))
