"""tools/compose.sh and its overlays: `L3MON_OVERLAYS=a,b` adds deploy/compose/compose.a.yml and compose.b.yml after the environment's own file.

Each piece of the platform that adds services (the instancer, the operations stack, ...) ships its own overlay file, so none of them has to edit a shared
file. The script is copied into a throwaway folder with a fake `docker` first on the path that only prints what it was given, so no Docker is needed."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GIT_BASH = "C:/Program Files/Git/bin/bash.exe"
BASH = os.environ.get("L3MON_BASH") or (GIT_BASH if sys.platform == "win32" and os.path.exists(GIT_BASH) else "bash")  # on Windows plain "bash" can be WSL


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / "tools").mkdir()
    shutil.copy(ROOT / "tools" / "compose.sh", tmp_path / "tools" / "compose.sh")
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "SECRET_KEY").write_text("x\n")
    compose = tmp_path / "deploy" / "compose"
    (compose / "generated").mkdir(parents=True)
    (compose / "generated" / "compose.env").write_text("PLATFORM_HOST=x\n")
    for name in ("base", "dev", "production", "instancer", "ops"):
        (compose / f"compose.{name}.yml").write_text("services: {}\n")
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "docker").write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    (fake / "docker").chmod(0o755)
    return tmp_path


def run(repo: Path, **env):
    full = {**os.environ, "PATH": f"{(repo / 'fakebin').as_posix()}{os.pathsep}{os.environ['PATH']}", **env}
    for name in ("L3MON_ENV", "L3MON_OVERLAYS"):
        if name not in env:
            full.pop(name, None)
    return subprocess.run([BASH, str(repo / "tools" / "compose.sh"), "config"], capture_output=True, text=True, env=full, timeout=60)


def files_of(out: subprocess.CompletedProcess) -> list[str]:
    lines = out.stdout.splitlines()
    return [Path(lines[i + 1]).name for i, line in enumerate(lines) if line == "-f"]


def test_without_overlays_nothing_changes(repo):
    out = run(repo)
    assert out.returncode == 0, out.stderr
    assert files_of(out) == ["compose.base.yml", "compose.dev.yml"]


def test_overlays_come_after_the_environment_file_in_the_order_given(repo):
    out = run(repo, L3MON_OVERLAYS="instancer,ops")
    assert out.returncode == 0, out.stderr
    assert files_of(out) == ["compose.base.yml", "compose.dev.yml", "compose.instancer.yml", "compose.ops.yml"]
    out = run(repo, L3MON_ENV="production", L3MON_OVERLAYS="ops")
    assert files_of(out) == ["compose.base.yml", "compose.production.yml", "compose.ops.yml"]


def test_an_empty_list_and_empty_items_are_ignored(repo):
    assert files_of(run(repo, L3MON_OVERLAYS="")) == ["compose.base.yml", "compose.dev.yml"]
    assert files_of(run(repo, L3MON_OVERLAYS=",ops,")) == ["compose.base.yml", "compose.dev.yml", "compose.ops.yml"]


@pytest.mark.parametrize("bad", ["../etc/passwd", "ops/../x", "Ops", "ops yml", "-f", "ops;ls", "ops\nx", "", "a" * 40 + "/"])
def test_a_name_that_is_not_a_plain_lowercase_word_is_refused(repo, bad):
    if bad == "":
        pytest.skip("empty is the 'no overlay' case, tested above")
    out = run(repo, L3MON_OVERLAYS=bad)
    assert out.returncode != 0
    assert "overlay" in out.stderr.lower()
    assert "config" not in out.stdout  # docker was never started


def test_an_overlay_that_does_not_exist_is_refused(repo):
    out = run(repo, L3MON_OVERLAYS="missing")
    assert out.returncode != 0
    assert "missing" in out.stderr


@pytest.mark.parametrize("name", ["base", "dev", "production"])
def test_the_names_of_the_base_and_the_environments_cannot_be_used_as_overlays(repo, name):
    out = run(repo, L3MON_OVERLAYS=name)
    assert out.returncode != 0
    assert "overlay" in out.stderr.lower()
