"""Resilience and operational failure drills for L3m0nCTF 2026.

Automates the live dress-rehearsal drills against the running Compose stack.
By design, these drills simulate container stops and worker crashes, so they
only run when explicitly requested:
    python -m pytest tests/integration/test_ops_drills.py -q --run-drill
(or with L3MON_DRILL=1).
"""
from __future__ import annotations

import json
import subprocess
import time

import pytest
import test_stack as t

pytestmark = [pytest.mark.integration, pytest.mark.drill]


def test_drill_worker_crash_recovers():
    """Drill 1: A killed CTFd gunicorn worker process is immediately respawned by master."""
    t.wait_healthy("ctfd", 60)
    status, _, body = t.request("/readyz")
    assert status == 200, f"/readyz must be healthy before drill: {body.decode()}"

    ctfd_cid = t.compose("ps", "-q", "ctfd").stdout.strip()
    assert ctfd_cid, "ctfd container must be running"

    # Find worker PIDs (processes whose PPID is neither 0 nor 1)
    find_cmd = [
        "docker",
        "exec",
        ctfd_cid,
        "/opt/venv/bin/python",
        "-c",
        (
            "import os\n"
            "workers = []\n"
            "for p in os.listdir('/proc'):\n"
            "    if p.isdigit():\n"
            "        try:\n"
            "            with open(f'/proc/{p}/stat') as f:\n"
            "                ppid = f.read().split()[3]\n"
            "                if ppid not in ('0', '1'):\n"
            "                    workers.append(p)\n"
            "        except Exception:\n"
            "            pass\n"
            "print(' '.join(workers))\n"
        ),
    ]
    pids = subprocess.run(find_cmd, capture_output=True, text=True, check=True).stdout.split()
    assert pids, "At least one gunicorn worker process must exist"
    target_pid = pids[0]

    # Kill target worker with SIGKILL using Python os.kill
    subprocess.run(
        ["docker", "exec", ctfd_cid, "/opt/venv/bin/python", "-c", f"import os, signal; os.kill({target_pid}, signal.SIGKILL)"],
        check=True,
    )

    # Verify platform recovers immediately (within 10 seconds)
    recovered = False
    start_time = time.time()
    for _ in range(20):
        time.sleep(0.5)
        try:
            status, _, _ = t.request("/readyz")
            if status == 200:
                recovered = True
                break
        except Exception:
            pass

    assert recovered, f"CTFd failed to recover within 10s after worker {target_pid} killed"
    elapsed = time.time() - start_time
    print(f"\n[DRILL 1 SUCCESS] Worker killed and platform responsive in {elapsed:.2f}s")


def test_drill_redis_down_and_recovery():
    """Drill 2: Stopping Redis causes /readyz to report 503; restarting restores 200."""
    t.wait_healthy("cache", 60)
    status, _, _ = t.request("/readyz")
    assert status == 200

    # Stop cache container
    t.compose("stop", "cache", timeout=30)
    try:
        # /readyz must fail with 503 not_ready (strict 1-second timeout)
        status, _, body = t.request("/readyz")
        assert status == 503, f"Expected 503 when Redis is down, got {status}: {body.decode()}"
        data = json.loads(body)
        assert data.get("status") == "not_ready"
    finally:
        # Restore cache container
        t.compose("start", "cache", timeout=30)
        t.wait_healthy("cache", 60)

    # Wait for /readyz to return 200
    recovered = False
    start_time = time.time()
    for _ in range(30):
        time.sleep(0.5)
        try:
            status, _, body = t.request("/readyz")
            if status == 200:
                recovered = True
                break
        except Exception:
            pass

    assert recovered, "Platform failed to recover /readyz after Redis restarted"
    elapsed = time.time() - start_time
    print(f"\n[DRILL 2 SUCCESS] Redis stop -> 503 detected -> restart -> readyz in {elapsed:.2f}s")


