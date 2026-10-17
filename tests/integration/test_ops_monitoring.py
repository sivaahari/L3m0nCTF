"""Integration tests for the monitoring and observability stack (Milestone 4).

Covers:
- Prometheus health, lifecycle, and active targets all reporting UP.
- Prometheus rule loading (all 13 operational alert rules present).
- Alertmanager health and API responsiveness (Discord webhook fallback / nowebhook).
- Grafana authentication, provisioned datasources (Prometheus, Loki), and provisioned dashboards.
- Loki log store readiness.
- Alert fire drill script execution and validation.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
import test_stack as t

pytestmark = pytest.mark.integration

ROOT = t.ROOT
SECRETS_DIR = ROOT / ".secrets"


def get_secret(name: str) -> str:
    path = SECRETS_DIR / name
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _required_targets_state(required_jobs):
    """(missing jobs, {job: (health, last error)}) as Prometheus sees them right now."""
    url = "http://127.0.0.1:9090/api/v1/targets"
    resp = urllib.request.urlopen(url, timeout=5)
    data = json.loads(resp.read().decode("utf-8"))
    found = {}
    for target in data.get("data", {}).get("activeTargets", []):
        found[target.get("labels", {}).get("job")] = (target.get("health"), target.get("lastError") or "")
    return required_jobs - set(found), found


def test_prometheus_health_and_targets_up():
    """Verify Prometheus is reachable and that all core scrape targets are UP.

    Integration audit: a target is only UP after Prometheus has scraped it once since it last changed (15 s interval), so a
    container restarted a moment before is checked for up to a minute. A target that stays down (a persistent error 500, for
    example) still fails the test with Prometheus's own error text.
    """
    # Core required targets that must be UP when stack is running
    required_jobs = {
        "ctfd",
        "mail-relay",
        "nginx-exporter",
        "mysqld-exporter",
        "redis-exporter",
        "blackbox-readyz",
        "blackbox-ctftime",
    }

    deadline = time.time() + 60
    while True:
        try:
            missing, found = _required_targets_state(required_jobs)
            error = None
        except Exception as exc:
            missing, found, error = required_jobs, {}, exc
        down = {job: found[job] for job in required_jobs & set(found) if found[job][0] != "up"}
        if (not missing and not down) or time.time() > deadline:
            break
        time.sleep(3)

    if error is not None:
        pytest.fail(f"Could not read the targets from Prometheus: {error}")
    assert found, "No active targets found in Prometheus"
    assert not missing, f"Missing required scrape targets: {missing}"
    for job in sorted(down):
        health, last_err = down[job]
        pytest.fail(f"Target job '{job}' is not UP: health={health}, lastError={last_err}")


def test_prometheus_rules_loaded():
    """Verify that all 13 operational alert rules are loaded into Prometheus."""
    url = "http://127.0.0.1:9090/api/v1/rules"
    resp = urllib.request.urlopen(url, timeout=5)
    data = json.loads(resp.read().decode("utf-8"))

    expected_rules = {
        "High5xxRate",
        "HighLatency",
        "DatabaseConnectionsHigh",
        "RedisMemoryHigh",
        "DiskSpaceWarning",
        "DiskSpaceCritical",
        "ContainerRestarting",
        "ReadyzFailing",
        "CTFtimeFeedStale",
        "BackupStale",
        "MailQueueGrowing",
        "CertificateExpiring",
        "InstancerErrorRateHigh",
    }

    groups = data.get("data", {}).get("groups", [])
    loaded_rules = {r.get("name") for g in groups for r in g.get("rules", [])}

    missing = expected_rules - loaded_rules
    assert not missing, f"Missing operational alert rules in Prometheus: {missing}"


def test_alertmanager_health_and_status():
    """Verify Alertmanager /-/healthy and /api/v2/status."""
    healthy_url = "http://127.0.0.1:9093/-/healthy"
    status_url = "http://127.0.0.1:9093/api/v2/status"

    resp = urllib.request.urlopen(healthy_url, timeout=5)
    assert resp.status == 200

    resp = urllib.request.urlopen(status_url, timeout=5)
    assert resp.status == 200
    status_data = json.loads(resp.read().decode("utf-8"))
    assert "versionInfo" in status_data
    assert "cluster" in status_data


def test_grafana_health_datasources_and_dashboards():
    """Verify Grafana health, provisioned Prometheus/Loki datasources, and 4 dashboards."""
    health_url = "http://127.0.0.1:3000/api/health"
    resp = urllib.request.urlopen(health_url, timeout=5)
    assert resp.status == 200
    assert json.loads(resp.read().decode("utf-8")).get("database") == "ok"

    admin_pw = get_secret("GRAFANA_ADMIN_PASSWORD")
    assert admin_pw, "GRAFANA_ADMIN_PASSWORD secret missing"

    auth_header = "Basic " + base64.b64encode(f"admin:{admin_pw}".encode()).decode()

    # 1. Verify datasources
    ds_url = "http://127.0.0.1:3000/api/datasources"
    req = urllib.request.Request(ds_url, headers={"Authorization": auth_header})
    ds_resp = urllib.request.urlopen(req, timeout=5)
    assert ds_resp.status == 200
    datasources = json.loads(ds_resp.read().decode("utf-8"))
    ds_names = {d.get("name"): d.get("type") for d in datasources}
    assert "Prometheus" in ds_names, f"Prometheus datasource missing: {ds_names}"
    assert "Loki" in ds_names, f"Loki datasource missing: {ds_names}"

    # 2. Verify dashboards
    dash_url = "http://127.0.0.1:3000/api/search?type=dash-db"
    req = urllib.request.Request(dash_url, headers={"Authorization": auth_header})
    dash_resp = urllib.request.urlopen(req, timeout=5)
    assert dash_resp.status == 200
    dashboards = json.loads(dash_resp.read().decode("utf-8"))
    dash_uids = {d.get("uid") for d in dashboards}

    expected_uids = {
        "l3mon-overview",
        "l3mon-database",
        "l3mon-hosts",
        "l3mon-mail-backups",
    }
    missing_dashboards = expected_uids - dash_uids
    assert not missing_dashboards, f"Missing Grafana dashboards: {missing_dashboards}"


def test_loki_ready_status():
    """Verify Loki log store answers /ready with 'ready'."""
    cmd = ["docker", "exec", "l3mon-loki-1", "wget", "-qO-", "http://127.0.0.1:3100/ready"]
    out = ""
    for _ in range(30):  # Loki answers 503 for a while after it starts and while the machine is busy (integration audit: ten tries of a second were too few)
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if done.returncode == 0 and "ready" in done.stdout.lower():
            out = done.stdout
            break
        time.sleep(2)
    assert "ready" in out.lower(), f"Loki failed to report ready: {out}"


def test_fire_drill_script_passes():
    """Execute tools/ops/fire_drill.sh and verify all 13 alerts inject and verify."""
    drill_script = ROOT / "tools" / "ops" / "fire_drill.sh"
    assert drill_script.is_file(), f"fire_drill.sh not found at {drill_script}"

    done = subprocess.run(
        [t.BASH, "tools/ops/fire_drill.sh"],
        cwd=t.ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert done.returncode == 0, f"fire_drill failed:\nSTDOUT:\n{done.stdout}\nSTDERR:\n{done.stderr}"
    assert "Total Alerts Tested : 13" in done.stdout
    assert "Successfully Verified: 13" in done.stdout
    assert "Failed              : 0" in done.stdout
