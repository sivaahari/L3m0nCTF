"""The restore drill: prove that a backup brings a destroyed platform back, and time it.

Destroys the development stack's data on purpose (volumes included) and restores it from a fresh backup, so it only runs
when asked:    python -m pytest tests/integration/test_restore_drill.py -q --run-drill   (or L3MON_DRILL=1)
Target from the build design: back to a working platform in under 15 minutes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid

import pytest

import test_stack as t

pytestmark = [pytest.mark.integration, pytest.mark.drill]

BUDGET_SECONDS = 15 * 60


def pytest_configure_hint():  # documentation only: see conftest.py for the --run-drill option
    return None


def multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = "----l3mon" + uuid.uuid4().hex
    out = b""
    for name, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    for name, (filename, content) in files.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: text/plain\r\n\r\n'.encode() + content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return out, f"multipart/form-data; boundary={boundary}"


def bash(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    # Git bash on Windows wants forward slashes in every path it is handed
    fixed = [a.replace(chr(92), "/") for a in args]
    return subprocess.run([t.BASH, *fixed], capture_output=True, text=True, timeout=timeout, cwd=t.ROOT)


def test_a_destroyed_platform_comes_back_from_a_backup(tmp_path):
    token = {"Authorization": f"Token {t.secret('PRESET_ADMIN_TOKEN')}"}
    api = {**token, "Content-Type": "application/json"}
    marker = f"drill-{int(time.time())}"
    started = time.time()

    # 1. data worth keeping: a page and an uploaded file
    status, _, body = t.request("/api/v1/pages", method="POST", headers=api, body=json.dumps({"title": marker, "route": marker, "content": "<p>drill</p>", "format": "html", "draft": False, "hidden": False}).encode())
    assert status == 200, body
    page_id = json.loads(body)["data"]["id"]
    payload, ctype = multipart({"type": "standard"}, {"file": (f"{marker}.txt", marker.encode())})
    status, _, body = t.request("/api/v1/files", method="POST", headers={**token, "Content-Type": ctype}, body=payload)
    assert status == 200, body
    location = json.loads(body)["data"][0]["location"]
    status, _, body = t.request(f"/files/{location}")
    assert status == 200 and body == marker.encode(), "the uploaded file must be downloadable before the drill"

    # a token that lives in the database (the preset administrator token is read from the environment and proves nothing here)
    status, _, body = t.request("/api/v1/tokens", method="POST", headers=api, body=json.dumps({"expiration": "2099-01-01", "description": marker}).encode())
    assert status == 200, body
    db_token = json.loads(body)["data"]["value"]
    status, _, _ = t.request("/api/v1/users/me", headers={"Authorization": f"Token {db_token}", "Content-Type": "application/json"})
    assert status == 200

    # 2. back up
    backup = tmp_path / "backup"
    done = bash(str(t.ROOT / "tools" / "backup.sh"), str(backup))
    assert done.returncode == 0, done.stdout + done.stderr
    assert (backup / "manifest.json").is_file()

    # 3. destroy everything, volumes included, and start from nothing
    t.compose("down", "-v", timeout=300)
    volumes = subprocess.run(["docker", "volume", "ls", "--format", "{{.Name}}"], capture_output=True, text=True).stdout
    assert "l3mon_dbdata" not in volumes and "l3mon_uploads" not in volumes
    t.compose("up", "-d", "--wait", "--wait-timeout", "300", timeout=600)
    status, _, _ = t.request(f"/api/v1/pages/{page_id}", headers=api)
    assert status != 200, "a fresh platform must not have the page"
    status, _, _ = t.request("/api/v1/users/me", headers={"Authorization": f"Token {db_token}", "Content-Type": "application/json"})
    assert status in (401, 403), "a fresh platform must not know the database token"
    status, _, _ = t.request(f"/files/{location}")
    assert status == 404, "a fresh platform must not have the file"

    # 4. restore
    done = bash(str(t.ROOT / "tools" / "restore.sh"), str(backup), "--force")
    assert done.returncode == 0, done.stdout + done.stderr
    t.wait_healthy("ctfd", 180)

    # 5. everything is back, and the administrator token still works
    status, _, body = t.request(f"/api/v1/pages/{page_id}", headers=api)
    assert status == 200 and json.loads(body)["data"]["route"] == marker
    status, _, body = t.request(f"/files/{location}")
    assert status == 200 and body == marker.encode()
    status, _, _ = t.request("/api/v1/users/me", headers=api)
    assert status == 200
    # the token that only the restored database knows works again
    status, _, body = t.request("/api/v1/users/me", headers={"Authorization": f"Token {db_token}", "Content-Type": "application/json"})
    assert status == 200 and json.loads(body)["data"]["name"] == "organiser"

    elapsed = time.time() - started
    print(f"drill finished in {elapsed:.0f} s")
    assert elapsed < BUDGET_SECONDS, f"the drill took {elapsed:.0f} s, the budget is {BUDGET_SECONDS} s"
    t.request(f"/api/v1/pages/{page_id}", method="DELETE", headers=api)


def test_a_damaged_backup_is_refused_and_changes_nothing(tmp_path):
    backup = tmp_path / "backup"
    done = bash(str(t.ROOT / "tools" / "backup.sh"), str(backup))
    assert done.returncode == 0, done.stdout + done.stderr
    data = (backup / "db.sql.gz").read_bytes()
    (backup / "db.sql.gz").write_bytes(data[:-10] + b"\x00" * 10)
    done = bash(str(t.ROOT / "tools" / "restore.sh"), str(backup), "--force")
    assert done.returncode != 0 and "Checksum mismatch" in done.stderr


def test_restore_always_needs_force_and_changes_nothing_without_it(tmp_path):
    backup = tmp_path / "backup"
    assert bash(str(t.ROOT / "tools" / "backup.sh"), str(backup)).returncode == 0
    before = t.sql("SELECT COUNT(*) FROM users")
    done = bash(str(t.ROOT / "tools" / "restore.sh"), str(backup))
    assert done.returncode != 0 and "--force" in done.stderr
    assert t.sql("SELECT COUNT(*) FROM users") == before
    status, _, _ = t.request("/login")
    assert status == 200, "the web side must still be up: nothing was stopped"


def test_a_backup_is_private_to_its_owner(tmp_path):
    import stat

    backup = tmp_path / "backup"
    assert bash(str(t.ROOT / "tools" / "backup.sh"), str(backup)).returncode == 0
    if sys.platform != "win32":
        assert stat.S_IMODE((backup / "db.sql.gz").stat().st_mode) & 0o077 == 0
        assert stat.S_IMODE(backup.stat().st_mode) & 0o077 == 0
