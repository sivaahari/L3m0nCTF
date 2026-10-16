"""`python -m l3mon deploy check`: the production stack must not start with a missing secret, a leftover development token, the example list of
organisers' addresses, a missing certificate or a stale generated file, and must say plainly what each one is."""
from __future__ import annotations

import os
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l3mon import config, deploy, secrets_gen  # noqa: E402

CERT = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"
KEY = "-----BEGIN PRIVATE KEY-----\nMIIBfake\n-----END PRIVATE KEY-----\n"
TODAY = date(2026, 10, 20)


def make_deployment(tmp_path: Path, mail=True, ctftime=False) -> dict:
    """A repository folder that is ready for production, and the paths the check takes."""
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "deploy" / "compose").mkdir(parents=True)
    (root / "deploy" / "nginx" / "snippets").mkdir(parents=True)
    shutil.copy(ROOT / "deploy" / "nginx" / "snippets" / "cloudflare-realip.conf", root / "deploy" / "nginx" / "snippets" / "cloudflare-realip.conf")
    (root / "deploy" / "nginx" / "snippets" / "admin-nets.conf").write_text("127.0.0.1 1;\n", encoding="utf-8")
    data = config.load(ROOT / "config" / "event.example.toml")
    if mail:
        data["mail"] = {"host": "smtp.example.org", "port": 587, "username": "mailer", "from_addr": "no-reply@l3m0nctf.xyz", "security": "starttls"}
    if ctftime:
        data["ctftime"] = {"client_id": "1234"}
    event = root / "config" / "event.toml"
    event.write_text(_toml(data), encoding="utf-8")
    config.render(data, root / "deploy" / "compose" / "generated")
    secrets_dir = root / ".secrets"
    secrets_gen.generate(secrets_dir)
    (secrets_dir / "PRESET_ADMIN_TOKEN").unlink()
    if mail:
        (secrets_dir / "MAIL_PASSWORD").write_text("the-mail-password\n", encoding="utf-8")
    if ctftime:
        (secrets_dir / "CTFTIME_CLIENT_SECRET").write_text("the-ctftime-secret\n", encoding="utf-8")
    tls = tmp_path / "tls"
    tls.mkdir()
    (tls / "origin.pem").write_text(CERT, encoding="utf-8")
    (tls / "origin.key").write_text(KEY, encoding="utf-8")
    (tls / "cloudflare-origin-pull-ca.pem").write_text(CERT, encoding="utf-8")
    if os.name == "posix":
        (tls / "origin.key").chmod(0o640)
        secrets_dir.chmod(0o700)
    nets = tmp_path / "admin-nets.conf"
    nets.write_text("# the crew\n203.0.113.0/24 1;\n" if False else "# the crew\n100.64.9.9 1;\n2a02:6b8::/48 1;\n", encoding="utf-8")
    return {"root": root, "event_file": event, "secrets_dir": secrets_dir, "tls_dir": tls, "admin_nets": nets,
            "env": {"L3MON_ENV": "production", "L3MON_IMAGE_TAG": "v1.0.0"}, "today": TODAY}


def _toml(data: dict) -> str:
    lines = []
    for section, values in data.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = " + (f'"{value}"' if isinstance(value, str) else str(value).lower()))
        lines.append("")
    return "\n".join(lines)


def run(world: dict):
    return deploy.run_checks(world["root"], event_file=world["event_file"], secrets_dir=world["secrets_dir"], tls_dir=world["tls_dir"],
                             admin_nets=world["admin_nets"], env=world["env"], today=world["today"])


def blocked(items):
    return {i.name: i.detail for i in items if i.status == deploy.BLOCKED}


def statuses(items):
    return {i.name: i.status for i in items}


def test_a_ready_deployment_has_nothing_blocked_and_says_what_is_in_place(tmp_path):
    items = run(make_deployment(tmp_path, ctftime=True))
    assert blocked(items) == {}, blocked(items)
    got = statuses(items)
    assert got["event settings"] == got["secrets"] == got["TLS files"] == got["organisers' addresses"] == got["mail"] == got["Login with CTFtime"] == deploy.OK
    assert got["development administrator token"] == got["image tag"] == deploy.OK


def test_before_ctftime_approves_the_event_the_sign_in_is_a_todo_and_nothing_is_blocked(tmp_path):
    items = run(make_deployment(tmp_path))
    assert blocked(items) == {}
    assert statuses(items)["Login with CTFtime"] == deploy.TODO
    assert "docs/ctftime-endpoints.md" in next(i.detail for i in items if i.name == "Login with CTFtime")


