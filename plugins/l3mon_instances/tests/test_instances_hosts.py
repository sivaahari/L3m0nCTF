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
    monkeypatch.setenv("FLAG_HMAC_SECRET", "test_flag_hmac_secret_for_tests")
    for h in ["H1", "H2", "H3", "H4"]:
        monkeypatch.setenv(f"INSTANCER_TOKEN_{h}", f"test_token_{h.lower()}")
    if ctfd_available:
        try:
            import CTFd.plugins.l3mon_instances.api as api_mod
            monkeypatch.setattr(api_mod, "get_flag_secret", lambda: "test_flag_hmac_secret_for_tests")
        except Exception:
            pass


class MockAgentClient:
    def __init__(self, host_name: str, slots_total: int = 50, slots_used: int = 0, should_fail: bool = False, fail_start: bool = False):
        self.host_name = host_name
        self.fail_start = fail_start  # capacity answers, every START fails (a host whose Docker is broken)
        self.slots_total = slots_total
        self.slots_used = slots_used
        self.should_fail = should_fail
        self.capacity_probe_count = 0
        self.start_attempts = 0
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
        self.start_attempts += 1
        if self.should_fail or self.fail_start:
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

    with pytest.raises(ValueError, match="Host name 'default' is reserved"):
        validate_host_entry({"name": "default", "url": "http://10.0.0.1:8300", "router_url": "http://10.0.0.1:8090", "host": "h1.local"})

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

def _setup_route_world(app, num_challenges: int = 1):
    """Set up contest, live challenge(s), and player team in CTFd database."""
    set_config("start", 1000)
    set_config("end", 5000000000)
    admin = login_as_user(app, "admin")
    c_ids = []
    programmes = []
    for i in range(num_challenges):
        chal = gen_challenge(db, name=f"Live Chal Hosts {i+1}", state="visible")
        c_ids.append(chal.id)
        programmes.append({
            "challenge_id": chal.id,
            "channel": "web",
            "cell": i,
            "number": 101 + i,
            "slug": f"chal-hosts-{i+1}",
            "delivery": "live_single",
        })
    db.session.commit()

    admin.put("/api/v1/l3mon/admin/programmes", json={
        "channels": [{"slug": "web", "name": "Web Channel", "position": 1}],
        "programmes": programmes,
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

    if num_challenges == 1:
        return c_ids[0], alice, admin
    return c_ids, alice, admin


def test_route_probe_a_failed_start_marks_host_down_and_retries():
    """Probe A (Real Route): Failed start on h1 marks h1 down for 30s and retries on h2.

    A second subsequent start (another challenge) must not touch the broken host.
    """
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        (c_id1, c_id2), alice, _ = _setup_route_world(app, num_challenges=2)

        client_h1 = MockAgentClient("h1", slots_total=50, slots_used=5, fail_start=True)  # most free slots, capacity answers, every start fails
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
            r = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id1})
            assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.get_data(as_text=True)}"
            data = r.get_json()["data"]
            assert "inst_h2_" in data["url"]
            assert tracker.is_host_down("h1") is True
            assert tracker.is_host_down("h2") is False

            row = L3monInstance.query.filter_by(challenge_id=c_id1).first()
            assert row is not None
            assert row.host_name == "h2"
            assert row.instance_id.startswith("inst_h2_")

            # Second start (another challenge) via route: MUST NOT touch the broken host h1!
            r2 = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id2})
            assert r2.status_code == 201, f"Expected 201 on second start, got {r2.status_code}: {r2.get_data(as_text=True)}"
            data2 = r2.get_json()["data"]
            assert "inst_h2_" in data2["url"]
            assert client_h1.start_attempts == 1, f"Broken host was touched again: {client_h1.start_attempts} attempts"
            assert len(client_h2.started_instances) == 2
        finally:
            set_host_tracker(None)
            destroy_ctfd(app)


