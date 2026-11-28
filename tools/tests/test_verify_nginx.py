"""The verification portal's front door: the `verify` overlay, its nginx block, and real requests through the pinned nginx.

The block (deploy/nginx/verify/verify.conf) serves only the static website `python -m l3mon verify build` writes: read-only, GET and HEAD only, a
strict Content-Security-Policy, Cloudflare's client certificate required exactly as for the platform, and nothing but the routes it names.
The compose tests render the real files with `docker compose config` (client side). The nginx tests start the pinned nginx image, serve a real build
and talk to it over TLS with a client certificate; they skip when Docker cannot run (CI has it).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from l3mon import deploy
from l3mon.verify import codes, issue, site, store

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_DIR = ROOT / "deploy" / "compose"
VERIFY_CONF = ROOT / "deploy" / "nginx" / "verify" / "verify.conf"
HEADERS = ROOT / "deploy" / "nginx" / "snippets" / "verify-headers.conf"
PROD_TEMPLATE = ROOT / "deploy" / "nginx" / "templates-production" / "platform.conf.template"
DEV_TEMPLATE = ROOT / "deploy" / "nginx" / "templates" / "platform.conf.template"
NGINX_CONF = ROOT / "deploy" / "nginx" / "nginx.conf"


def text(path: Path) -> str:
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))


def tls_lines(block: str) -> list[str]:
    return sorted(re.sub(r"\s+", " ", line.strip()) for line in block.splitlines() if re.match(r"\s*ssl_", line))


def docker_works() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def compose_works() -> bool:
    return shutil.which("docker") is not None and subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=60).returncode == 0


def render(overlays: list[str], extra_env: dict | None = None) -> subprocess.CompletedProcess:
    files = ["-f", str(COMPOSE_DIR / "compose.base.yml"), "-f", str(COMPOSE_DIR / "compose.production.yml")]
    for overlay in overlays:
        files += ["-f", str(COMPOSE_DIR / f"compose.{overlay}.yml")]
    env = {k: v for k, v in os.environ.items() if not k.startswith("L3MON_")}
    env.update(extra_env or {})
    return subprocess.run(["docker", "compose", *files, "config", "--format", "json"], cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=90)


# ------------------------------------------------------------------ the overlay

@pytest.mark.skipif(not compose_works(), reason="needs the docker compose client")
def test_the_overlay_adds_no_container_and_mounts_the_block_and_the_site_read_only(tmp_path):
    res = render(["verify"], {"L3MON_VERIFY_SITE_DIR": str(tmp_path)})
    assert res.returncode == 0, res.stderr
    cfg = json.loads(res.stdout)
    base = json.loads(render([]).stdout)
    assert set(cfg["services"]) == set(base["services"]), "the portal is files served by the existing nginx: no new service"
    mounts = {v["target"]: v for v in cfg["services"]["nginx"]["volumes"]}
    block, site_mount = mounts["/etc/nginx/verify.d/verify.conf"], mounts["/srv/verify"]
    assert block["read_only"] and site_mount["read_only"]
    assert Path(block["source"]).name == "verify.conf" and Path(site_mount["source"]) == tmp_path
    for svc, conf in cfg["services"].items():
        if svc != "nginx":
            assert "/srv/verify" not in {v.get("target") for v in conf.get("volumes", [])}, f"{svc} must not see the portal's files"


@pytest.mark.skipif(not compose_works(), reason="needs the docker compose client")
def test_the_overlay_fails_loudly_without_the_site_folder():
    res = render(["verify"])
    assert res.returncode != 0 and "L3MON_VERIFY_SITE_DIR" in (res.stderr + res.stdout)


@pytest.mark.skipif(not compose_works(), reason="needs the docker compose client")
def test_without_the_overlay_nginx_gets_no_portal():
    cfg = json.loads(render([]).stdout)
    assert "/srv/verify" not in {v.get("target") for v in cfg["services"]["nginx"]["volumes"]}


# ------------------------------------------------------------------ the block itself

def test_the_block_uses_the_same_tls_settings_as_the_platform_block():
    platform = text(PROD_TEMPLATE).split("server_name ${PLATFORM_HOST}", 1)[1].split("include /etc/nginx/snippets/platform-server.conf;", 1)[0]
    assert tls_lines(platform) == tls_lines(text(VERIFY_CONF)), "the portal must check Cloudflare's client certificate exactly like the platform"


def test_only_the_production_template_has_the_optional_include():
    assert "include /etc/nginx/verify.d/*.conf;" in text(PROD_TEMPLATE)
    assert "verify.d" not in text(DEV_TEMPLATE)


def test_the_block_answers_only_verify_hosts_and_never_proxies():
    conf = text(VERIFY_CONF)
    assert re.findall(r"server_name\s+([^;]+);", conf) == ['"~^verify\\.l3m0nctf\\.xyz$"']
    for word in ("proxy_pass", "fastcgi_pass", "uwsgi_pass", "autoindex on", "alias "):
        assert word not in conf, f"{word} has no place in a static, read-only block"
    assert "root /srv/verify;" in conf


def test_the_code_routes_accept_exactly_the_codes_the_tool_makes():
    conf = text(VERIFY_CONF)
    patterns = re.findall(r'"\^/?\((L3M0N26-[^"]+)\)\$"', conf)
    assert patterns == [codes.ROUTE, codes.ROUTE], patterns
    route = re.compile(f"^{codes.ROUTE}$")
    for code in ("L3M0N26-ORG-A1O0E", "L3M0N26-ORG-77777", "L3M0N26-PAR-BCDFG", "L3M0N26-APP-ZZZZZ"):
        assert route.match(code) and codes.CODE_RE.match(code), code
    for code in ("L3M0N26-PAR-A1O0E", "L3M0N26-ACH-BCDFA", "L3M0N26-ORG-a1o0e", "L3M0N26-ORG-A1O0", "L3M0N26-ORG-A1O0EX"):
        assert not route.match(code) and not codes.CODE_RE.match(code), code


def test_the_pdf_route_uses_the_same_code_rule_and_the_builds_address():
    conf = text(VERIFY_CONF)
    assert re.findall(r'"\^/\(\?<cert_code>(L3M0N26-[^"]+)\)/certificate\\.pdf\$"', conf) == [codes.ROUTE]
    assert site.pdf_path("L3M0N26-PAR-BCDFG") == "/L3M0N26-PAR-BCDFG/certificate.pdf"
    block = re.search(r'certificate\\.pdf\$" \{(.*?)\n  \}', conf, re.S).group(1)
    assert "try_files /c/$cert_code.pdf =404;" in block and "default_type application/pdf;" in block and "types { }" in block
    assert "$1" not in block, "a numbered capture is overwritten by the $hsts map before the headers are made"


def test_every_location_sets_the_portal_headers():
    conf = text(VERIFY_CONF)
    for block in re.findall(r"location ([^{]+)\{(.*?)\n  \}", conf, re.S):
        loc, body = block
        if "return" in body and "try_files" not in body:
            continue  # a redirect or a 404 handed to /404.html, which has its own headers
        assert "include /etc/nginx/snippets/verify-headers.conf;" in body, f"location {loc.strip()} would drop the security headers"


def test_the_csp_is_strict_and_the_zone_exists():
    headers = text(HEADERS)
    csp = re.search(r'Content-Security-Policy "([^"]+)"', headers).group(1)
    assert "default-src 'none'" in csp and "script-src 'self'" in csp and "style-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe" not in csp
    assert "include /etc/nginx/snippets/security-headers.conf;" in headers
    assert re.search(r"limit_req_zone \$limit_addr\s+zone=verify:\d+m\s+rate=\d+r/m;", text(NGINX_CONF))
    assert "limit_req zone=verify" in text(VERIFY_CONF)


# ------------------------------------------------------------------ the pre-deploy check

def make_build(tmp_path) -> Path:
    db = tmp_path / "c.sqlite"
    conn = store.connect(db, create=True)
    issue.issue(conn, issue_date="2026-11-30", org=["Jane Doe"])
    conn.close()
    out = tmp_path / "site"
    site.build(db, out, base_url="https://verify.example.test")
    return out


def portal_items(env):
    items: list = []
    deploy.check_verify_site(env, items)
    return [(i.status, i.name) for i in items]


def test_the_deploy_check_says_nothing_without_the_overlay():
    assert portal_items({"L3MON_OVERLAYS": "identity,ops"}) == []


def test_the_deploy_check_blocks_a_missing_or_unfinished_site(tmp_path):
    assert portal_items({"L3MON_OVERLAYS": "ops,verify"}) == [(deploy.BLOCKED, "verification portal")]
    assert portal_items({"L3MON_OVERLAYS": "verify", "L3MON_VERIFY_SITE_DIR": str(tmp_path)}) == [(deploy.BLOCKED, "verification portal")]


def test_the_deploy_check_passes_a_real_build(tmp_path):
    out = make_build(tmp_path)
    assert portal_items({"L3MON_OVERLAYS": "verify", "L3MON_VERIFY_SITE_DIR": str(out)}) == [(deploy.OK, "verification portal")]


# ------------------------------------------------------------------ real requests through the pinned nginx

def nginx_image() -> str:
    return re.search(r"image:\s*(nginxinc/nginx-unprivileged:\S+)", (COMPOSE_DIR / "compose.base.yml").read_text(encoding="utf-8")).group(1)


def request(port: int, path: str, *, method: str = "GET", host: str = "verify.l3m0nctf.xyz", cert: tuple | None = None) -> tuple[int, dict, bytes]:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if cert:
        ctx.load_cert_chain(*cert)
    with socket.create_connection(("127.0.0.1", port), timeout=10) as raw, ctx.wrap_socket(raw, server_hostname=host) as s:
        s.sendall(f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode())
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        headers.setdefault(k.strip().lower(), v.strip())
    return int(lines[0].split()[1]), headers, body


@pytest.fixture(scope="module")
def served():
    if not docker_works() or shutil.which("openssl") is None:
        pytest.skip("needs a running Docker and openssl (CI has both)")
    tmp = Path(tempfile.mkdtemp())
    try:
        run = lambda *a: subprocess.run(a, check=True, capture_output=True, cwd=tmp)  # noqa: E731
        (tmp / "tls").mkdir()
        # the server's certificate, and a stand-in for Cloudflare's origin-pull CA with one client certificate it signed
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "tls/origin.key", "-out", "tls/origin.pem", "-days", "1", "-subj", "/CN=origin")
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "tls/cloudflare-origin-pull-ca.pem", "-days", "1", "-subj", "/CN=pull-ca")
        run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "client.key", "-out", "client.csr", "-subj", "/CN=cloudflare")
        run("openssl", "x509", "-req", "-in", "client.csr", "-CA", "tls/cloudflare-origin-pull-ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-out", "client.pem", "-days", "1")
        (tmp / "tls").chmod(0o755)
        for item in (tmp / "tls").iterdir():
            item.chmod(0o644)
        # a real build with one certificate of each kind
        db = tmp / "c.sqlite"
        conn = store.connect(db, create=True)
        (tmp / "p.csv").write_text("user_id,username,studio\n11,ctf.username,Test\n12,neo.sample,Test\n", encoding="utf-8")
        issue.issue(conn, issue_date="2026-11-30", org=["Jane Doe", "Alex Example", ("Pat Organiser", "L3M0N26-ORG-A1O0E")],
                    prelims=issue.read_prelims(tmp / "p.csv"))
        withdrawn = next(c for c in store.all_rows(conn) if c.name == "Alex Example").verify_code
        store.withdraw(conn, withdrawn, "test")
        valid = next(c for c in store.all_rows(conn) if c.category == "PAR").verify_code
        conn.close()
        site.build(db, tmp / "site", base_url="https://verify.example.test")
        # a stand-in for a printed certificate (the browser is tested in test_verify_certificate.py): nginx only has to serve the file
        (tmp / "site" / "c" / f"{valid}.pdf").write_bytes(b"%PDF-1.4 a stand-in certificate\n%%EOF\n")
        for p in [tmp / "site", *(tmp / "site").rglob("*")]:
            p.chmod(0o755 if p.is_dir() else 0o644)
        rendered = PROD_TEMPLATE.read_text(encoding="utf-8").replace("${PLATFORM_HOST}", "play.example.test").replace("${EXTRA_HOST}", "crew.example.test")
        (tmp / "default.conf").write_text(rendered, encoding="utf-8")
        name = f"l3mon-verify-test-{os.getpid()}"
        res = subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::8443", "--add-host", "ctfd:127.0.0.1",
             "-v", f"{NGINX_CONF}:/etc/nginx/nginx.conf:ro", "-v", f"{ROOT / 'deploy' / 'nginx' / 'snippets'}:/etc/nginx/snippets:ro",
             "-v", f"{tmp / 'default.conf'}:/etc/nginx/conf.d/default.conf:ro", "-v", f"{tmp / 'tls'}:/etc/nginx/tls:ro",
             "-v", f"{VERIFY_CONF}:/etc/nginx/verify.d/verify.conf:ro", "-v", f"{tmp / 'site'}:/srv/verify:ro", nginx_image()],
            capture_output=True, text=True, timeout=180)
        assert res.returncode == 0, res.stderr
        try:
            port = int(subprocess.run(["docker", "port", name, "8443/tcp"], capture_output=True, text=True, check=True).stdout.strip().rsplit(":", 1)[1])
            cert = (str(tmp / "client.pem"), str(tmp / "client.key"))
            for _ in range(60):
                try:
                    request(port, "/", cert=cert)
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True).stderr
                pytest.fail(f"nginx did not answer: {logs}")
            yield {"port": port, "cert": cert, "valid": valid, "withdrawn": withdrawn, "fixed": "L3M0N26-ORG-A1O0E"}
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_certificate_page_is_served_with_the_strict_headers(served):
    status, h, body = request(served["port"], f"/{served['valid']}", cert=served["cert"])
    assert status == 200 and served["valid"].encode() in body and b"Certificate of Participation" in body
    assert "default-src 'none'" in h["content-security-policy"] and h["x-robots-tag"] == "noindex, nofollow"
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert h["content-type"].startswith("text/html")


def test_a_certificates_pdf_is_served_as_a_pdf_with_the_strict_headers_and_only_for_its_code(served):
    status, h, body = request(served["port"], f"/{served['valid']}/certificate.pdf", cert=served["cert"])
    assert status == 200 and body.startswith(b"%PDF-1.4 a stand-in certificate")
    assert h["content-type"] == "application/pdf" and h["x-content-type-options"] == "nosniff"
    assert h["content-disposition"] == f'inline; filename="L3m0n-CTF-2026-{served["valid"]}.pdf"'
    assert "default-src 'none'" in h["content-security-policy"] and h["x-robots-tag"] == "noindex, nofollow"
    for path in (f"/{served['withdrawn']}/certificate.pdf", f"/{served['fixed']}/certificate.pdf", f"/c/{served['valid']}.pdf",
                 f"/{served['valid'].lower()}/certificate.pdf", f"/{served['valid']}/certificate.pdf/", f"/{served['valid']}/../c/{served['valid']}.pdf"):
        status, h, _ = request(served["port"], path, cert=served["cert"])
        assert status == 404 and h["content-type"].startswith("text/html"), path


def test_the_lookup_page_and_the_assets(served):
    status, h, body = request(served["port"], "/", cert=served["cert"])
    assert status == 200 and b"Check a certificate" in body and "content-security-policy" in h
    css = re.search(rb'href="(/assets/site\.[0-9a-f]+\.css)"', body).group(1).decode()
    status, h, _ = request(served["port"], css, cert=served["cert"])
    assert status == 200 and h["content-type"].startswith("text/css") and "immutable" in h["cache-control"]


@pytest.mark.parametrize("path", ["/L3M0N26-PAR-BBBBB", "/l3m0n26-par-bbbbb", "/c/x.html", "/build.json", "/index.html", "/assets/", "/../etc/passwd", "/admin"])
def test_anything_else_is_no_signal_with_a_404(served, path):
    status, h, body = request(served["port"], path, cert=served["cert"])
    assert status in (400, 404), (path, status)
    if status == 404:
        assert b"No certificate found" in body and "content-security-policy" in h


def test_a_permanent_organiser_code_with_vowels_and_digits_is_served_and_found_by_the_lookup(served):
    status, _, body = request(served["port"], f"/{served['fixed']}", cert=served["cert"])
    assert status == 200 and b"Pat Organiser" in body and b"Certificate of Recognition" in body
    status, h, _ = request(served["port"], f"/check?code={served['fixed']}", cert=served["cert"])
    assert status == 302 and h["location"] == f"/{served['fixed']}"
    for wrong in ("/L3M0N26-ORG-A1OOE", "/L3M0N26-PAR-A1O0E"):  # a look-alike is not the code; a vowel or digit is never a qualifier code
        assert request(served["port"], wrong, cert=served["cert"])[0] == 404, wrong


def test_the_raw_files_are_not_reachable_by_their_folder_path(served):
    status, _, body = request(served["port"], f"/c/{served['valid']}.html", cert=served["cert"])
    assert status == 404 and b"No certificate found" in body


def test_a_withdrawn_code_answers_with_its_notice(served):
    status, _, body = request(served["port"], f"/{served['withdrawn']}", cert=served["cert"])
    assert status == 200 and b"withdrawn" in body and b"Alex Example" not in body


def test_the_no_script_lookup_redirects_a_code_typed_as_printed(served):
    status, h, _ = request(served["port"], f"/check?code={served['valid']}", cert=served["cert"])
    assert status == 302 and h["location"] == f"/{served['valid']}", "relative: an absolute one would carry nginx's own port 8443"
    assert "default-src 'none'" in h["content-security-policy"]
    status, _, _ = request(served["port"], "/check?code=nonsense", cert=served["cert"])
    assert status == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_only_reading_is_allowed(served, method):
    status, h, _ = request(served["port"], "/", method=method, cert=served["cert"])
    assert status == 405 and "default-src 'none'" in h["content-security-policy"]


def test_the_server_level_headers_and_relative_redirects_are_set():
    conf = text(VERIFY_CONF)
    server_level = conf.split("location", 1)[0]
    assert "include /etc/nginx/snippets/verify-headers.conf;" in server_level
    assert "absolute_redirect off;" in server_level


def test_without_cloudflares_client_certificate_there_is_no_page(served):
    try:
        status, _, body = request(served["port"], f"/{served['valid']}")
    except (ssl.SSLError, ConnectionError, OSError):
        return  # refused in the handshake
    assert status == 400 and served["valid"].encode() not in body


@pytest.mark.parametrize("host", ["evil.example.test", "verify.example.test", "verify.l3m0nctf.xyz.evil.example.test"])
def test_another_host_name_gets_nothing(served, host):
    # Cloudflare's origin-pull certificate is shared by all its customers: only our exact name may reach the portal
    with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
        request(served["port"], "/", host=host, cert=served["cert"])


def test_a_flood_gets_the_wait_page_never_a_cached_error(served):
    css = re.search(rb'href="(/assets/site\.[0-9a-f]+\.css)"', request(served["port"], "/", cert=served["cert"])[2]).group(1).decode()
    statuses = []
    for _ in range(45):
        status, h, body = request(served["port"], css, cert=served["cert"])
        statuses.append(status)
        if status == 429:
            assert h["cache-control"] == "no-store", "a 429 must never carry the stylesheet's one-year cache"
            assert "default-src 'none'" in h["content-security-policy"] and h.get("retry-after") == "60"
            assert b"Too many checks" in body
            break
    assert 429 in statuses, statuses
    time.sleep(2)