def test_the_event_number_without_the_secret_is_blocked_and_the_secret_without_the_number_is_a_todo(tmp_path):
    world = make_deployment(tmp_path, ctftime=True)
    (world["secrets_dir"] / "CTFTIME_CLIENT_SECRET").write_text("", encoding="utf-8")
    assert "Login with CTFtime" in blocked(run(world))
    world = make_deployment(tmp_path / "b")
    (world["secrets_dir"] / "CTFTIME_CLIENT_SECRET").write_text("a-secret", encoding="utf-8")
    items = run(world)
    assert blocked(items) == {} and statuses(items)["Login with CTFtime"] == deploy.TODO


def test_a_leftover_development_token_is_blocked(tmp_path):
    world = make_deployment(tmp_path)
    (world["secrets_dir"] / "PRESET_ADMIN_TOKEN").write_text("ctfd_x", encoding="utf-8")
    assert "development administrator token" in blocked(run(world))


@pytest.mark.parametrize("name", [n for n in secrets_gen.MAKERS if n != "PRESET_ADMIN_TOKEN"])
def test_a_missing_or_empty_generated_secret_is_blocked_by_name(tmp_path, name):
    world = make_deployment(tmp_path)
    (world["secrets_dir"] / name).unlink()
    assert name in blocked(run(world)).get("secrets", "")
    (world["secrets_dir"] / name).write_text("", encoding="utf-8")
    assert name in blocked(run(world)).get("secrets", "")


def test_no_secrets_folder_at_all_is_blocked_with_the_command_to_make_it(tmp_path):
    world = make_deployment(tmp_path)
    shutil.rmtree(world["secrets_dir"])
    assert "secrets generate" in blocked(run(world))["secrets"]


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_a_secrets_folder_other_users_can_open_is_blocked(tmp_path):
    world = make_deployment(tmp_path)
    world["secrets_dir"].chmod(0o755)
    assert "secrets folder" in blocked(run(world))


@pytest.mark.parametrize("name", ["CTFTIME_CLIENT_SECRET", "MAIL_PASSWORD"])
def test_a_missing_placeholder_file_is_blocked_because_compose_needs_it(tmp_path, name):
    world = make_deployment(tmp_path, ctftime=True)
    (world["secrets_dir"] / name).unlink()
    assert blocked(run(world)), name


def test_a_mail_login_without_its_password_is_blocked_and_no_mail_section_is_a_todo_that_says_why(tmp_path):
    world = make_deployment(tmp_path)
    (world["secrets_dir"] / "MAIL_PASSWORD").write_text("", encoding="utf-8")
    assert "mail" in blocked(run(world))
    world = make_deployment(tmp_path / "b", mail=False)
    items = run(world)
    assert statuses(items)["mail"] == deploy.TODO and "nobody can finish registering" in next(i.detail for i in items if i.name == "mail")


def test_an_event_file_that_is_missing_or_invalid_is_blocked_and_stops_the_settings_comparison(tmp_path):
    world = make_deployment(tmp_path)
    world["event_file"].unlink()
    assert "event settings" in blocked(run(world))
    world = make_deployment(tmp_path / "b")
    world["event_file"].write_text(world["event_file"].read_text(encoding="utf-8").replace("size_max = 4", "size_max = 99"), encoding="utf-8")
    assert "size_max" in blocked(run(world))["event settings"]


def test_generated_settings_older_than_the_event_file_are_blocked_with_the_render_command(tmp_path):
    world = make_deployment(tmp_path)
    world["event_file"].write_text(world["event_file"].read_text(encoding="utf-8").replace("size_max = 4", "size_max = 3"), encoding="utf-8")
    detail = blocked(run(world))["generated settings"]
    assert "config render" in detail
    (world["root"] / "deploy" / "compose" / "generated" / "compose.env").unlink()
    assert "generated settings" in blocked(run(world))


@pytest.mark.parametrize("missing", ["origin.pem", "origin.key", "cloudflare-origin-pull-ca.pem"])
def test_a_missing_tls_file_is_blocked_by_name(tmp_path, missing):
    world = make_deployment(tmp_path)
    (world["tls_dir"] / missing).unlink()
    assert missing in blocked(run(world))["TLS files"]