def test_route_probe_b_racing_starts_across_hosts_returns_existing_and_stops_orphan():
    """Probe B (Real Route): Two requests race across 2 hosts. Loser stops orphaned instance and returns 200."""
    from sqlalchemy.exc import IntegrityError

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
            # Force tracker to select h2 for request 2
            tracker.select_host_for_start = lambda *args, **kwargs: hosts[1]

            real_commit = db.session.commit
            lost = {"done": False}

            def commit_race():
                if not lost["done"] and any(isinstance(obj, L3monInstance) for obj in db.session.new):
                    lost["done"] = True
                    # Winning instance from h1 committed just before us
                    win_row = L3monInstance(
                        instance_id="inst_h1_winner",
                        team_id=1,
                        challenge_id=c_id,
                        slug="chal-hosts",
                        protocol="http",
                        state="running",
                        host_name="h1",
                        url="http://inst_h1_winner.inst.l3m0nctf.xyz/",
                        created_at=datetime.datetime.utcnow(),
                        expires_at=datetime.datetime.utcnow() + datetime.timedelta(seconds=2700),
                        active_key=f"1:{c_id}",
                    )
                    db.session.rollback()
                    db.session.add(win_row)
                    real_commit()
                    # Now raise the collision on duplicate active_key
                    raise IntegrityError("insert", {}, Exception("Duplicate entry for key 'active_key'"))
                return real_commit()

            with mock.patch.object(db.session, "commit", side_effect=commit_race):
                r = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id})

            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.get_data(as_text=True)}"
            data = r.get_json()["data"]

            # Returns existing winning instance
            assert "inst_h1_winner" in data["url"]

            # Orphaned container created on losing host h2 must have been stopped
            assert len(client_h2.stopped_instances) == 1
            assert client_h2.stopped_instances[0].startswith("inst_h2_")
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

            # While host is healthy, GET /instances lists running state
            r_list = alice.get("/api/v1/l3mon/instances")
            assert r_list.status_code == 200
            assert r_list.get_json()["data"]["instances"][0]["state"] == "running"

            # And idempotent POST /instances returns running state
            r_post = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id})
            assert r_post.status_code == 200
            assert r_post.get_json()["data"]["state"] == "running"

            # Host enters down penalty
            tracker.mark_host_down("h1")

            # Route list reports state="error"
            r_list_down = alice.get("/api/v1/l3mon/instances")
            assert r_list_down.status_code == 200
            assert r_list_down.get_json()["data"]["instances"][0]["state"] == "error"

            # And idempotent POST /instances reports state="error"
            r_post_down = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id})
            assert r_post_down.status_code == 200
            assert r_post_down.get_json()["data"]["state"] == "error"
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
    from sqlalchemy.exc import IntegrityError

    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c_id, alice, _ = _setup_route_world(app)

        client_h1 = MockAgentClient("h1", slots_total=50, slots_used=5)
        # On single host, manager returns the existing active instance to a concurrent second start request
        client_h1.start_instance = lambda **kwargs: {
            "id": "inst_h1_winner",
            "team_id": 1,
            "challenge_id": c_id,
            "slug": "chal-hosts",
            "state": "running",
            "protocol": "http",
            "url": "http://inst_h1_winner.inst.l3m0nctf.xyz/",
            "created_at": 1000,
            "expires_at": 3700,
        }

        hosts = [{"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 50}]
        tracker = HostCapacityTracker(hosts=hosts)
        tracker.set_client_factory(lambda h, tok: client_h1)
        set_host_tracker(tracker)

        try:
            real_commit = db.session.commit
            lost = {"done": False}

            def commit_race():
                if not lost["done"] and any(isinstance(obj, L3monInstance) for obj in db.session.new):
                    lost["done"] = True
                    win_row = L3monInstance(
                        instance_id="inst_h1_winner",
                        team_id=1,
                        challenge_id=c_id,
                        slug="chal-hosts",
                        protocol="http",
                        state="running",
                        host_name="h1",
                        url="http://inst_h1_winner.inst.l3m0nctf.xyz/",
                        created_at=datetime.datetime.utcnow(),
                        expires_at=datetime.datetime.utcnow() + datetime.timedelta(seconds=2700),
                        active_key=f"1:{c_id}",
                    )
                    db.session.rollback()
                    db.session.add(win_row)
                    real_commit()
                    raise IntegrityError("insert", {}, Exception("Duplicate entry for key 'active_key'"))
                return real_commit()

            with mock.patch.object(db.session, "commit", side_effect=commit_race):
                r = alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id})

            assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.get_data(as_text=True)}"
            data = r.get_json()["data"]

            # Must return the active instance
            assert "inst_h1_winner" in data["url"]

            # MUST NOT call stop_instance on the winner's instance!
            assert len(client_h1.stopped_instances) == 0, f"Winner's instance was stopped: {client_h1.stopped_instances}"

            # Instance is still active in DB
            active = L3monInstance.query.filter_by(instance_id="inst_h1_winner").first()
            assert active is not None
            assert active.state == "running"
        finally:
            set_host_tracker(None)
            destroy_ctfd(app)


