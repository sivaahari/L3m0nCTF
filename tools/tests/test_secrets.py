"""Generated secrets: random, long, private to the machine, never printed, never overwritten by accident."""
from __future__ import annotations

import re
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l3mon import secrets_gen  # noqa: E402

NAMES = {
    "SECRET_KEY", "DATABASE_PASSWORD", "DATABASE_ROOT_PASSWORD", "REDIS_PASSWORD",
    "PRESET_ADMIN_PASSWORD", "PRESET_ADMIN_TOKEN", "FLAG_HMAC_SECRET",
    # made for the pieces that bring their own services (overlays): the instancer and its router, the operations stack
    "INSTANCER_TOKEN", "ROUTER_TOKEN", "GRAFANA_ADMIN_PASSWORD", "DB_EXPORTER_PASSWORD",
}
# secrets that cannot exist when the machine is set up: somebody outside has to give them (an empty file is made until then)
LATER = {"CTFTIME_CLIENT_SECRET", "MAIL_PASSWORD", "DISCORD_WEBHOOK_URL", "TURNSTILE_SECRET", "GOOGLE_CLIENT_SECRET"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def test_every_secret_is_written_to_its_own_file(tmp_path):
    made = secrets_gen.generate(tmp_path / "s")
    assert set(made) == NAMES
    for name, path in made.items():
        assert path.read_text(encoding="utf-8").endswith("\n")
        assert len(read(path)) >= 32, name


def test_shapes(tmp_path):
    made = secrets_gen.generate(tmp_path / "s")
    assert re.fullmatch(r"[0-9a-f]{64}", read(made["SECRET_KEY"]))
    assert re.fullmatch(r"[0-9a-f]{64}", read(made["FLAG_HMAC_SECRET"]))
    assert re.fullmatch(r"ctfd_[0-9a-f]{64}", read(made["PRESET_ADMIN_TOKEN"]))
    for name in ("DATABASE_PASSWORD", "DATABASE_ROOT_PASSWORD", "REDIS_PASSWORD", "PRESET_ADMIN_PASSWORD", "GRAFANA_ADMIN_PASSWORD", "DB_EXPORTER_PASSWORD"):
        assert re.fullmatch(r"[A-Za-z0-9_-]{32,}", read(made[name])), name
    for name in ("INSTANCER_TOKEN", "ROUTER_TOKEN"):
        assert re.fullmatch(r"[0-9a-f]{64}", read(made[name])), name  # long, and plain enough to travel in an Authorization header


def test_no_two_secrets_are_equal_and_two_runs_differ(tmp_path):
    a = secrets_gen.generate(tmp_path / "a")
    b = secrets_gen.generate(tmp_path / "b")
    values = [read(p) for p in a.values()]
    assert len(set(values)) == len(values)
    assert all(read(a[n]) != read(b[n]) for n in NAMES)


def test_it_refuses_to_overwrite_unless_forced(tmp_path):
    made = secrets_gen.generate(tmp_path / "s")
    before = read(made["SECRET_KEY"])
    with pytest.raises(FileExistsError):
        secrets_gen.generate(tmp_path / "s")
    assert read(made["SECRET_KEY"]) == before
    again = secrets_gen.generate(tmp_path / "s", force=True)
    assert read(again["SECRET_KEY"]) != before


def test_the_folder_is_private_and_the_files_are_readable_by_the_containers(tmp_path):
    # 0600 files cannot be read by the services' own users on Linux (CI found it); the private folder is the protection
    made = secrets_gen.generate(tmp_path / "s")
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits do not apply on Windows")
    assert stat.S_IMODE((tmp_path / "s").stat().st_mode) == 0o700
    for path in made.values():
        assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_the_command_never_prints_a_value(tmp_path, capsys):
    from l3mon.cli import main

    assert main(["secrets", "generate", "--dir", str(tmp_path / "s")]) == 0
    out = capsys.readouterr().out
    for path in (tmp_path / "s").iterdir():
        if read(path):  # the two later secrets start as empty files: nothing in them to leak
            assert read(path) not in out
    assert "SECRET_KEY" in out  # names are fine, values are not
    assert main(["secrets", "generate", "--dir", str(tmp_path / "s")]) == 1  # refuses to overwrite


def test_the_config_commands(tmp_path, capsys):
    from l3mon.cli import main

    example = Path(__file__).resolve().parents[2] / "config" / "event.example.toml"
    assert main(["config", "validate", str(example)]) == 0
    assert main(["config", "render", str(example), "--out", str(tmp_path / "gen")]) == 0
    assert (tmp_path / "gen" / "preset_configs.json").is_file()
    bad = tmp_path / "bad.toml"
    bad.write_text("[event]\nname = ''\n", encoding="utf-8")
    assert main(["config", "validate", str(bad)]) == 1
    assert "event.name" in capsys.readouterr().out


def test_the_secrets_that_come_later_start_as_empty_files_and_are_never_replaced(tmp_path):
    folder = tmp_path / "s"
    secrets_gen.generate(folder)
    assert set(secrets_gen.PLACEHOLDERS) == LATER
    for name in secrets_gen.PLACEHOLDERS:
        assert (folder / name).exists() and (folder / name).read_text(encoding="utf-8") == "", name
        if sys.platform != "win32":
            assert stat.S_IMODE((folder / name).stat().st_mode) == 0o644, name
    (folder / "CTFTIME_CLIENT_SECRET").write_text("the-real-secret", encoding="utf-8")
    secrets_gen.generate(folder, force=True)
    assert (folder / "CTFTIME_CLIENT_SECRET").read_text(encoding="utf-8") == "the-real-secret", "a filled one is a real secret: not even --force replaces it"
    assert set(secrets_gen.PLACEHOLDERS).isdisjoint(secrets_gen.MAKERS)


def test_a_folder_that_holds_only_placeholders_is_not_taken_for_one_that_holds_secrets(tmp_path):
    folder = tmp_path / "s"
    folder.mkdir()
    (folder / "MAIL_PASSWORD").write_text("", encoding="utf-8")
    secrets_gen.generate(folder)  # does not raise: nothing that matters was there


def test_only_missing_adds_what_is_not_there_and_never_touches_what_is(tmp_path):
    """A machine set up before a new secret existed gets just that one: the others are real and in use (changing SECRET_KEY signs everybody out,
    changing the flag secret changes every team's flags)."""
    folder = tmp_path / "s"
    made = secrets_gen.generate(folder)
    kept = {name: read(path) for name, path in made.items()}
    (folder / "INSTANCER_TOKEN").unlink()
    (folder / "GRAFANA_ADMIN_PASSWORD").unlink()
    (folder / "TURNSTILE_SECRET").unlink()
    (folder / "MAIL_PASSWORD").write_text("a-real-password", encoding="utf-8")
    added = secrets_gen.generate(folder, only_missing=True)
    assert set(added) == {"INSTANCER_TOKEN", "GRAFANA_ADMIN_PASSWORD"}, "only the generated ones that were missing are reported"
    for name, value in kept.items():
        if name not in added:
            assert read(folder / name) == value, f"{name} must not change"
    assert re.fullmatch(r"[0-9a-f]{64}", read(folder / "INSTANCER_TOKEN"))
    assert (folder / "TURNSTILE_SECRET").exists() and read(folder / "TURNSTILE_SECRET") == "", "a missing later secret comes back as an empty file"
    assert read(folder / "MAIL_PASSWORD") == "a-real-password"
    assert secrets_gen.generate(folder, only_missing=True) == {}, "nothing missing: nothing written, and no complaint"


def test_only_missing_on_an_empty_folder_makes_everything_and_force_is_not_needed(tmp_path):
    made = secrets_gen.generate(tmp_path / "new", only_missing=True)
    assert set(made) == NAMES


def test_only_missing_and_force_cannot_be_asked_together(tmp_path):
    with pytest.raises(ValueError):
        secrets_gen.generate(tmp_path / "s", force=True, only_missing=True)
    from l3mon.cli import main

    with pytest.raises(SystemExit) as stopped:  # argparse refuses the pair
        main(["secrets", "generate", "--dir", str(tmp_path / "t"), "--force", "--only-missing"])
    assert stopped.value.code == 2
    assert not (tmp_path / "t").exists()


def test_the_command_takes_only_missing(tmp_path, capsys):
    from l3mon.cli import main

    assert main(["secrets", "generate", "--dir", str(tmp_path / "s")]) == 0
    (tmp_path / "s" / "ROUTER_TOKEN").unlink()
    capsys.readouterr()
    assert main(["secrets", "generate", "--dir", str(tmp_path / "s"), "--only-missing"]) == 0
    out = capsys.readouterr().out
    assert "ROUTER_TOKEN" in out and "SECRET_KEY" not in out

