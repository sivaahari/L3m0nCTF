"""tools/export-state.sh and tools/import-state.sh: save everything that lives only on one machine, and put it back on another, exactly.

The scripts are run as they are, on a small fake machine: a git folder with a tag, secrets, settings, a folder standing in for /etc/l3mon, and stand-ins
for compose.sh, backup.sh, restore.sh and docker (they record what they were asked to do). The real thing, with a real database, was rehearsed with
Docker (docs/deploy/move-to-a-new-account.md says how); these tests keep every refusal and every promise of the scripts from changing quietly.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GIT_BASH = "C:/Program Files/Git/bin/bash.exe"
BASH = os.environ.get("L3MON_BASH") or (GIT_BASH if sys.platform == "win32" and os.path.exists(GIT_BASH) else "bash")
WINDOWS = sys.platform == "win32"
PASSPHRASE = "a rehearsal passphrase"
SECRETS = {
    "SECRET_KEY": "0123456789abcdef" * 4,
    "FLAG_HMAC_SECRET": "fedcba9876543210" * 4,
    "DATABASE_PASSWORD": "db-password-value-1",
    "MAIL_PASSWORD": "",
}

COMPOSE = """#!/usr/bin/env bash
# stand-in for tools/compose.sh
args="$*"
echo "compose $args" >> "$FAKE_LOG"
case "$args" in
  "ps -q ctfd") [[ -n "${FAKE_DOWN:-}" ]] || echo fakecontainerid ;;
  *"ls -A /var/story"*) echo "${FAKE_STORY_COUNT:-0}" ;;
  *"tar -cf - ."*) tar -cf - -C "$FAKE_STORY_DIR" . ;;
  *"mariadb -N"*) echo 3 ;;
esac
"""
BACKUP = """#!/usr/bin/env bash
# stand-in for tools/backup.sh: a folder with the three files the real one writes
out="$1"; mkdir -p "$out"
printf 'fake database dump\\n' | gzip > "$out/db.sql.gz"
printf 'fake uploads\\n' | gzip > "$out/uploads.tar.gz"
echo '{"format": 1}' > "$out/manifest.json"
echo "backup $out" >> "$FAKE_LOG"
"""
RESTORE = """#!/usr/bin/env bash
# stand-in for tools/restore.sh
echo "restore $1 $2 manifest=$(cat "$1/manifest.json")" >> "$FAKE_LOG"
"""
DOCKER = """#!/usr/bin/env bash
# stand-in for docker
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  inspect) echo fake-image-name ;;
  volume) echo l3mon_story ;;
  run) cat > "$FAKE_LOG.story" ;;
  *) echo "fake docker output" ;;