# ---------------------------------------------------------------------------
# 8. Lead engineer's tests (5 November 2026): the host-down mark is shared between workers, tokens from a folder, a missing token
# ---------------------------------------------------------------------------

class _SharedStore:
    """What CTFd's cache (Redis in production) is to the tracker: one place that every worker process reads and writes."""

    def __init__(self):
        self.d: dict[str, float] = {}

    def get(self, name):
        return self.d.get(name)

    def set(self, name, until, ttl):
        self.d[name] = until

    def clear(self, name):
        self.d.pop(name, None)


class _NoStore:
    """A tracker with no shared mark (the old behaviour): used to show that the shared mark is what the tests below depend on."""

    def get(self, name):
        return None

    def set(self, name, until, ttl):
        pass

    def clear(self, name):
        pass


_ONE_HOST = [{"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 50}]


def test_a_host_marked_down_by_one_worker_is_down_for_the_other_workers():
    store = _SharedStore()
    t0 = 1000.0
    worker1 = HostCapacityTracker(hosts=_ONE_HOST, down_store=store, time_provider=lambda: t0)
    worker2 = HostCapacityTracker(hosts=_ONE_HOST, down_store=store, time_provider=lambda: t0)
    assert worker2.is_host_down("h1") is False
    worker1.mark_host_down("h1")
    assert worker2.is_host_down("h1") is True, "worker 2 never failed a probe itself, but worker 1 did"
    assert worker2.is_host_down("h1", now_ts=t0 + 31) is False, "the mark lasts 30 seconds"
    worker1.mark_host_up("h1")
    worker3 = HostCapacityTracker(hosts=_ONE_HOST, down_store=store, time_provider=lambda: t0)
    assert worker3.is_host_down("h1") is False, "a host that answers again is up for everybody"


def test_check_host_finds_a_dead_host_without_any_start_and_tells_every_worker():
    store = _SharedStore()
    dead = MockAgentClient("h1", should_fail=True)
    worker1 = HostCapacityTracker(hosts=_ONE_HOST, down_store=store, cache_ttl=0.0, time_provider=lambda: 1000.0)
    worker1.set_client_factory(lambda h, tok: dead)
    worker2 = HostCapacityTracker(hosts=_ONE_HOST, down_store=store, time_provider=lambda: 1000.0)
    worker2.set_client_factory(lambda h, tok: MockAgentClient("h1"))  # worker 2 would find it healthy if it asked
    assert worker1.check_host("h1") is False
    assert dead.capacity_probe_count == 1
    assert worker2.check_host("h1") is False, "worker 2 reads the shared mark and does not even ask"


def test_a_healthy_host_passes_check_host_through_the_capacity_cache():
    client = MockAgentClient("h1")
    tracker = HostCapacityTracker(hosts=_ONE_HOST, down_store=_SharedStore(), time_provider=lambda: 1000.0)
    tracker.set_client_factory(lambda h, tok: client)
    assert all(tracker.check_host("h1") for _ in range(10))
    assert client.capacity_probe_count == 1, "ten list reads inside 5 seconds cost one probe"


def test_player_list_says_error_for_a_dead_host_whichever_worker_answers():
    """The list used to say 'running' about 40-70% of the time: the mark lived in one worker's memory. Two trackers play two workers."""
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c_id, alice, _ = _setup_route_world(app)
        store = _SharedStore()
        agent = MockAgentClient("h1", slots_total=50, slots_used=5)
        worker1 = HostCapacityTracker(hosts=_ONE_HOST, down_store=store, cache_ttl=0.0)  # probes at every list
        worker1.set_client_factory(lambda h, tok: agent)
        worker2 = HostCapacityTracker(hosts=_ONE_HOST, down_store=store, cache_ttl=3600.0)  # believes its warm, healthy cache
        worker2.set_client_factory(lambda h, tok: MockAgentClient("h1", slots_total=50, slots_used=5))
        try:
            set_host_tracker(worker2)
            worker2.get_capacity("h1")  # warm and healthy
            set_host_tracker(worker1)
            assert alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id}).status_code == 201
            assert alice.get("/api/v1/l3mon/instances").get_json()["data"]["instances"][0]["state"] == "running"

            agent.should_fail = True  # the host dies; nobody starts anything
            states = []
            for worker in (worker1, worker2, worker1, worker2, worker2, worker1):
                set_host_tracker(worker)
                states.append(alice.get("/api/v1/l3mon/instances").get_json()["data"]["instances"][0]["state"])
            assert states == ["error"] * 6, states
        finally:
            set_host_tracker(None)
            destroy_ctfd(app)


