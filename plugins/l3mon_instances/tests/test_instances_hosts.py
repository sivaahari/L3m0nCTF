"""Tests for multi-host scheduler, capacity tracking, per-host tokens, and route-level Probes A-E.

Verifies:
1. Host configuration loading and strict validation.
2. Per-host token isolation:
   - Single-host / implicit "default" host uses platform INSTANCER_TOKEN.
   - Multi-host mode requires specific INSTANCER_TOKEN_<NAME>.
3. 5-second TTL capacity cache.
4. Optimistic local counter under concurrent starts.
5. 30-second down rule and recovery.
6. Failover to available hosts and 503 no_capacity when all hosts are exhausted/down.
7. Real Route-Level Probes A through E:
   - Probe A: Failed start on h1 marks h1 down for 30s and retries on healthy h2.
   - Probe B: Racing starts across 2 hosts -> loser stops losing host orphan and returns existing winner with 200.
   - Probe C: Player queries instance whose host is down -> reported as state="error".
   - Probe D: Admin kill-all dispatches to all configured hosts and aggregates stopped counts.
   - Probe E: Racing starts on SAME host -> does NOT call stop_instance on winner's instance.
"""
from __future__ import annotations

import collections
import datetime
import json
import os
import tempfile
import time
from unittest import mock
import pytest

try:
    from CTFd.models import Challenges, Teams, Users, db
    from CTFd.plugins.l3mon_core.models import Channel, Programme
    from CTFd.plugins.l3mon_instances.client import InstancerClient, InstancerClientError, set_client_override
    from CTFd.plugins.l3mon_instances.hosts import (
        HostCapacityTracker,
        get_host_tracker,
        get_token_for_host,
        load_hosts_config,
        set_host_tracker,
        validate_host_entry,
    )
    from CTFd.plugins.l3mon_instances.models import L3monInstance, L3monInstanceLog
    from CTFd.utils import set_config
    from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, login_as_user, register_user
    ctfd_available = True
except ImportError:
    ctfd_available = False

pytestmark = pytest.mark.skipif(not ctfd_available, reason="CTFd package required")


@pytest.fixture(autouse=True)
def set_default_test_host_tokens(monkeypatch):
    for h in ["H1", "H2", "H3", "H4"]:
        monkeypatch.setenv(f"INSTANCER_TOKEN_{h}", f"test_token_{h.lower()}")


class MockAgentClient:
    def __init__(self, host_name: str, slots_total: int = 50, slots_used: int = 0, should_fail: bool = False):
        self.host_name = host_name
        self.slots_total = slots_total
        self.slots_used = slots_used
        self.should_fail = should_fail
        self.capacity_probe_count = 0
        self.killed_count = 0
        self.stopped_instances: list[str] = []
        self.started_instances: list[dict] = []
        self.next_id = 1

    def stop_instance(self, instance_id: str) -> None:
        self.stopped_instances.append(instance_id)

    def get_capacity(self) -> dict:
        self.capacity_probe_count += 1
        if self.should_fail:
            raise InstancerClientError(503, "upstream_unavailable", f"Host {self.host_name} unreachable")
        return {
            "host_name": self.host_name,
            "slots_total": self.slots_total,
            "slots_used": self.slots_used,
            "slots_free": max(0, self.slots_total - self.slots_used),
            "mem_mb_total": 8192,
            "mem_mb_used": 1024,
        }

    def start_instance(self, team_id: int, challenge_id: int, slug: str, flag: str, ttl_seconds: int | None = None) -> dict:
        if self.should_fail:
            raise InstancerClientError(500, "docker_broken", f"Host {self.host_name} Docker daemon broken")
        inst_id = f"inst_{self.host_name}_{self.next_id}"
        self.next_id += 1
        now_ts = time.time()
        inst = {
            "id": inst_id,
            "team_id": team_id,
            "challenge_id": challenge_id,
            "slug": slug,
            "state": "running",
            "protocol": "http",
            "url": f"http://{inst_id}.inst.l3m0nctf.xyz/",
            "host": None,
            "port": None,
            "created_at": now_ts,
            "expires_at": now_ts + (ttl_seconds or 2700),
            "renewals": 0,
        }
        self.started_instances.append(inst)
        return inst

    def kill_instances(self, team_id=None, challenge_id=None) -> int:
        self.killed_count += 3
        return 3


