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
}


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
    for name in ("DATABASE_PASSWORD", "DATABASE_ROOT_PASSWORD", "REDIS_PASSWORD", "PRESET_ADMIN_PASSWORD"):
        assert re.fullmatch(r"[A-Za-z0-9_-]{32,}", read(made[name])), name


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
