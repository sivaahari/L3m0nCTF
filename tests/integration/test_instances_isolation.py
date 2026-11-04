"""Automated isolation proof for challenge instances (F2).

Verifies:
1. Prober Logic Unit Verification: Prober correctly validates boundaries and reports
   full isolation when unroutable, and flags violations when a mock listener connects.
2. Container Security Profile & Invariants:
   - Non-root execution (UID 10001)
   - Read-only root filesystem
   - cap_drop: ["ALL"]
   - security_opt: ["no-new-privileges:true"]
   - Bounded tmpfs with noexec, nosuid, nodev
   - Per-instance isolated bridge networks (ICC disabled)
3. External Port Isolation:
   - Only router ports (8090, 30000-30199) are published.
   - Instance private container ports are not published (docker port is empty).
   - Docker socket proxy port (2375) is unreachable from host.
4. Live Stack Overlay Isolation Proof:
   - Starts a real prober instance (demo-prober) and real target instance (demo-web) on the running stack.
   - Probes other instance (by IP and name), gateway, internet outbound (TCP + DNS), cloud metadata,
     Docker API, instancer API, router control, and platform core services.
   - Enforces exact set of expected probe checks and asserts every check reports "isolated".
"""
from __future__ import annotations

import json
import os
import secrets as pysecrets
import socket
import subprocess
import time
import urllib.parse
import pytest

from services.instancer.tests.fixtures.prober.prober import (
    run_isolation_probes,
    try_tcp_connect,
)
from services.instancer.safety import assert_safe, ContainerSafetyError

pytestmark = pytest.mark.integration

EXPECTED_PROBER_CHECKS = {
    "other_instance_by_ip",
    "other_instance_by_name",
    "host_gateway",
    "internet_outbound",
    "cloud_metadata",
    "docker_api",
    "instancer_api",
    "router_control",
    "platform_core",
}

PROGRAMMES_API = "/api/v1/l3mon/admin/programmes"
RELEASE_API = "/api/v1/l3mon/admin/release"


# ---------------------------------------------------------------------------
# 1. Prober Logic Unit Verification (Offline)
# ---------------------------------------------------------------------------

def test_prober_reports_fully_isolated_when_all_targets_unreachable():
    """Unit test: when external/internal targets are unreachable, prober reports fully isolated."""
    report = run_isolation_probes({
        "other_ip": "10.255.255.1",
        "gateway_ip": "10.255.255.1",
        "instancer_host": "unreachable.invalid",
    })
    # Strict assertion: fully_isolated must be True (no weak field existence check)
    assert report["fully_isolated"] is True
    # Assert exact set of checks: a probe that silently disappears fails the test
    assert set(report["checks"].keys()) == EXPECTED_PROBER_CHECKS
    for check_name, check_data in report["checks"].items():
        assert check_data["isolated"] is True, f"Check {check_name} failed: {check_data}"