# ---------------------------------------------------------------------------
# 1. Configuration and Token Resolution Tests
# ---------------------------------------------------------------------------

def test_token_resolution_default_host_uses_platform_token(monkeypatch):
    """BLOCKING 1: Single-host / implicit default host falls back to INSTANCER_TOKEN."""
    monkeypatch.delenv("INSTANCER_TOKEN_DEFAULT", raising=False)
    monkeypatch.setenv("INSTANCER_TOKEN", "platform_global_token")

    # The default host must resolve the platform token even when allow_fallback=False
    token = get_token_for_host("default", allow_fallback=False)
    assert token == "platform_global_token"


def test_token_resolution_listed_host_requires_specific_token(monkeypatch):
    """BLOCKING 1: Listed hosts in multi-host mode strictly require their own token."""
    monkeypatch.delenv("INSTANCER_TOKEN_H1", raising=False)
    monkeypatch.delenv("INSTANCER_TOKEN_H2", raising=False)
    monkeypatch.setenv("INSTANCER_TOKEN", "platform_global_token")
    monkeypatch.setenv("INSTANCER_TOKEN_H1", "secret_host_1_only")

    # Host h1 gets dedicated token
    token_h1 = get_token_for_host("h1")
    assert token_h1 == "secret_host_1_only"

    # Host h2 without its own token is strictly refused in multi-host mode
    with pytest.raises(ValueError, match="INSTANCER_TOKEN_H2"):
        get_token_for_host("h2", allow_fallback=False)

    # In single-host/dev mode with allow_fallback=True, falls back
    assert get_token_for_host("h2", allow_fallback=True) == "platform_global_token"