def test_files_that_are_not_pem_are_blocked(tmp_path):
    world = make_deployment(tmp_path)
    (world["tls_dir"] / "origin.pem").write_text("not a certificate", encoding="utf-8")
    assert "TLS files" in blocked(run(world))
    world = make_deployment(tmp_path / "b")
    (world["tls_dir"] / "origin.key").write_text(CERT, encoding="utf-8")
    assert "private key" in blocked(run(world))["TLS files"]


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_a_key_every_user_can_read_is_blocked_but_group_read_is_the_documented_setting(tmp_path):
    world = make_deployment(tmp_path)
    (world["tls_dir"] / "origin.key").chmod(0o644)
    assert "TLS key" in blocked(run(world))
    (world["tls_dir"] / "origin.key").chmod(0o640)
    assert blocked(run(world)) == {}


@pytest.mark.parametrize("line,word", [
    ("10.0.0.0/8 1;", "private"), ("192.168.1.5 1;", "private"), ("172.16.0.0/12 1;", "private"), ("127.0.0.1 1;", "private"), ("169.254.1.1 1;", "private"),
    ("fe80::/10 1;", "private"), ("fd00::/8 1;", "private"), ("0.0.0.0/0 1;", "too wide"), ("::/0 1;", "too wide"), ("1.0.0.0/4 1;", "too wide"),
    ("300.1.1.1 1;", "not an address"),
])
def test_dangerous_organiser_lists_are_blocked(tmp_path, line, word):
    world = make_deployment(tmp_path)
    world["admin_nets"].write_text("100.64.9.9 1;\n" + line + "\n", encoding="utf-8")
    detail = blocked(run(world)).get("organisers' addresses", "")
    assert word in detail, (line, detail)


def test_the_example_list_the_development_list_a_missing_file_and_an_unset_variable_are_blocked(tmp_path):
    world = make_deployment(tmp_path)
    shutil.copy(ROOT / "deploy" / "nginx" / "snippets" / "admin-nets.production.example.conf", world["admin_nets"])
    assert "example" in blocked(run(world))["organisers' addresses"]
    world["admin_nets"] = world["root"] / "deploy" / "nginx" / "snippets" / "admin-nets.conf"
    assert "development list" in blocked(run(world))["organisers' addresses"]
    world["admin_nets"] = tmp_path / "nowhere.conf"
    assert "does not exist" in blocked(run(world))["organisers' addresses"]
    world["admin_nets"] = None
    assert "ADMIN_NETS_FILE" in blocked(run(world))["organisers' addresses"]
    world["admin_nets"] = tmp_path / "empty.conf"
    world["admin_nets"].write_text("# nobody\n", encoding="utf-8")
    assert "lists no address" in blocked(run(world))["organisers' addresses"]


def test_a_real_address_next_to_the_example_lines_passes_with_a_reminder_to_remove_them(tmp_path):
    world = make_deployment(tmp_path)
    world["admin_nets"].write_text("100.64.9.9 1;\n192.0.2.10 1;\n", encoding="utf-8")
    items = run(world)
    assert "organisers' addresses" not in blocked(items)
    assert "remove them" in next(i.detail for i in items if i.name == "organisers' addresses")


def test_an_old_cloudflare_address_list_is_a_todo_and_a_missing_one_is_blocked(tmp_path):
    world = make_deployment(tmp_path)
    world["today"] = date(2026, 12, 31)
    items = run(world)
    assert statuses(items)["Cloudflare address list"] == deploy.TODO and blocked(items) == {}
    (world["root"] / "deploy" / "nginx" / "snippets" / "cloudflare-realip.conf").unlink()
    assert "Cloudflare address list" in blocked(run(world))


def test_the_environment_lines_say_to_set_production_and_a_release_tag(tmp_path):
    world = make_deployment(tmp_path)
    world["env"] = {}
    got = statuses(run(world))
    assert got["environment"] == deploy.TODO and got["image tag"] == deploy.TODO


def test_no_line_ever_carries_a_secret_value(tmp_path):
    world = make_deployment(tmp_path, ctftime=True)
    text = "\n".join(i.human() for i in run(world))
    for name in ("the-mail-password", "the-ctftime-secret"):
        assert name not in text
    for path in world["secrets_dir"].iterdir():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            assert value not in text


def test_the_command_prints_the_lines_and_exits_one_only_when_something_is_blocked(tmp_path, capsys):
    from l3mon.cli import main

    world = make_deployment(tmp_path)
    args = ["deploy", "check", "--root", str(world["root"]), "--event", str(world["event_file"]), "--secrets", str(world["secrets_dir"]),
            "--tls", str(world["tls_dir"]), "--admin-nets", str(world["admin_nets"])]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "0 blocked" in out and "ok       event settings" in out
    (world["tls_dir"] / "origin.pem").unlink()
    assert main(args) == 1
    assert "BLOCKED  TLS files" in capsys.readouterr().out