def test_drill_ctfd_down_and_slate_recovery():
    """Drill 3: Stopping CTFd causes Nginx to serve static restarting slates; restarting recovers."""
    t.wait_healthy("ctfd", 60)
    status, _, _ = t.request("/readyz")
    assert status == 200

    # Stop ctfd container
    t.compose("stop", "ctfd", timeout=30)
    try:
        # 1. HTML request must receive 502/504 with static down slate
        status, headers, body = t.request("/", headers={"Accept": "text/html"})
        assert status in (502, 504), f"Expected 502/504 for page load during app down, got {status}"
        assert b"restarting" in body.lower() or b"l3mon" in body.lower()

        # 2. API request must receive 502/504 with JSON error
        status, headers, body = t.request("/api/v1/l3mon/board", headers={"Accept": "application/json"})
        assert status in (502, 504)
        data = json.loads(body)
        assert data.get("error") == "restarting"
    finally:
        # Restore ctfd container
        t.compose("start", "ctfd", timeout=30)
        t.wait_healthy("ctfd", 180)

    # Verify recovery
    recovered = False
    start_time = time.time()
    for _ in range(30):
        time.sleep(0.5)
        try:
            status, _, _ = t.request("/readyz")
            if status == 200:
                recovered = True
                break
        except Exception:
            pass

    assert recovered, "Platform failed to recover /readyz after CTFd restarted"
    elapsed = time.time() - start_time
    print(f"\n[DRILL 3 SUCCESS] CTFd down -> 502/504 slate verified -> restart in {elapsed:.2f}s")


def test_drill_maintenance_mode_lifecycle():
    """Drill 4: Maintenance mode flag file enforces 503 on general traffic with crew and health bypass."""
    t.wait_healthy("ctfd", 60)
    # Ensure maintenance mode is off initially
    subprocess.run([t.BASH, str(t.ROOT / "tools" / "ops" / "maintenance.sh"), "off"], check=True)

    status, _, _ = t.request("/")
    assert status in (200, 302, 303)

    # Enable maintenance mode
    subprocess.run(
        [
            t.BASH,
            str(t.ROOT / "tools" / "ops" / "maintenance.sh"),
            "on",
            "--message",
            "Drill maintenance in progress",
            "--until",
            "2099-01-01T00:00:00Z",
        ],
        check=True,
    )

    nobody_nets = t.ROOT / "tests" / "integration" / "fixtures" / "admin-nets-nobody.conf"
    # Switch nginx to treat client as outside player
    t.compose("up", "-d", "--wait", "--no-deps", "nginx", env={"ADMIN_NETS_FILE": str(nobody_nets), "L3MON_OVERLAYS": "ops"})
    t.wait_healthy("nginx", 60)

    try:
        # 1. Outside player gets 503 maintenance slate
        status, _, body = t.request("/", headers={"Accept": "text/html"})
        assert status == 503
        assert b"maintenance" in body.lower()

        # 2. Outside API gets 503 JSON (as busy, with retry_after)
        status, _, body = t.request("/api/v1/l3mon/board", headers={"Accept": "application/json"})
        assert status == 503
        data = json.loads(body)
        assert data.get("error") == "busy"

        # 3. Healthcheck bypasses maintenance mode
        status, _, body = t.request("/readyz")
        assert status == 200, f"/readyz must bypass maintenance mode: {body.decode()}"

        status, _, body = t.request("/l3mon/healthz")
        assert status == 200, f"/l3mon/healthz must bypass maintenance mode: {body.decode()}"

        # 4. Crew bypass: restore normal admin nets so 127.0.0.1 is crew
        t.compose("up", "-d", "--wait", "--no-deps", "nginx", env={"L3MON_OVERLAYS": "ops"})
        t.wait_healthy("nginx", 60)
        status, _, body = t.request("/login")
        assert status == 200, f"Expected crew bypass 200, got {status}"
    finally:
        # Turn maintenance off
        subprocess.run([t.BASH, str(t.ROOT / "tools" / "ops" / "maintenance.sh"), "off"], check=True)
        t.compose("up", "-d", "--wait", "--no-deps", "nginx", env={"L3MON_OVERLAYS": "ops"})
        t.wait_healthy("nginx", 60)

    # General traffic restored
    status, _, _ = t.request("/")
    assert status in (200, 302, 303)
    print("\n[DRILL 4 SUCCESS] Maintenance mode cycle: on -> 503 -> bypasses verified -> off -> restored")