def test_load_hosts_config_valid():
    sample_hosts = [
        {
            "name": "h1",
            "url": "http://10.200.1.10:8300",
            "router_url": "http://10.200.1.10:8090",
            "host": "h1.inst.l3m0nctf.xyz",
            "weight": 1.0,
            "max_slots": 50,
            "ca_cert": None,
        },
        {
            "name": "h2",
            "url": "https://10.200.1.11:8300",
            "router_url": "https://10.200.1.11:8090",
            "public_tcp_host": "h2.inst.l3m0nctf.xyz",
            "weight": 2.0,
            "max_slots": 100,
            "ca_cert": None,
        },
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(sample_hosts, f)
        cfg_path = f.name

    try:
        hosts = load_hosts_config(cfg_path)
        assert len(hosts) == 2
        assert hosts[0]["name"] == "h1"
        assert hosts[0]["public_tcp_host"] == "h1.inst.l3m0nctf.xyz"
        assert hosts[0]["weight"] == 1.0
        assert hosts[1]["name"] == "h2"
        assert hosts[1]["weight"] == 2.0
    finally:
        os.unlink(cfg_path)


def test_load_hosts_config_validation_errors():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump([], f)
        p_empty = f.name
    try:
        with pytest.raises(ValueError, match="must not be empty"):
            load_hosts_config(p_empty)
    finally:
        os.unlink(p_empty)

    with pytest.raises(ValueError, match="Invalid host 'name'"):
        validate_host_entry({"name": "bad name!", "url": "http://10.0.0.1:8300", "router_url": "http://10.0.0.1:8090", "host": "h1.local"})

    with pytest.raises(ValueError, match="invalid 'url'"):
        validate_host_entry({"name": "h1", "url": "ftp://10.0.0.1:8300", "router_url": "http://10.0.0.1:8090", "host": "h1.local"})

    with pytest.raises(ValueError, match="missing required 'public_tcp_host'"):
        validate_host_entry({"name": "h1", "url": "http://10.0.0.1:8300", "router_url": "http://10.0.0.1:8090"})

    with pytest.raises(ValueError, match="invalid 'public_tcp_host'"):
        validate_host_entry({"name": "h1", "url": "http://10.0.0.1:8300", "router_url": "http://10.0.0.1:8090", "host": "http://h1.local"})

    with pytest.raises(ValueError, match="invalid 'weight'"):
        validate_host_entry({"name": "h1", "url": "http://10.0.0.1:8300", "router_url": "http://10.0.0.1:8090", "host": "h1.local", "weight": 0})

    with pytest.raises(ValueError, match="ca_cert file not found"):
        validate_host_entry({"name": "h1", "url": "https://10.0.0.1:8300", "router_url": "https://10.0.0.1:8090", "host": "h1.local", "ca_cert": "/nonexistent/ca.pem"})


# ---------------------------------------------------------------------------
# 2. Capacity Tracking and Scheduling Logic Tests
# ---------------------------------------------------------------------------

def test_capacity_5s_caching():
    current_time = 1000.0
    mock_client = MockAgentClient("h1", slots_total=50, slots_used=10)
    host = {
        "name": "h1",
        "url": "http://10.0.1.10:8300",
        "router_url": "http://10.0.1.10:8090",
        "host": "h1.inst.l3m0nctf.xyz",
        "weight": 1.0,
        "max_slots": 50,
        "ca_cert": None,
    }

    tracker = HostCapacityTracker(hosts=[host], time_provider=lambda: current_time)
    tracker.set_client_factory(lambda h, tok: mock_client)

    cap1 = tracker.get_capacity("h1")
    assert cap1["slots_free"] == 40
    assert mock_client.capacity_probe_count == 1

    current_time = 1003.0
    cap2 = tracker.get_capacity("h1")
    assert cap2["slots_free"] == 40
    assert mock_client.capacity_probe_count == 1

    current_time = 1006.0
    cap3 = tracker.get_capacity("h1")
    assert cap3["slots_free"] == 40
    assert mock_client.capacity_probe_count == 2


def test_optimistic_local_counter_concurrent_starts():
    current_time = 1000.0
    client_h1 = MockAgentClient("h1", slots_total=50, slots_used=10)
    client_h2 = MockAgentClient("h2", slots_total=50, slots_used=10)
    clients = {"h1": client_h1, "h2": client_h2}

    hosts = [
        {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 50, "ca_cert": None},
        {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090", "host": "h2.xyz", "weight": 1.0, "max_slots": 50, "ca_cert": None},
    ]

    tracker = HostCapacityTracker(hosts=hosts, time_provider=lambda: current_time)
    tracker.set_client_factory(lambda h, tok: clients[h["name"]])

    chosen1 = tracker.select_host_for_start()
    name1 = chosen1["name"]

    chosen2 = tracker.select_host_for_start()
    name2 = chosen2["name"]
    assert name1 != name2, "Optimistic counter must cause subsequent immediate start to choose alternate host"

    chosen3 = tracker.select_host_for_start()
    assert chosen3["name"] == name1


def test_30s_down_rule_and_recovery():
    current_time = 1000.0
    client_h1 = MockAgentClient("h1", slots_total=50, slots_used=5, should_fail=True)
    client_h2 = MockAgentClient("h2", slots_total=50, slots_used=10, should_fail=False)

    clients = {"h1": client_h1, "h2": client_h2}
    hosts = [
        {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 50, "ca_cert": None},
        {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090", "host": "h2.xyz", "weight": 1.0, "max_slots": 50, "ca_cert": None},
    ]

    tracker = HostCapacityTracker(hosts=hosts, time_provider=lambda: current_time)
    tracker.set_client_factory(lambda h, tok: clients[h["name"]])

    chosen = tracker.select_host_for_start()
    assert chosen["name"] == "h2"
    assert tracker.is_host_down("h1", now_ts=current_time) is True

    current_time = 1015.0
    probe_count_before = client_h1.capacity_probe_count
    chosen_15 = tracker.select_host_for_start()
    assert chosen_15["name"] == "h2"
    assert client_h1.capacity_probe_count == probe_count_before

    current_time = 1031.0
    client_h1.should_fail = False
    client_h1.slots_used = 2
    chosen_rec = tracker.select_host_for_start()
    assert chosen_rec["name"] == "h1"
    assert tracker.is_host_down("h1", now_ts=current_time) is False


def test_all_hosts_full_or_down_raises_no_capacity():
    current_time = 1000.0
    client_h1 = MockAgentClient("h1", slots_total=50, slots_used=50)
    client_h2 = MockAgentClient("h2", should_fail=True)

    clients = {"h1": client_h1, "h2": client_h2}
    hosts = [
        {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 50, "ca_cert": None},
        {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090", "host": "h2.xyz", "weight": 1.0, "max_slots": 50, "ca_cert": None},
    ]

    tracker = HostCapacityTracker(hosts=hosts, time_provider=lambda: current_time)
    tracker.set_client_factory(lambda h, tok: clients[h["name"]])

    with pytest.raises(InstancerClientError) as exc_info:
        tracker.select_host_for_start()

    assert exc_info.value.status == 503
    assert exc_info.value.error_code == "no_capacity"
    assert exc_info.value.retry_after == 30


def test_multihost_failover_and_rebalance_at_scale():
    current_time = 1000.0
    client_h1 = MockAgentClient("h1", slots_total=30, slots_used=0)
    client_h2 = MockAgentClient("h2", slots_total=30, slots_used=0)
    client_h3 = MockAgentClient("h3", slots_total=30, slots_used=0)
    client_h4 = MockAgentClient("h4", slots_total=30, slots_used=0)
    clients = {"h1": client_h1, "h2": client_h2, "h3": client_h3, "h4": client_h4}

    hosts = [
        {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 30},
        {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090", "host": "h2.xyz", "weight": 1.0, "max_slots": 30},
        {"name": "h3", "url": "http://10.0.1.12:8300", "router_url": "http://10.0.1.12:8090", "host": "h3.xyz", "weight": 1.0, "max_slots": 30},
        {"name": "h4", "url": "http://10.0.1.13:8300", "router_url": "http://10.0.1.13:8090", "host": "h4.xyz", "weight": 1.0, "max_slots": 30},
    ]

    tracker = HostCapacityTracker(hosts=hosts, time_provider=lambda: current_time)
    tracker.set_client_factory(lambda h, tok: clients[h["name"]])

    counts: dict[str, int] = collections.defaultdict(int)
    for _ in range(40):
        chosen = tracker.select_host_for_start()
        counts[chosen["name"]] += 1
        clients[chosen["name"]].slots_used += 1

    assert all(counts[h["name"]] == 10 for h in hosts)

    current_time += 6.0
    client_h2.should_fail = True
    client_h4.should_fail = True
    counts_phase2: dict[str, int] = collections.defaultdict(int)

    for _ in range(20):
        chosen = tracker.select_host_for_start()
        counts_phase2[chosen["name"]] += 1
        clients[chosen["name"]].slots_used += 1

    assert counts_phase2["h2"] == 0
    assert counts_phase2["h4"] == 0
    assert counts_phase2["h1"] == 10
    assert counts_phase2["h3"] == 10
    assert tracker.is_host_down("h2") is True
    assert tracker.is_host_down("h4") is True

    current_time += 31.0
    client_h2.should_fail = False
    client_h4.should_fail = False

    counts_phase3: dict[str, int] = collections.defaultdict(int)
    for _ in range(20):
        chosen = tracker.select_host_for_start()
        counts_phase3[chosen["name"]] += 1
        clients[chosen["name"]].slots_used += 1

    assert counts_phase3["h2"] == 10
    assert counts_phase3["h4"] == 10
    assert counts_phase3["h1"] == 0
    assert counts_phase3["h3"] == 0

    assert client_h1.slots_used == 20
    assert client_h2.slots_used == 20
    assert client_h3.slots_used == 20
    assert client_h4.slots_used == 20


# ---------------------------------------------------------------------------
# 3. Real Route-Level Probes A through E (Calling actual Flask routes)
# ---------------------------------------------------------------------------

def _setup_route_world(app):
    """Set up contest, live challenge, and player team in CTFd database."""
    set_config("start", 1000)
    set_config("end", 5000000000)
    admin = login_as_user(app, "admin")
    chal = gen_challenge(db, name="Live Chal Hosts", state="visible")
    c_id = chal.id
    db.session.commit()

    admin.put("/api/v1/l3mon/admin/programmes", json={
        "channels": [{"slug": "web", "name": "Web Channel", "position": 1}],
        "programmes": [
            {"challenge_id": c_id, "channel": "web", "cell": 0, "number": 101, "slug": "chal-hosts", "delivery": "live_single"},
        ],
    })
    release = [{"kind": "channel", "id": Channel.query.one().id, "mode": "release"}] + [
        {"kind": "programme", "id": p.id, "mode": "release"} for p in Programme.query.all()
    ]
    admin.put("/api/v1/l3mon/admin/release", json={"changes": release})

    register_user(app, name="alice", email="alice@example.com")
    alice = login_as_user(app, "alice")
    alice.get("/team")
    with alice.session_transaction() as s:
        nonce = s.get("nonce")
    alice.post("/teams/new", data={"name": "team_hosts", "password": "password", "nonce": nonce})

    return c_id, alice, admin


def test_route_probe_a_failed_start_marks_host_down_and_retries():
    """Probe A (Real Route): Failed start on h1 marks h1 down for 30s and retries on h2."""
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c_id, alice, _ = _setup_route_world(app)

        client_h1 = MockAgentClient("h1", slots_total=50, slots_used=5, should_fail=True)  # fails start
        client_h2 = MockAgentClient("h2", slots_total=50, slots_used=20, should_fail=False) # healthy
        clients = {"h1": client_h1, "h2": client_h2}
        hosts = [
            {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 50},
            {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090", "host": "h2.xyz", "weight": 1.0, "max_slots": 50},
        ]

        tracker = HostCapacityTracker(hosts=hosts)
        tracker.set_client_factory(lambda h, tok: clients[h["name"]])
        set_host_tracker(tracker)

        try:
            # Player POSTs /api/v1/l3mon/instances: attempt 0 fails on h1, marks down, retries on h2!
            r = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id})
            assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.get_data(as_text=True)}"
            data = r.get_json()["data"]
            assert data["host_name"] == "h2"
            assert tracker.is_host_down("h1") is True

            # Subsequent start immediately selects h2 (h1 remains down)
            chosen = tracker.select_host_for_start()
            assert chosen["name"] == "h2"
        finally:
            set_host_tracker(None)
            destroy_ctfd(app)


def test_route_probe_b_racing_starts_across_hosts_returns_existing_and_stops_orphan():
    """Probe B (Real Route): Two requests race across 2 hosts. Loser stops orphaned instance and returns 200."""
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c_id, alice, _ = _setup_route_world(app)

        client_h1 = MockAgentClient("h1", slots_total=50, slots_used=5)
        client_h2 = MockAgentClient("h2", slots_total=50, slots_used=5)
        clients = {"h1": client_h1, "h2": client_h2}
        hosts = [
            {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 50},
            {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090", "host": "h2.xyz", "weight": 1.0, "max_slots": 50},
        ]

        tracker = HostCapacityTracker(hosts=hosts)
        tracker.set_client_factory(lambda h, tok: clients[h["name"]])
        set_host_tracker(tracker)

        try:
            # 1. Winning request started on h1
            r1 = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id})
            assert r1.status_code == 201
            win_data = r1.get_json()["data"]
            win_id = win_data["id"]

            # 2. Racing request on h2: mock tracker to select h2
            tracker.select_host_for_start = lambda: hosts[1]  # force h2
            r2 = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id})
            assert r2.status_code == 200
            lose_data = r2.get_json()["data"]

            # Returns existing winner instance
            assert lose_data["id"] == win_id
            # Orphan container on losing host h2 must have been stopped
            assert len(client_h2.stopped_instances) == 1
        finally:
            set_host_tracker(None)
            destroy_ctfd(app)


