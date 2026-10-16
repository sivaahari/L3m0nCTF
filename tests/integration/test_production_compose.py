"""The production compose file, read the way docker compose reads it (no stack is started): base + production.

What it must say: only nginx is published, on 443 and nowhere else; the development administrator token is given to nobody; the session cookie is
Secure; CTFd answers for the crew's host name as well; the mail settings and the two later secrets (CTFtime's and the mail password) are wired; the
production nginx uses the production template and the TLS folder, with a health check that needs no plain HTTP port.

Needs docker compose and the generated settings (python -m l3mon config render ...) and the secrets (python -m l3mon secrets generate ...), as the other
integration tests do. The development file is read too, to show the two differ in the ways they should.
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

import test_stack as t

pytestmark = pytest.mark.integration

EXTRA = {"EXTRA_HOST": "crew.l3m0nctf.xyz", "MAIL_SERVER": "smtp.example.org", "MAIL_PORT": "587", "MAIL_USEAUTH": "true", "MAIL_USERNAME": "mailer",
         "MAIL_TLS": "true", "MAILFROM_ADDR": "no-reply@l3m0nctf.xyz", "TLS_DIR": "/etc/l3mon/tls"}


def compose_config(environment: str, extra: dict | None = None) -> dict:
    env = {**os.environ, "L3MON_ENV": environment, **(extra or {})}
    out = subprocess.run([t.BASH, str(t.ROOT / "tools" / "compose.sh"), "config", "--format", "json"], capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr[-600:]
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def production():
    return compose_config("production", EXTRA)


@pytest.fixture(scope="module")
def development():
    return compose_config("dev")


def published(service: dict) -> list[tuple[str, str, str]]:
    return [(p.get("host_ip", ""), str(p.get("published", "")), str(p["target"])) for p in service.get("ports", [])]


def secrets_of(service: dict) -> set[str]:
    return {s["source"] if isinstance(s, dict) else s for s in service.get("secrets", [])}


def test_only_nginx_is_published_and_only_on_443(production):
    assert published(production["services"]["nginx"]) == [("", "443", "8443")]
    for name in ("ctfd", "db", "cache"):
        assert published(production["services"][name]) == [], name


def test_development_is_published_on_the_loopback_address_only_and_the_two_differ(development, production):
    ports = published(development["services"]["nginx"])
    assert ports and all(host == "127.0.0.1" for host, _, _ in ports)
    assert production["services"]["nginx"]["ports"] != development["services"]["nginx"]["ports"]


def test_nobody_is_given_the_development_administrator_token(production, development):
    for name, service in production["services"].items():
        assert "PRESET_ADMIN_TOKEN" not in secrets_of(service), name
    assert "PRESET_ADMIN_TOKEN" in secrets_of(development["services"]["ctfd"]), "the development file still has it (that is what the tests sign in with)"


def test_ctfd_gets_the_secure_cookie_the_crews_host_name_and_the_mail_settings(production):
    env = production["services"]["ctfd"]["environment"]
    assert env["L3MON_SECURE_COOKIES"] == "true"
    assert env["TRUSTED_HOSTS"] == "play.l3m0nctf.xyz,crew.l3m0nctf.xyz"
    assert env["MAIL_SERVER"] == "smtp.example.org" and env["MAIL_PORT"] == "587" and env["MAIL_USEAUTH"] == "true"
    assert env["MAIL_USERNAME"] == "mailer" and env["MAIL_TLS"] == "true" and env["MAILFROM_ADDR"] == "no-reply@l3m0nctf.xyz"
    assert "MAIL_PASSWORD" not in env, "the password is a Docker secret"
    assert not any("PASSWORD" in key or "SECRET" in key for key in env if not key.endswith("_FILE") and key not in ("MAIL_PASSWORD",)), "no secret value in the environment"


def test_the_ctftime_secret_and_the_mail_password_are_docker_secrets_of_ctfd_only(production):
    assert {"CTFTIME_CLIENT_SECRET", "MAIL_PASSWORD", "SECRET_KEY", "DATABASE_PASSWORD"} <= secrets_of(production["services"]["ctfd"])
    for name in ("nginx", "db", "cache"):
        assert not ({"CTFTIME_CLIENT_SECRET", "MAIL_PASSWORD"} & secrets_of(production["services"][name])), name
    for name in ("CTFTIME_CLIENT_SECRET", "MAIL_PASSWORD"):
        assert production["secrets"][name]["file"].replace("\\", "/").endswith(f".secrets/{name}")


def test_without_the_extra_settings_the_mail_lines_are_empty_and_the_crews_name_is_the_platforms(tmp_path):
    env = compose_config("production")["services"]["ctfd"]["environment"]
    assert env["MAIL_SERVER"] == "" and env["MAILFROM_ADDR"] == ""
    assert env["TRUSTED_HOSTS"].split(",")[0] == env["TRUSTED_HOSTS"].split(",")[1]


def test_nginx_uses_the_production_template_the_tls_folder_and_a_health_check_that_needs_no_public_port(production):
    nginx = production["services"]["nginx"]
    volumes = {v["target"]: v for v in nginx["volumes"]}
    assert volumes["/etc/nginx/templates"]["source"].replace("\\", "/").endswith("deploy/nginx/templates-production")
    assert volumes["/etc/nginx/tls"]["source"].replace("\\", "/").endswith("/etc/l3mon/tls") and volumes["/etc/nginx/tls"]["read_only"] is True
    assert volumes["/etc/nginx/templates"]["read_only"] is True
    test = nginx["healthcheck"]["test"]
    assert "http://127.0.0.1:8080/__nginx_health" in test and not any("Host:" in part for part in test)


def test_the_production_nginx_still_has_every_hardening_the_base_file_gives_it(production):
    nginx = production["services"]["nginx"]
    assert nginx["read_only"] is True and nginx["cap_drop"] == ["ALL"] and "no-new-privileges:true" in nginx["security_opt"]
    assert nginx["networks"].keys() == {"edge"}, "nginx is on the edge network only"
    assert production["services"]["db"]["networks"].keys() == {"internal"} and production["networks"]["internal"]["internal"] is True
