"""The event settings file: validation, and the settings CTFd is started with."""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from l3mon import config  # noqa: E402

EXAMPLE = ROOT / "config" / "event.example.toml"


def example() -> dict:
    return config.load(EXAMPLE)


def epoch(y, mo, d, h, mi) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp())


def test_the_public_example_is_valid():
    assert config.validate(example()) == []


def mutate(path: str, value) -> dict:
    data = copy.deepcopy(example())
    section, key = path.split(".")
    if value is config.REMOVE:
        del data[section][key]
    else:
        data[section][key] = value
    return data


@pytest.mark.parametrize(
    "path,value,needle",
    [
        ("event.name", config.REMOVE, "event.name"),
        ("event.name", "   ", "event.name"),
        ("event.theme", "Bad Theme!", "event.theme"),
        ("event.start", "2026-11-28T09:00:00+05:30", "UTC"),
        ("event.start", "28 November", "UTC"),
        ("event.end", "2026-11-27T03:30:00Z", "after"),
        ("event.platform_host", "play.example.org", "domain"),
        ("event.domain", "not a domain", "event.domain"),
        ("teams.mode", "squads", "teams.mode"),
        ("teams.size_max", 0, "teams.size_max"),
        ("teams.size_max", 9, "teams.size_max"),
        ("teams.size_max", True, "teams.size_max"),
        ("visibility.challenges", "everyone", "visibility.challenges"),
        ("visibility.scores", "hidden", "visibility.scores"),
        ("accounts.verify_emails", "yes", "accounts.verify_emails"),
        ("admin.name", "a", "admin.name"),
        ("admin.name", "has space", "admin.name"),
        ("admin.email", "not-an-email", "admin.email"),
        # `$` also matches before a final line break, which would put an empty line into the environment file; \Z does not
        ("event.theme", "l3mon" + chr(10), "event.theme"),
        ("event.domain", "example.org" + chr(10), "event.domain"),
        ("event.start", "2026-11-28T04:30:00Z" + chr(10), "UTC"),
        ("admin.name", "organiser" + chr(10), "admin.name"),
        ("admin.email", "organiser@example.org" + chr(10), "admin.email"),
    ],
)
def test_validate_rejects(path, value, needle):
    errors = config.validate(mutate(path, value))
    assert errors, f"{path}={value!r} should be rejected"
    assert any(needle in e for e in errors), errors


def test_a_missing_section_is_reported_once_per_section():
    data = example()
    del data["visibility"]
    errors = config.validate(data)
    assert any("[visibility]" in e for e in errors)


def test_preset_configs_are_the_keys_ctfd_reads():
    preset = config.preset_configs(example())
    assert preset == {
        "setup": True,
        "ctf_name": "L3m0nCTF 2026",
        "ctf_description": "The capture-the-flag contest of Amrita Vishwa Vidyapeetham, Coimbatore.",
        "ctf_theme": "l3mon",
        "user_mode": "teams",
        "team_size": 4,
        "start": epoch(2026, 11, 28, 4, 30),
        "end": epoch(2026, 11, 28, 16, 30),
        "challenge_visibility": "private",
        "account_visibility": "private",
        "score_visibility": "public",
        "registration_visibility": "public",
        "verify_emails": True,
        "view_after_ctf": True,
        "view_self_submissions": False,
    }


def test_preset_configs_refuses_an_invalid_file():
    with pytest.raises(ValueError):
        config.preset_configs(mutate("teams.mode", "squads"))


def test_the_start_and_end_are_exactly_12_hours_apart_in_the_example():
    preset = config.preset_configs(example())
    assert preset["end"] - preset["start"] == 12 * 3600


def test_render_writes_one_line_of_json(tmp_path):
    out = config.render(example(), tmp_path)
    text = out["preset_configs.json"].read_text(encoding="utf-8")
    assert "\n" not in text.strip()
    assert json.loads(text)["ctf_name"] == "L3m0nCTF 2026"


def test_render_writes_the_compose_environment_without_any_secret(tmp_path):
    out = config.render(example(), tmp_path)
    env = out["compose.env"].read_text(encoding="utf-8")
    values = dict(line.split("=", 1) for line in env.splitlines() if line and not line.startswith("#"))
    assert values == {
        "PLATFORM_HOST": "play.l3m0nctf.xyz",
        "PRESET_ADMIN_NAME": "organiser",
        "PRESET_ADMIN_EMAIL": "organiser@l3m0nctf.xyz",
        "CTFTIME_CLIENT_ID": "",
    }
    assert "PASSWORD" not in env and "TOKEN" not in env and "SECRET" not in env