def test_route_probe_c_player_queries_down_host_reported_as_error():
    """Probe C (Real Route): Player querying instance on a host marked DOWN gets state='error'."""
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c_id, alice, _ = _setup_route_world(app)

        client_h1 = MockAgentClient("h1", slots_total=50, slots_used=5)
        hosts = [{"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 50}]
        tracker = HostCapacityTracker(hosts=hosts)
        tracker.set_client_factory(lambda h, tok: client_h1)
        set_host_tracker(tracker)

        try:
            r = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id})
            assert r.status_code == 201

            # While host is healthy, state is running
            r_get = alice.get(f"/api/v1/l3mon/instances/{c_id}")
            assert r_get.status_code == 200
            assert r_get.get_json()["data"]["state"] == "running"

            # Host enters down penalty
            tracker.mark_host_down("h1")

            # Route reports state="error"
            r_down = alice.get(f"/api/v1/l3mon/instances/{c_id}")
            assert r_down.status_code == 200
            assert r_down.get_json()["data"]["state"] == "error"

            # Route list also reports state="error"
            r_list = alice.get("/api/v1/l3mon/instances")
            assert r_list.status_code == 200
            assert r_list.get_json()["data"][0]["state"] == "error"
        finally:
            set_host_tracker(None)
            destroy_ctfd(app)