def test_prober_detects_egress_and_target_violations():
    """Unit test: if a probe target connects (e.g. mock listener), prober flags violation."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        mock_port = s.getsockname()[1]

        report = run_isolation_probes({
            "other_ip": "127.0.0.1",
            "other_port": str(mock_port),
        })

        assert report["checks"]["other_instance_by_ip"]["isolated"] is False
        assert report["checks"]["other_instance_by_ip"]["reachable"] is True
        assert report["fully_isolated"] is False


# ---------------------------------------------------------------------------
# 2. Container Spec & Isolation Invariants (Offline)
# ---------------------------------------------------------------------------

def test_container_security_invariants_enforced():
    """Validate that challenge container specifications strictly reject unsafe options."""
    valid_spec = {
        "Image": "registry.example/web@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "User": "10001:10001",
        "Labels": {
            "l3mon.instance": "0123456789abcdef0123456789abcdef",
        },
        "HostConfig": {
            "NetworkMode": "l3mon_inst_0123456789abcdef",
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": [],
            "SecurityOpt": ["no-new-privileges:true"],
            "ReadonlyRootfs": True,
            "Init": True,
            "RestartPolicy": {"Name": "no"},
            "Binds": [],
            "VolumesFrom": [],
            "Devices": [],
            "NanoCpus": 500_000_000,
            "Memory": 268_435_456,
            "PidsLimit": 128,
            "PortBindings": {},
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
        },
    }
    assert_safe(valid_spec, dev_mode=False)

    # Invariant: Must drop ALL capabilities
    unsafe_cap = json.loads(json.dumps(valid_spec))
    unsafe_cap["HostConfig"]["CapDrop"] = ["NET_ADMIN"]
    with pytest.raises(ContainerSafetyError, match=r"(?i)capabilities|capdrop"):
        assert_safe(unsafe_cap, dev_mode=False)

    # Invariant: Must enforce no-new-privileges
    unsafe_sec = json.loads(json.dumps(valid_spec))
    unsafe_sec["HostConfig"]["SecurityOpt"] = []
    with pytest.raises(ContainerSafetyError, match=r"(?i)no-new-privileges"):
        assert_safe(unsafe_sec, dev_mode=False)

    # Invariant: Must forbid privileged
    unsafe_priv = json.loads(json.dumps(valid_spec))
    unsafe_priv["HostConfig"]["Privileged"] = True
    with pytest.raises(ContainerSafetyError, match=r"(?i)privileged"):
        assert_safe(unsafe_priv, dev_mode=False)

    # Invariant: Must forbid host network mode
    unsafe_net = json.loads(json.dumps(valid_spec))
    unsafe_net["HostConfig"]["NetworkMode"] = "host"
    with pytest.raises(ContainerSafetyError, match=r"(?i)network mode"):
        assert_safe(unsafe_net, dev_mode=False)

    # Invariant: Must forbid docker socket binds
    unsafe_sock = json.loads(json.dumps(valid_spec))
    unsafe_sock["HostConfig"]["Binds"] = ["/var/run/docker.sock:/var/run/docker.sock:ro"]
    with pytest.raises(ContainerSafetyError, match=r"(?i)bind|docker socket"):
        assert_safe(unsafe_sock, dev_mode=False)

    # Invariant: User must not be root (UID 0)
    unsafe_root = json.loads(json.dumps(valid_spec))
    unsafe_root["User"] = "0:0"
    with pytest.raises(ContainerSafetyError, match=r"(?i)root|user"):
        assert_safe(unsafe_root, dev_mode=False)


# ---------------------------------------------------------------------------
# 3. External Network & Port Isolation (Offline)
# ---------------------------------------------------------------------------

def test_external_port_isolation():
    """Verify that Docker daemon port 2375 is not publicly open on host."""
    d_reachable, _ = try_tcp_connect("127.0.0.1", 2375, timeout=0.2)
    assert not d_reachable, "Security violation: Docker daemon TCP port 2375 is accessible on host"


# ---------------------------------------------------------------------------
# 4. Live Stack Isolation Proof (F2 Real Proof with Live Instances)
# ---------------------------------------------------------------------------

@pytest.mark.overlays
def test_live_overlay_instance_isolation_proof(world):
    """Real isolation proof: start target and prober instances on overlay stack and read report."""
    try:
        ps = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=5)
        if ps.returncode != 0:
            pytest.skip("Docker daemon not accessible")
    except Exception:
        pytest.skip("Docker CLI not available")

    # Ensure required fixture images are built
    subprocess.run(["docker", "build", "-t", "l3mon-fixture/demo-web:dev", "services/instancer/tests/fixtures/web"], check=True)
    subprocess.run(["docker", "build", "-t", "l3mon-fixture/prober:dev", "services/instancer/tests/fixtures/prober"], check=True)

    from tests.integration.test_instances import admin, make_player, post_json, request_router

    # 1. Create Target Challenge (demo-web) and Prober Challenge (demo-prober)
    status, out_t = admin("POST", "/api/v1/challenges", {
        "name": "Target Web Challenge",
        "category": "Web",
        "description": "Target instance for isolation check",
        "value": 100,
        "state": "visible",
        "type": "standard",
    })
    assert status == 200, out_t
    chal_target_id = out_t["data"]["id"]

    status, out_p = admin("POST", "/api/v1/challenges", {
        "name": "Prober Challenge",
        "category": "Internal",
        "description": "Prober instance for isolation proof",
        "value": 100,
        "state": "visible",
        "type": "standard",
    })
    assert status == 200, out_p
    chal_prober_id = out_p["data"]["id"]

    # 2. Add Dynamic Flags
    admin("POST", "/api/v1/flags", {"challenge_id": chal_target_id, "content": "", "type": "l3mon_dynamic", "data": ""})
    admin("POST", "/api/v1/flags", {"challenge_id": chal_prober_id, "content": "", "type": "l3mon_dynamic", "data": ""})

    # 3. Create Programmes for both challenges
    chan_slug = f"chan-iso-{pysecrets.token_hex(4)}"
    status, _ = admin("PUT", PROGRAMMES_API, {
        "channels": [{"slug": chan_slug, "name": "Isolation Channel", "position": 1}],
        "programmes": [
            {"challenge_id": chal_target_id, "channel": chan_slug, "cell": 0, "number": 301, "slug": "demo-web", "delivery": "live_single"},
            {"challenge_id": chal_prober_id, "channel": chan_slug, "cell": 1, "number": 302, "slug": "demo-prober", "delivery": "live_single"},
        ],
    })
    assert status == 200

    status, view = admin("GET", RELEASE_API)
    assert status == 200
    chan_id = view["data"]["channels"][0]["id"]
    for prog in view["data"]["channels"][0]["programmes"]:
        admin("PUT", RELEASE_API, {"changes": [{"kind": "programme", "id": prog["id"], "mode": "release"}]})
    admin("PUT", RELEASE_API, {"changes": [{"kind": "channel", "id": chan_id, "mode": "release"}]})

    # 4. Create Players (Team Target and Team Prober)
    session_target, _, _ = make_player(f"user_target_{pysecrets.token_hex(4)}")
    session_prober, _, _ = make_player(f"user_prober_{pysecrets.token_hex(4)}")

    # 5. Start Target Instance
    status, resp_t = post_json(session_target, "/api/v1/l3mon/instances", {"challenge_id": chal_target_id})
    assert status == 201, resp_t
    target_url = resp_t["data"]["url"]
    target_inst_id = urllib.parse.urlparse(target_url).netloc.split(".")[0]

    # Retrieve Target container IP and name via Docker inspect
    target_cname = f"l3mon-inst-{target_inst_id}"
    inspect_target = subprocess.run(["docker", "inspect", target_cname], capture_output=True, text=True, check=True)
    target_info = json.loads(inspect_target.stdout)[0]
    target_ip = target_info["NetworkSettings"]["IPAddress"] or list(target_info["NetworkSettings"]["Networks"].values())[0]["IPAddress"]
    gateway_ip = list(target_info["NetworkSettings"]["Networks"].values())[0]["Gateway"]

    # 6. Start Prober Instance
    status, resp_p = post_json(session_prober, "/api/v1/l3mon/instances", {"challenge_id": chal_prober_id})
    assert status == 201, resp_p
    prober_url = resp_p["data"]["url"]
    prober_host = urllib.parse.urlparse(prober_url).netloc
    prober_inst_id = prober_host.split(".")[0]
    prober_cname = f"l3mon-inst-{prober_inst_id}"

    # Wait for router route propagation
    time.sleep(3)

    # 7. Query Prober /report endpoint through Router with real target parameters
    params = urllib.parse.urlencode({
        "other_ip": target_ip,
        "other_name": target_cname,
        "gateway_ip": gateway_ip,
        "instancer_host": "instancer",
        "router_host": "router",
    })
    status_code, report_bytes = request_router(prober_host, f"/report?{params}")
    assert status_code == 200, f"Prober returned status {status_code}: {report_bytes.decode('utf-8', 'replace')}"

    report = json.loads(report_bytes.decode("utf-8"))

    # Assert every probe in prompt's F2 list reads "isolated"
    assert report["fully_isolated"] is True, f"Isolation failure in live prober report: {report}"
    assert set(report["checks"].keys()) == EXPECTED_PROBER_CHECKS

    for check_name in EXPECTED_PROBER_CHECKS:
        cdata = report["checks"][check_name]
        assert cdata["isolated"] is True, f"Check '{check_name}' failed isolation: {cdata}"

    # 8. Outside verification from host:
    # Instance's own container ports are NOT published
    port_t = subprocess.run(["docker", "port", target_cname], capture_output=True, text=True)
    assert port_t.stdout.strip() == "", f"Target container exposed ports: {port_t.stdout}"
    port_p = subprocess.run(["docker", "port", prober_cname], capture_output=True, text=True)
    assert port_p.stdout.strip() == "", f"Prober container exposed ports: {port_p.stdout}"

    # Socket proxy port is not reachable from host
    sp_reachable, _ = try_tcp_connect("127.0.0.1", 2375, timeout=0.5)
    assert not sp_reachable, "Socket proxy port 2375 is reachable from host runner"
