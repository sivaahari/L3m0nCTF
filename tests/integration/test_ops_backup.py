"""Integration tests and PITR restore drill for L3m0nCTF operations backups.

Covers:
- Full database and uploads backups with age public-key encryption.
- MariaDB binary log shipping.
- Prometheus textfile heartbeat metric updates.
- Failure modes (missing recipient file, restore without --force).
- PITR drill (pytest --run-drill or L3MON_DRILL=1): destroys the stack, restores to chosen point in time.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import test_stack as t

pytestmark = pytest.mark.integration

BUDGET_SECONDS = 15 * 60


def bash(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    fixed = [a.replace(chr(92), "/") for a in args]
    return subprocess.run([t.BASH, *fixed], capture_output=True, text=True, timeout=timeout, cwd=t.ROOT)


def generate_age_keypair(key_path: Path, pub_path: Path):
    cmd_gen = subprocess.run(["age-keygen", "-o", str(key_path)], capture_output=True, text=True, check=True)
    cmd_pub = subprocess.run(["age-keygen", "-y", str(key_path)], capture_output=True, text=True, check=True)
    pub_path.write_text(cmd_pub.stdout.strip() + "\n", encoding="utf-8")


def test_ops_backup_full_and_binlog_encrypted(tmp_path):
    key_file = tmp_path / "test.key"
    pub_file = tmp_path / "test.pub"
    generate_age_keypair(key_file, pub_file)

    sink_dir = tmp_path / "sink"
    prom_file = tmp_path / "metrics" / "backup.prom"
    sink_uri = f"file://{sink_dir.as_posix()}"

    # 1. Run full backup
    backup_sh = t.ROOT / "tools" / "ops" / "backup.sh"
    res = bash(str(backup_sh), "--full", "--recipients", str(pub_file), "--sink", sink_uri, "--heartbeat", str(prom_file))
    assert res.returncode == 0, res.stdout + res.stderr

    full_files = list((sink_dir / "full").glob("backup-*.tar.age"))
    assert len(full_files) == 1, "Expected one encrypted full backup archive"
    enc_data = full_files[0].read_bytes()
    assert enc_data.startswith(b"age-encryption.org/v1"), "Backup file must be age encrypted"

    # Verify sha256 checksum file was generated
    sha_file = full_files[0].with_suffix(".age.sha256")
    assert sha_file.is_file()

    # 2. Run binlog backup
    res_bin = bash(str(backup_sh), "--binlog", "--recipients", str(pub_file), "--sink", sink_uri, "--heartbeat", str(prom_file))
    assert res_bin.returncode == 0, res_bin.stdout + res_bin.stderr

    bin_files = list((sink_dir / "binlogs").glob("binlogs-*.tar.age"))
    assert len(bin_files) >= 1, "Expected at least one encrypted binlog archive"
    assert bin_files[0].read_bytes().startswith(b"age-encryption.org/v1")

    # 3. Check Prometheus textfile metrics
    assert prom_file.is_file()
    prom_text = prom_file.read_text(encoding="utf-8")
    assert 'l3mon_backup_last_success_timestamp_seconds{type="binlog"}' in prom_text
    assert 'l3mon_backup_last_size_bytes{type="binlog"}' in prom_text
    assert 'l3mon_backup_last_duration_seconds{type="binlog"}' in prom_text


def test_ops_backup_missing_recipients_fails(tmp_path):
    sink_uri = f"file://{tmp_path.as_posix()}"
    backup_sh = t.ROOT / "tools" / "ops" / "backup.sh"
    res = bash(str(backup_sh), "--full", "--recipients", str(tmp_path / "nonexistent.pub"), "--sink", sink_uri)
    assert res.returncode != 0
    assert "recipients file not found" in res.stderr.lower() or "error" in res.stderr.lower()


def test_ops_restore_requires_force(tmp_path):
    key_file = tmp_path / "dummy.key"
    key_file.write_text("AGE-SECRET-KEY-DUMMY\n", encoding="utf-8")
    restore_sh = t.ROOT / "tools" / "ops" / "restore_pitr.sh"
    res = bash(str(restore_sh), "--sink", f"file://{tmp_path.as_posix()}", "--key", str(key_file))
    assert res.returncode != 0
    assert "--force" in res.stderr or "--force" in res.stdout


@pytest.mark.drill
def test_ops_restore_pitr_drill(tmp_path):
    token = {"Authorization": f"Token {t.secret('PRESET_ADMIN_TOKEN')}"}
    api = {**token, "Content-Type": "application/json"}
    started = time.time()

    key_file = tmp_path / "drill.key"
    pub_file = tmp_path / "drill.pub"
    generate_age_keypair(key_file, pub_file)

    sink_dir = tmp_path / "drill_sink"
    sink_uri = f"file://{sink_dir.as_posix()}"
    prom_file = tmp_path / "metrics" / "backup.prom"

    # Step 1: Create base platform data (Page A)
    marker_a = f"drill-base-{int(time.time())}"
    status, _, body = t.request(
        "/api/v1/pages",
        method="POST",
        headers=api,
        body=json.dumps({"title": marker_a, "route": marker_a, "content": "<p>Base data</p>", "format": "html", "draft": False, "hidden": False}).encode(),
    )
    assert status == 200, body
    page_a_id = json.loads(body)["data"]["id"]

    # Step 2: Take full backup
    backup_sh = t.ROOT / "tools" / "ops" / "backup.sh"
    res = bash(str(backup_sh), "--full", "--recipients", str(pub_file), "--sink", sink_uri, "--heartbeat", str(prom_file))
    assert res.returncode == 0, res.stdout + res.stderr

    time.sleep(1)

    # Step 3: Insert post-backup transaction (Page B)
    marker_b = f"drill-pitr-{int(time.time())}"
    status, _, body = t.request(
        "/api/v1/pages",
        method="POST",
        headers=api,
        body=json.dumps({"title": marker_b, "route": marker_b, "content": "<p>Post-backup transaction</p>", "format": "html", "draft": False, "hidden": False}).encode(),
    )
    assert status == 200, body
    page_b_id = json.loads(body)["data"]["id"]

    # Step 4: Ship binary logs (capturing Page B)
    res = bash(str(backup_sh), "--binlog", "--recipients", str(pub_file), "--sink", sink_uri, "--heartbeat", str(prom_file))
    assert res.returncode == 0, res.stdout + res.stderr

    # Step 5: Destroy platform completely
    t.compose("down", "-v", timeout=300)
    volumes = subprocess.run(["docker", "volume", "ls", "--format", "{{.Name}}"], capture_output=True, text=True).stdout
    assert "l3mon_dbdata" not in volumes

    # Bring up fresh stack with ops overlay
    env = {**os.environ, "L3MON_OVERLAYS": "ops"}
    t.compose("up", "-d", "--wait", "--wait-timeout", "300", timeout=600, env=env)
    t.wait_healthy("ctfd", 180)

    # Verify platform is completely fresh (neither Page A nor Page B exist)
    status, _, _ = t.request(f"/api/v1/pages/{page_a_id}", headers=api)
    assert status != 200
    status, _, _ = t.request(f"/api/v1/pages/{page_b_id}", headers=api)
    assert status != 200

    # Step 6: Restore via tools/ops/restore_pitr.sh
    restore_sh = t.ROOT / "tools" / "ops" / "restore_pitr.sh"
    res = bash(str(restore_sh), "--sink", sink_uri, "--key", str(key_file), "--force")
    assert res.returncode == 0, res.stdout + res.stderr

    t.wait_healthy("ctfd", 180)

    # Step 7: Verify BOTH Page A (from base dump) and Page B (from binlogs) exist!
    status, _, body = t.request(f"/api/v1/pages/{page_a_id}", headers=api)
    assert status == 200 and json.loads(body)["data"]["route"] == marker_a
    status, _, body = t.request(f"/api/v1/pages/{page_b_id}", headers=api)
    assert status == 200 and json.loads(body)["data"]["route"] == marker_b

    elapsed = time.time() - started
    print(f"\n[PITR DRILL SUCCESS] Completed in {elapsed:.1f} seconds (budget: {BUDGET_SECONDS}s)")
    assert elapsed < BUDGET_SECONDS, f"PITR drill took {elapsed:.1f}s, exceeding budget {BUDGET_SECONDS}s"

    # Cleanup pages
    t.request(f"/api/v1/pages/{page_a_id}", method="DELETE", headers=api)
    t.request(f"/api/v1/pages/{page_b_id}", method="DELETE", headers=api)