def test_route_probe_d_admin_kill_all_dispatches_across_all_hosts():
    """Probe D (Real Route): Admin kill-all dispatches to all configured hosts and aggregates stopped counts."""
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c_id, _, admin = _setup_route_world(app)

        client_h1 = MockAgentClient("h1")
        client_h2 = MockAgentClient("h2")
        clients = {"h1": client_h1, "h2": client_h2}
        hosts = [
            {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz"},
            {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090", "host": "h2.xyz"},
        ]

        tracker = HostCapacityTracker(hosts=hosts)
        tracker.set_client_factory(lambda h, tok: clients[h["name"]])
        set_host_tracker(tracker)

        try:
            r = admin.post("/api/v1/l3mon/admin/instances/kill", json={})
            assert r.status_code == 200
            res = r.get_json()
            # 3 from h1 + 3 from h2 = 6 aggregated
            assert res["data"]["stopped"] == 6
            assert client_h1.killed_count == 3
            assert client_h2.killed_count == 3
        finally:
            set_host_tracker(None)
            destroy_ctfd(app)


def test_route_probe_e_racing_starts_same_host_does_not_stop_winner():
    """Probe E (BLOCKING 2 & Real Route): Racing starts on SAME host does NOT stop the winner's instance."""
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c_id, alice, _ = _setup_route_world(app)

        client_h1 = MockAgentClient("h1", slots_total=50, slots_used=5)
        hosts = [{"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz"}]
        tracker = HostCapacityTracker(hosts=hosts)
        tracker.set_client_factory(lambda h, tok: client_h1)
        set_host_tracker(tracker)

        try:
            # 1. Start winner instance on h1
            r1 = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id})
            assert r1.status_code == 201
            win_data = r1.get_json()["data"]
            win_id = win_data["id"]

            # 2. On single host, manager returns the existing active instance to a second start request
            client_h1.start_instance = lambda **kwargs: {
                "id": win_id,
                "team_id": 1,
                "challenge_id": c_id,
                "slug": "chal-hosts",
                "state": "running",
                "protocol": "http",
                "url": f"http://{win_id}.inst.l3m0nctf.xyz/",
                "created_at": time.time(),
                "expires_at": time.time() + 2700,
            }

            # Second racing request hits DB insert conflict
            r2 = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id})
            assert r2.status_code == 200
            data2 = r2.get_json()["data"]

            # Must return the active instance
            assert data2["id"] == win_id

            # MUST NOT call stop_instance on the winner's instance!
            assert len(client_h1.stopped_instances) == 0, f"Winner's instance was stopped: {client_h1.stopped_instances}"

            # Instance is still active in DB
            active = L3monInstance.query.filter_by(instance_id=win_id).first()
            assert active is not None
            assert active.state == "running"
        finally:
            set_host_tracker(None)
            destroy_ctfd(app)