def test_without_the_shared_mark_the_second_worker_would_still_say_running():
    """Control for the test above: the same steps with workers that share nothing show the old behaviour, so the test above proves the mark."""
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c_id, alice, _ = _setup_route_world(app)
        agent = MockAgentClient("h1", slots_total=50, slots_used=5)
        worker1 = HostCapacityTracker(hosts=_ONE_HOST, down_store=_NoStore(), cache_ttl=0.0)
        worker1.set_client_factory(lambda h, tok: agent)
        worker2 = HostCapacityTracker(hosts=_ONE_HOST, down_store=_NoStore(), cache_ttl=3600.0)
        worker2.set_client_factory(lambda h, tok: MockAgentClient("h1", slots_total=50, slots_used=5))
        try:
            set_host_tracker(worker2)
            worker2.get_capacity("h1")
            set_host_tracker(worker1)
            assert alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id}).status_code == 201
            agent.should_fail = True
            set_host_tracker(worker1)
            assert alice.get("/api/v1/l3mon/instances").get_json()["data"]["instances"][0]["state"] == "error"
            set_host_tracker(worker2)
            assert alice.get("/api/v1/l3mon/instances").get_json()["data"]["instances"][0]["state"] == "running"
        finally:
            set_host_tracker(None)
            destroy_ctfd(app)


def test_tokens_come_from_the_host_secrets_folder(tmp_path, monkeypatch):
    from CTFd.plugins.l3mon_instances.hosts import host_token_problems

    monkeypatch.setenv("L3MON_HOST_SECRETS_DIR", str(tmp_path))
    for h in ("H1", "H2", "H3", "H4"):
        monkeypatch.delenv(f"INSTANCER_TOKEN_{h}", raising=False)
    (tmp_path / "INSTANCER_TOKEN_H2").write_text("folder-token-h2\n", encoding="utf-8")
    assert get_token_for_host("h2") == "folder-token-h2"
    with pytest.raises(ValueError, match="INSTANCER_TOKEN_H3"):
        get_token_for_host("h3")
    hosts = [{"name": "h2"}, {"name": "h3"}]
    problems = host_token_problems(hosts)
    assert len(problems) == 1 and "INSTANCER_TOKEN_H3" in problems[0]
    # any number of hosts: a new host is a new file, not an edit of a compose file
    (tmp_path / "INSTANCER_TOKEN_H3").write_text("folder-token-h3", encoding="utf-8")
    assert host_token_problems(hosts) == []


def test_a_host_that_lost_its_token_gives_a_clear_503_on_renew_and_stop_not_a_crash(monkeypatch):
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        c_id, alice, _ = _setup_route_world(app)
        agent = MockAgentClient("h1", slots_total=50, slots_used=5)
        tracker = HostCapacityTracker(hosts=_ONE_HOST, down_store=_SharedStore())
        tracker.set_client_factory(lambda h, tok: agent)
        set_host_tracker(tracker)
        try:
            assert alice.post("/api/v1/l3mon/instances", json={"challenge_id": c_id}).status_code == 201
            monkeypatch.delenv("INSTANCER_TOKEN_H1")  # the secret file of this host disappears
            r = alice.post(f"/api/v1/l3mon/instances/{c_id}/renew", json={})
            assert r.status_code == 503, r.get_data(as_text=True)
            assert r.get_json()["error"] == "busy"
            assert alice.delete(f"/api/v1/l3mon/instances/{c_id}", json={}).status_code == 200
        finally:
            set_host_tracker(None)
            destroy_ctfd(app)