esac
"""


def sh(*args: str, cwd: Path, env: dict | None = None, input: str | None = None) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if not k.startswith(("L3MON_", "FAKE_", "COMPOSE_"))}
    full.update(env or {})
    return subprocess.run([BASH, *[a.replace("\\", "/") for a in args]], cwd=cwd, env=full, capture_output=True, text=True, input=input, timeout=120)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args], check=True, capture_output=True)


def make_machine(base: Path, name: str, with_state: bool = True) -> dict:
    """A fake machine: <base>/<name>/repo (a git folder at a tag, with the two scripts copied in) and <name>/etc."""
    machine = base / name
    repo, etc, bin_dir = machine / "repo", machine / "etc", machine / "bin"
    (repo / "tools").mkdir(parents=True)
    bin_dir.mkdir()
    for fake, body in (("compose.sh", COMPOSE), ("backup.sh", BACKUP), ("restore.sh", RESTORE)):
        (repo / "tools" / fake).write_text(body, newline="\n")
        (repo / "tools" / fake).chmod(0o755)  # git keeps the mode, so the clone of the new machine can run them too
    (bin_dir / "docker").write_text(DOCKER, newline="\n")
    (bin_dir / "docker").chmod(0o755)
    (repo / ".gitignore").write_text(".secrets/\ndeploy/compose/generated/\nconfig/event.toml\nbackups/\n.fake.log*\n")
    (repo / "README.md").write_text("fake\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "release")
    git(repo, "tag", "release-test")
    for script in ("export-state.sh", "import-state.sh"):
        shutil.copy(ROOT / "tools" / script, repo / "tools" / script)
    story = machine / "story"
    if with_state:
        (repo / ".secrets").mkdir()
        for key, value in SECRETS.items():
            (repo / ".secrets" / key).write_text(value)
        (repo / "config").mkdir()
        (repo / "config" / "event.toml").write_text('platform_host = "play.example.test"\n')
        (repo / "deploy" / "compose" / "generated").mkdir(parents=True)
        (repo / "deploy" / "compose" / "generated" / "compose.env").write_text("PLATFORM_HOST=play.example.test\n")
        (etc / "tls").mkdir(parents=True)
        (etc / "production.env").write_text("L3MON_ENV=production\nL3MON_IMAGE_TAG=release-test\nTLS_DIR=/etc/l3mon/tls\n")
        (etc / "admin-nets.conf").write_text("203.0.113.10 1;\n")
        for tls_file, content in (("origin.pem", "fake origin certificate\n"), ("origin.key", "fake origin key material\n"), ("cloudflare-origin-pull-ca.pem", "fake ca\n")):
            (etc / "tls" / tls_file).write_text(content)
        (story / "pics").mkdir(parents=True)
        (story / "one.json").write_text('{"slug": "one"}\n')
        (story / "pics" / "1.webp").write_text("webp\n")
    log = machine / "fake.log"
    env = {
        "PATH": f"{bin_dir.as_posix()}{os.pathsep}{os.environ['PATH']}",
        "FAKE_LOG": log.as_posix(),
        "FAKE_STORY_DIR": story.as_posix(),
        "FAKE_STORY_COUNT": "2",
        "L3MON_ETC": etc.as_posix(),
        "L3MON_SUDO": "",
        "L3MON_TLS_GROUP": "",
        "L3MON_NO_MODES": "1" if WINDOWS else "",
    }
    return {"repo": repo, "etc": etc, "env": env, "log": log, "dir": machine}


@pytest.fixture()
def old(tmp_path):
    return make_machine(tmp_path, "old")


def export(machine: dict, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
    out = machine["dir"] / "out"
    return sh("tools/export-state.sh", "--out", str(out), *extra, cwd=machine["repo"], env={**machine["env"], **(env or {})})


def saved_file(machine: dict, suffix: str = ".tar") -> Path:
    found = sorted((machine["dir"] / "out").glob(f"l3mon-state-*{suffix}"))
    assert found, "no state file was written"
    return found[-1]


def test_the_export_writes_one_owner_only_file_with_everything_in_it(old):
    r = export(old)
    assert r.returncode == 0, r.stdout + r.stderr
    plain = saved_file(old)
    names = tarfile.open(plain).getnames()
    for wanted in ("state/STATE.env", "state/MANIFEST.sha256", "state/backup/db.sql.gz", "state/backup/manifest.json", "state/story.tar.gz", "state/repo/secrets/SECRET_KEY",
                   "state/repo/secrets/FLAG_HMAC_SECRET", "state/repo/event.toml", "state/repo/generated/compose.env", "state/etc/production.env",
                   "state/etc/admin-nets.conf", "state/etc/tls/origin.key", "state/etc/tls/origin.pem", "state/git/status.txt"):
        assert wanted in names, wanted
    if not WINDOWS:
        assert oct(plain.stat().st_mode & 0o777) == "0o600"
    assert (plain.parent / (plain.name + ".sha256")).read_text().split()[0] == __import__("hashlib").sha256(plain.read_bytes()).hexdigest()


def test_the_secrets_come_out_bit_for_bit_and_the_summary_names_the_release(old):
    export(old)
    with tarfile.open(saved_file(old)) as tar:
        for key, value in SECRETS.items():
            assert tar.extractfile(f"state/repo/secrets/{key}").read().decode() == value
        summary = dict(line.split("=", 1) for line in tar.extractfile("state/STATE.env").read().decode().splitlines())
    head = subprocess.run(["git", "-C", str(old["repo"]), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert summary["COMMIT"] == head and summary["TAG"] == "release-test" and summary["STORY_FILES"] == "2"
    assert summary["LOCAL_CHANGES"] == "no", "the two scripts copied onto a machine are not changes"


def test_nothing_secret_is_printed(old):
    r = export(old)
    shown = r.stdout + r.stderr
    for value in SECRETS.values():
        if value:
            assert value not in shown
    assert "fake origin key material" not in shown


def test_the_story_and_the_backup_are_asked_for_through_compose(old):
    export(old)
    log = old["log"].read_text()
    assert "backup " in log and "compose exec -T ctfd sh -c" in log
    with tarfile.open(saved_file(old)) as tar:
        import gzip
        import io
        inner = tarfile.open(fileobj=io.BytesIO(gzip.decompress(tar.extractfile("state/story.tar.gz").read())))
        assert "./one.json" in inner.getnames() and "./pics/1.webp" in inner.getnames()


def test_a_change_to_the_checked_out_files_is_recorded_with_a_patch(old):
    (old["repo"] / "README.md").write_text("changed on the machine\n")
    export(old)
    with tarfile.open(saved_file(old)) as tar:
        assert b"changed on the machine" in tar.extractfile("state/git/local-changes.patch").read()
        assert b"LOCAL_CHANGES=yes" in tar.extractfile("state/STATE.env").read()


def test_it_refuses_when_the_platform_is_not_running(old):
    r = export(old, env={"FAKE_DOWN": "1"})
    assert r.returncode != 0 and "not running" in r.stderr
    assert not list((old["dir"] / "out").glob("l3mon-state-*"))


def test_it_refuses_a_folder_that_has_no_secrets(tmp_path):
    machine = make_machine(tmp_path, "empty", with_state=False)
    r = export(machine)
    assert r.returncode != 0 and ".secrets" in r.stderr


def test_the_encrypted_file_is_not_readable_without_the_passphrase_and_the_plain_copy_is_gone(old, tmp_path):
    pass_file = tmp_path / "pass.txt"
    pass_file.write_text(PASSPHRASE)
    r = export(old, "--encrypt", env={"L3MON_STATE_PASSPHRASE_FILE": pass_file.as_posix()})
    assert r.returncode == 0, r.stdout + r.stderr
    enc = saved_file(old, ".tar.enc")
    assert not list((old["dir"] / "out").glob("*.tar"))
    with pytest.raises(tarfile.TarError):
        tarfile.open(enc)
    assert b"SECRET_KEY" not in enc.read_bytes() and SECRETS["SECRET_KEY"].encode() not in enc.read_bytes()


def test_different_passphrases_write_nothing(old, tmp_path):
    r = sh("tools/export-state.sh", "--out", str(old["dir"] / "out"), "--encrypt", cwd=old["repo"], env=old["env"], input="one\ntwo\n")
    assert r.returncode != 0
    assert not list((old["dir"] / "out").glob("l3mon-state-*"))


def place(new: dict, state: Path, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return sh("tools/import-state.sh", "place", str(state), *extra, cwd=new["repo"], env={**new["env"], **(env or {})})


@pytest.fixture()
def saved(old, tmp_path):
    export(old)
    return saved_file(old)


@pytest.fixture()
def new(old, tmp_path):
    """The new machine: a fresh clone of the same repository at the same release, with nothing of the platform on it yet."""
    machine = tmp_path / "new"
    (machine / "bin").mkdir(parents=True)
    shutil.copy(old["dir"] / "bin" / "docker", machine / "bin" / "docker")
    (machine / "bin" / "docker").chmod(0o755)
    subprocess.run(["git", "clone", "-q", str(old["repo"]), str(machine / "repo")], check=True, capture_output=True)
    for script in ("export-state.sh", "import-state.sh"):
        shutil.copy(ROOT / "tools" / script, machine / "repo" / "tools" / script)
    env = dict(old["env"])
    env.update({"PATH": f"{(machine / 'bin').as_posix()}{os.pathsep}{os.environ['PATH']}", "FAKE_LOG": (machine / "fake.log").as_posix(), "L3MON_ETC": (machine / "etc").as_posix()})
    return {"repo": machine / "repo", "etc": machine / "etc", "env": env, "log": machine / "fake.log", "dir": machine}


def test_place_puts_everything_back_the_same(saved, new):
    r = place(new, saved)
    assert r.returncode == 0, r.stdout + r.stderr
    for key, value in SECRETS.items():
        assert (new["repo"] / ".secrets" / key).read_text() == value
    assert (new["repo"] / "config" / "event.toml").read_text() == 'platform_host = "play.example.test"\n'
    assert (new["repo"] / "deploy" / "compose" / "generated" / "compose.env").exists()
    assert (new["etc"] / "production.env").read_text().startswith("L3MON_ENV=production")
    assert (new["etc"] / "tls" / "origin.key").read_text() == "fake origin key material\n"
    assert (new["etc"] / "admin-nets.conf").read_text() == "203.0.113.10 1;\n"
    assert "tools/compose.sh up -d --wait" in r.stdout and "tools/import-state.sh restore" in r.stdout


@pytest.mark.skipif(WINDOWS, reason="a Windows file system keeps no Unix modes")
def test_place_gives_the_files_the_modes_of_the_hosting_guide(saved, new):
    place(new, saved)
    mode = lambda p: oct(p.stat().st_mode & 0o777)
    assert mode(new["repo"] / ".secrets") == "0o700"
    assert mode(new["etc"] / "tls") == "0o750"
    assert mode(new["etc"] / "tls" / "origin.key") == "0o640"
    assert mode(new["etc"] / "production.env") == "0o644"


def test_place_refuses_to_overwrite_what_is_there_unless_forced(saved, new):
    (new["repo"] / ".secrets").mkdir()
    (new["repo"] / ".secrets" / "SECRET_KEY").write_text("other")
    r = place(new, saved)
    assert r.returncode != 0 and "--force" in r.stderr
    assert (new["repo"] / ".secrets" / "SECRET_KEY").read_text() == "other"
    assert place(new, saved, "--force").returncode == 0
    assert (new["repo"] / ".secrets" / "SECRET_KEY").read_text() == SECRETS["SECRET_KEY"]


def test_place_refuses_a_machine_with_a_filled_etc_folder(saved, new):
    new["etc"].mkdir(parents=True)
    (new["etc"] / "production.env").write_text("L3MON_ENV=other\n")
    r = place(new, saved)
    assert r.returncode != 0 and "--force" in r.stderr
    assert not (new["repo"] / ".secrets").exists()


def test_place_refuses_a_different_commit_unless_told(saved, new):
    (new["repo"] / "README.md").write_text("a later commit\n")
    git(new["repo"], "commit", "-q", "-am", "later")
    r = place(new, saved)
    assert r.returncode != 0 and "release-test" in r.stderr
    assert not (new["repo"] / ".secrets").exists()
    assert place(new, saved, "--allow-other-commit").returncode == 0


def test_a_changed_byte_is_caught_and_nothing_is_written(old, new, tmp_path):
    export(old)
    plain = saved_file(old)
    work = tmp_path / "tamper"
    work.mkdir()
    with tarfile.open(plain) as tar:
        tar.extractall(work, filter="data") if sys.version_info >= (3, 12) else tar.extractall(work)
    with open(work / "state" / "backup" / "db.sql.gz", "ab") as f:
        f.write(b"x")
    bad = tmp_path / "bad.tar"
    with tarfile.open(bad, "w") as tar:
        tar.add(work / "state", arcname="state")
    r = place(new, bad)
    assert r.returncode != 0 and "checksum" in r.stderr
    assert not (new["repo"] / ".secrets").exists() and not new["etc"].exists()


def test_a_path_that_leaves_the_folder_is_refused(new, tmp_path):
    evil = tmp_path / "evil.tar"
    payload = tmp_path / "payload.txt"
    payload.write_text("x")
    with tarfile.open(evil, "w") as tar:
        tar.add(payload, arcname="state/../../escaped.txt")
    r = place(new, evil)
    assert r.returncode != 0 and "outside" in r.stderr
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_a_file_that_is_not_ours_is_refused(new, tmp_path):
    other = tmp_path / "other.tar"
    payload = tmp_path / "payload.txt"
    payload.write_text("x")
    with tarfile.open(other, "w") as tar:
        tar.add(payload, arcname="state/readme.txt")
    r = place(new, other)
    assert r.returncode != 0 and "not a file made by" in r.stderr


def test_the_encrypted_file_opens_with_the_passphrase_and_not_without(old, new, tmp_path):
    pass_file = tmp_path / "pass.txt"
    pass_file.write_text(PASSPHRASE)
    assert export(old, "--encrypt", env={"L3MON_STATE_PASSPHRASE_FILE": pass_file.as_posix()}).returncode == 0
    enc = saved_file(old, ".tar.enc")
    wrong = tmp_path / "wrong.txt"
    wrong.write_text("not the passphrase")
    r = place(new, enc, env={"L3MON_STATE_PASSPHRASE_FILE": wrong.as_posix()})
    assert r.returncode != 0 and "did not open" in r.stderr
    assert not (new["repo"] / ".secrets").exists()
    r = place(new, enc, env={"L3MON_STATE_PASSPHRASE_FILE": pass_file.as_posix()})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (new["repo"] / ".secrets" / "FLAG_HMAC_SECRET").read_text() == SECRETS["FLAG_HMAC_SECRET"]


def test_restore_replaces_the_data_with_the_force_flag_and_refills_the_story(saved, new):
    assert place(new, saved).returncode == 0
    r = sh("tools/import-state.sh", "restore", str(saved), cwd=new["repo"], env=new["env"])
    assert r.returncode == 0, r.stdout + r.stderr
    log = new["log"].read_text()
    restore_line = [line for line in log.splitlines() if line.startswith("restore ")][0]
    assert restore_line.endswith(' manifest={"format": 1}') and " --force " in restore_line
    story = Path(str(new["log"]) + ".story").read_bytes()
    import io
    assert "./one.json" in tarfile.open(fileobj=io.BytesIO(story)).getnames()
    assert "users 3" in r.stdout and "sign in again" in r.stdout


def test_restore_refuses_when_the_platform_is_not_up(saved, new):
    r = sh("tools/import-state.sh", "restore", str(saved), cwd=new["repo"], env={**new["env"], "FAKE_DOWN": "1"})
    assert r.returncode != 0 and "not running" in r.stderr
    assert "restore " not in (new["log"].read_text() if new["log"].exists() else "")


def test_restore_without_a_database_backup_is_refused(old, new):
    export(old, "--skip-backup")
    r = sh("tools/import-state.sh", "restore", str(saved_file(old)), cwd=new["repo"], env=new["env"])
    assert r.returncode != 0 and "skip-backup" in r.stderr


# --- a file is never trusted beyond its checksums: found by the automated security review of the first version ---------------------------------------------


def rebuilt(old: dict, tmp_path: Path, edit) -> Path:
    """A plain state file made from the real one with `edit(state_dir)` applied and the manifest recomputed, as someone who can write the file would."""
    export(old)
    work = tmp_path / "rebuilt"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    with tarfile.open(saved_file(old)) as tar:
        tar.extractall(work, filter="data") if sys.version_info >= (3, 12) else tar.extractall(work)
    state = work / "state"
    edit(state)
    import hashlib
    lines = []
    for path in sorted(p for p in state.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256"):
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{path.relative_to(state).as_posix()}")
    (state / "MANIFEST.sha256").write_bytes(("\n".join(lines) + "\n").encode())  # bytes: a Windows text write would end every line with CR LF
    out = tmp_path / "rebuilt.tar"
    with tarfile.open(out, "w") as tar:
        tar.add(state, arcname="state")
    return out


def set_env_line(key: str, value: str):
    def edit(state: Path):
        text = (state / "STATE.env").read_text().splitlines()
        (state / "STATE.env").write_bytes(("\n".join(f"{key}={value}" if line.startswith(key + "=") else line for line in text) + "\n").encode())
    return edit


def test_a_value_in_the_summary_is_never_run_as_code(old, new, tmp_path):
    """`[[ x -gt 0 ]]` evaluates an array subscript such as a[$(command)]: a crafted STORY_FILES would have run the command."""
    marker = tmp_path / "PWNED"
    evil = rebuilt(old, tmp_path, set_env_line("STORY_FILES", f"a[$(touch {marker.as_posix()})]"))
    assert place(new, evil).returncode == 0
    r = sh("tools/import-state.sh", "restore", str(evil), cwd=new["repo"], env=new["env"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert not marker.exists(), "the value was evaluated as a command"
    assert not Path(str(new["log"]) + ".story").exists(), "an unreadable count means no story is refilled"


def test_a_summary_without_a_real_commit_is_refused(old, new, tmp_path):
    evil = rebuilt(old, tmp_path, set_env_line("COMMIT", "$(touch nothing)"))
    r = place(new, evil)
    assert r.returncode != 0 and "no valid commit" in r.stderr
    assert not (new["repo"] / ".secrets").exists()


def test_a_link_inside_the_file_is_refused(old, new, tmp_path):
    def add_link(state: Path):
        (state / "repo" / "etc-link").symlink_to("/etc")
    if WINDOWS:
        pytest.skip("a Windows file system cannot make the link")
    evil = rebuilt(old, tmp_path, add_link)
    r = place(new, evil)
    assert r.returncode != 0 and "link or a device" in r.stderr
    assert not (new["repo"] / ".secrets").exists()


def test_a_changed_byte_in_an_encrypted_file_is_refused_before_anything_is_decrypted(old, new, tmp_path):
    pass_file = tmp_path / "pass.txt"
    pass_file.write_text(PASSPHRASE)
    assert export(old, "--encrypt", env={"L3MON_STATE_PASSPHRASE_FILE": pass_file.as_posix()}).returncode == 0
    enc = saved_file(old, ".tar.enc")
    data = bytearray(enc.read_bytes())
    assert data[-76:].startswith(b"\nL3MON-MAC-") and data[-1:] == b"\n", "the last line is the authentication line"
    data[200] ^= 0x01
    bad = tmp_path / "changed.tar.enc"
    bad.write_bytes(bytes(data))
    r = place(new, bad, env={"L3MON_STATE_PASSPHRASE_FILE": pass_file.as_posix()})
    assert r.returncode != 0 and "was changed" in r.stderr
    assert not (new["repo"] / ".secrets").exists()


def test_an_encrypted_file_without_its_authentication_line_is_refused(old, new, tmp_path):
    pass_file = tmp_path / "pass.txt"
    pass_file.write_text(PASSPHRASE)
    export(old, "--encrypt", env={"L3MON_STATE_PASSPHRASE_FILE": pass_file.as_posix()})
    cut = tmp_path / "cut.tar.enc"
    cut.write_bytes(saved_file(old, ".tar.enc").read_bytes()[:-76])
    r = place(new, cut, env={"L3MON_STATE_PASSPHRASE_FILE": pass_file.as_posix()})
    assert r.returncode != 0 and "no authentication line" in r.stderr


def test_a_plain_file_is_accepted_with_a_warning_that_nothing_proves_who_made_it(saved, new):
    r = place(new, saved)
    assert r.returncode == 0 and "not encrypted" in r.stderr


def test_a_story_count_that_is_not_a_number_is_never_run_as_code_when_saving(old, tmp_path):
    marker = tmp_path / "PWNED2"
    r = export(old, env={"FAKE_STORY_COUNT": f"a[$(touch {marker.as_posix()})]"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert not marker.exists()
    with tarfile.open(saved_file(old)) as tar:
        assert b"STORY_FILES=0" in tar.extractfile("state/STATE.env").read()
        assert "state/story.tar.gz" not in tar.getnames()
