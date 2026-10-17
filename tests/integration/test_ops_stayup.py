"""Integration tests for SP9 operations and resilience (Milestone 1: stay-up behaviour).

Covers:
- /readyz healthcheck and cookie stripping
- /l3mon/metrics 404 to outside and Prometheus formatting internally
- Maintenance mode (Level 3): slates, countdown, API JSON error, crew bypass, health & CTFtime bypass
- Down mode (502/504): down slate, API restarting JSON error
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

import test_stack as t

pytestmark = pytest.mark.integration

FIXTURE_NOBODY = t.ROOT / "tests" / "integration" / "fixtures" / "admin-nets-nobody.conf"
MAINTENANCE_SCRIPT = t.ROOT / "tools" / "ops" / "maintenance.sh"


def bash(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    fixed = [a.replace(chr(92), "/") for a in args]
    return subprocess.run([t.BASH, *fixed], capture_output=True, text=True, timeout=timeout, cwd=t.ROOT)


@pytest.fixture
def outside_nets():
    """Run test with nginx treating standard client as outside visitor and ops overlay active."""
    t.compose("up", "-d", "--wait", "--no-deps", "nginx", env={"ADMIN_NETS_FILE": str(FIXTURE_NOBODY), "L3MON_OVERLAYS": "ops"})
    t.wait_healthy("nginx", 60)
    yield
    t.compose("up", "-d", "--wait", "--no-deps", "nginx", env={"L3MON_OVERLAYS": "ops"})
    t.wait_healthy("nginx", 60)


def test_readyz_returns_200_and_no_cookie_on_live_stack():
    status, headers, body = t.request("/readyz")
    assert status == 200, f"readyz status {status}, body {body}"
    data = json.loads(body)
    assert data == {"status": "ready"}
    # Nginx hides Set-Cookie
    assert not any(k.lower() == "set-cookie" for k, _ in headers)
    assert any(k.lower() == "cache-control" and "no-store" in v for k, v in headers)


def test_metrics_is_404_from_outside_and_accessible_internally():
    # Outside request through Nginx returns 404
    status, _, _ = t.request("/l3mon/metrics")
    assert status == 404

    # Internal scrape from within the docker network succeeds
    scrape = subprocess.run(
        ["docker", "exec", "-i", "l3mon-nginx-1", "wget", "-q", "-O", "-", "--header", "Host: localhost", "http://ctfd:8000/l3mon/metrics"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert scrape.returncode == 0
    text = scrape.stdout
    assert "l3mon_event_phase{" in text
    assert "l3mon_tick_value " in text
    assert "l3mon_build_info{" in text
    assert "L3m0n" not in text


def test_maintenance_mode_lifecycle_and_bypasses(outside_nets):
    # Ensure clean state first
    bash(str(MAINTENANCE_SCRIPT), "off")

    try:
        # 1. Enable maintenance
        res = bash(str(MAINTENANCE_SCRIPT), "on", "--until", "2026-11-28T10:00:00Z", "--message", "Scheduled platform upgrade")
        assert res.returncode == 0
        assert "Maintenance mode: ON" in res.stdout

        # 2. Outside visitor page load receives 503 and maintenance slate
        status, headers, body = t.request("/challenges")
        assert status == 503
        html = body.decode("utf-8", errors="replace")
        assert "Scheduled Maintenance" in html
        assert "L3m0nCTF 2026" in html
        assert "Request ID:" in html

        # 3. Outside visitor API call receives 503 and JSON error
        status, headers, body = t.request("/api/v1/users/me")
        assert status == 503
        data = json.loads(body)
        assert data.get("error") == "busy"
        assert data.get("retry_after") == 15
        assert any(k.lower() == "retry-after" for k, _ in headers)

        # 4. Health checks are exempt and return 200
        status, _, _ = t.request("/healthcheck")
        assert status == 200
        status, _, _ = t.request("/l3mon/healthz")
        assert status == 200
        status, _, _ = t.request("/readyz")
        assert status == 200

        # 5. Public CTFtime feeds are exempt
        status, _, _ = t.request("/ctftime/standings.json")
        assert status == 200

        # 6. Public static slates assets are exempt
        status, _, body = t.request("/slates/slate.css")
        assert status == 200 and b":root" in body

        # 7. Organisers bypass maintenance:
        # Recreating nginx with normal admin nets restores organiser access
        t.compose("up", "-d", "--wait", "--no-deps", "nginx", env={"L3MON_OVERLAYS": "ops"})
        t.wait_healthy("nginx", 60)
        # As organiser (from localhost in dev), /challenges or /login passes through
        status, _, body = t.request("/login")
        assert status == 200, f"Expected crew bypass 200, got {status}"

    finally:
        bash(str(MAINTENANCE_SCRIPT), "off")
        t.compose("up", "-d", "--wait", "--no-deps", "nginx", env={"L3MON_OVERLAYS": "ops"})
        t.wait_healthy("nginx", 60)

    # After maintenance is off, outside traffic succeeds
    status, _, _ = t.request("/login")
    assert status == 200


def test_upstream_down_serves_static_down_slate_and_api_restarting_json(outside_nets):
    # Stop CTFd container to simulate upstream crash
    t.compose("stop", "ctfd")
    try:
        # Page load receives 502 with static down slate
        status, headers, body = t.request("/challenges")
        assert status in (502, 504)
        html = body.decode("utf-8", errors="replace")
        assert "Platform Restarting" in html
        assert "L3m0nCTF 2026" in html
        assert "Request ID:" in html

        # API call receives 502 with JSON error
        status, headers, body = t.request("/api/v1/users/me")
        assert status in (502, 504)
        data = json.loads(body)
        assert data == {"error": "restarting"}
    finally:
        t.compose("start", "ctfd")
        t.wait_healthy("ctfd", 180)