def test_render_output_is_readable_by_the_container_user_whatever_the_umask(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits do not apply on Windows")
    import os

    old = os.umask(0o077)
    try:
        made = config.render(example(), tmp_path / "out")
    finally:
        os.umask(old)
    assert (made["preset_configs.json"].stat().st_mode & 0o044) == 0o044


def test_render_refuses_an_invalid_file(tmp_path):
    with pytest.raises(ValueError):
        config.render(mutate("teams.mode", "squads"), tmp_path)


def test_the_ctftime_event_number_is_optional_digits_in_quotes_and_goes_to_the_compose_environment(tmp_path):
    for good in ("", "3456", "999999999"):
        data = copy.deepcopy(example())
        data["ctftime"] = {"client_id": good}
        assert config.validate(data) == [], good
    for bad in (3456, "abc", "12 34", "1234567890", " 5", None, True):
        data = copy.deepcopy(example())
        data["ctftime"] = {"client_id": bad}
        assert any("ctftime.client_id" in e for e in config.validate(data)), bad
    data = copy.deepcopy(example())
    data["ctftime"] = "yes"
    assert any("[ctftime]" in e for e in config.validate(data))
    del data["ctftime"]
    assert config.validate(data) == [], "the whole section may be left out"
    data["ctftime"] = {"client_id": "3456"}
    env = config.render(data, tmp_path)["compose.env"].read_text(encoding="utf-8")
    assert "CTFTIME_CLIENT_ID=3456" in env.splitlines() and "SECRET" not in env



# ---- the optional [mail] section: how the platform sends the verification and password-reset messages --------------------------------------------

def with_mail(**over):
    data = copy.deepcopy(example())
    data["mail"] = {"host": "smtp.example.org", "port": 587, "username": "mailer@l3m0nctf.xyz", "from_addr": "no-reply@l3m0nctf.xyz", "security": "starttls", **over}
    return data


def test_the_mail_section_is_optional_and_a_good_one_is_valid():
    assert config.validate(example()) == []
    assert config.validate(with_mail()) == []


@pytest.mark.parametrize("over", [
    {"host": "not a host"}, {"host": "smtp.example.org" + chr(10)}, {"host": ""}, {"port": 0}, {"port": 70000}, {"port": "587"}, {"port": True},
    {"username": "has space"}, {"username": "a" * 200}, {"from_addr": "nobody"}, {"from_addr": "no-reply@l3m0nctf.xyz" + chr(10)},
    {"security": "plain"}, {"security": True},
])
def test_a_bad_mail_setting_is_refused(over):
    errors = config.validate(with_mail(**over))
    assert errors and any("mail." in e for e in errors), (over, errors)


def test_the_mail_section_must_be_a_table_and_may_not_carry_a_password_or_unknown_keys():
    data = copy.deepcopy(example())
    data["mail"] = "smtp.example.org"
    assert any("[mail]" in e for e in config.validate(data))
    for key in ("password", "api_key", "token", "secret"):
        errors = config.validate(with_mail(**{key: "x"}))
        assert any(f"mail.{key}" in e and "secret" in e for e in errors), key


def test_the_mail_settings_reach_the_compose_environment_in_the_names_ctfd_reads(tmp_path):
    out = config.render(with_mail(), tmp_path)["compose.env"].read_text(encoding="utf-8")
    lines = dict(line.split("=", 1) for line in out.splitlines() if "=" in line and not line.startswith("#"))
    assert lines["MAIL_SERVER"] == "smtp.example.org" and lines["MAIL_PORT"] == "587"
    assert lines["MAIL_USEAUTH"] == "true" and lines["MAIL_USERNAME"] == "mailer@l3m0nctf.xyz"
    assert lines["MAIL_TLS"] == "true" and lines["MAIL_SSL"] == "" and lines["MAILFROM_ADDR"] == "no-reply@l3m0nctf.xyz"
    assert "MAIL_PASSWORD" not in out, "the password is a Docker secret, never in this file"


def test_implicit_tls_and_no_login_are_written_as_ctfd_expects(tmp_path):
    ssl = config.render(with_mail(port=465, security="ssl", username=""), tmp_path)["compose.env"].read_text(encoding="utf-8")
    assert "MAIL_SSL=true" in ssl and "MAIL_TLS=" in ssl and "MAIL_TLS=true" not in ssl and "MAIL_USEAUTH=" in ssl and "MAIL_USEAUTH=true" not in ssl


def test_without_a_mail_section_no_mail_line_is_written(tmp_path):
    out = config.render(example(), tmp_path)["compose.env"].read_text(encoding="utf-8")
    assert "MAIL_" not in out and "MAILFROM" not in out
