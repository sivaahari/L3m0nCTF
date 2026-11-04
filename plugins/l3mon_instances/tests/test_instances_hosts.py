"""Tests for multi-host scheduler, capacity cache, down penalty, and optimistic counters.

Verifies:
1. Host configuration loading and strict validation.
2. Per-host token isolation (INSTANCER_TOKEN_<NAME> vs INSTANCER_TOKEN).
3. 5-second TTL capacity cache.
4. Optimistic local counter under concurrent starts (preventing thundering herd).
5. 30-second down rule when a host fails to respond.
6. Failover to available hosts and 503 no_capacity when all hosts are exhausted/down.
7. Existing instances stick to their assigned host.
8. Instances on down hosts report as state="error" to players.
9. Admin kill-all aggregates across all configured hosts.
"""
from __future__ import annotations

import collections
import json
import os
import tempfile
import time
from unittest import mock
import pytest

import sys
import types
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if "CTFd" not in sys.modules:
    _ctfd_mod = types.ModuleType("CTFd")
    _ctfd_mod.__path__ = []
    _ctfd_plugins_mod = types.ModuleType("CTFd.plugins")
    _ctfd_plugins_mod.__path__ = []
    _ctfd_flags_mod = types.ModuleType("CTFd.plugins.flags")
    _ctfd_flags_mod.FLAG_CLASSES = {}
    _ctfd_core_mod = types.ModuleType("CTFd.plugins.l3mon_core")
    _ctfd_versions_mod = types.ModuleType("CTFd.plugins.l3mon_core.versions")
    _ctfd_versions_mod.check_ctfd_version = lambda: None
    _ctfd_migrations_mod = types.ModuleType("CTFd.plugins.migrations")
    _ctfd_migrations_mod.upgrade = lambda **kwargs: None
    _ctfd_models_mod = types.ModuleType("CTFd.models")
    _ctfd_utils_mod = types.ModuleType("CTFd.utils")
    _ctfd_mod.plugins = _ctfd_plugins_mod

    sys.modules["CTFd"] = _ctfd_mod
    sys.modules["CTFd.plugins"] = _ctfd_plugins_mod
    sys.modules["CTFd.plugins.flags"] = _ctfd_flags_mod
    sys.modules["CTFd.plugins.l3mon_core"] = _ctfd_core_mod
    sys.modules["CTFd.plugins.l3mon_core.versions"] = _ctfd_versions_mod
    sys.modules["CTFd.plugins.migrations"] = _ctfd_migrations_mod
    sys.modules["CTFd.models"] = _ctfd_models_mod
    sys.modules["CTFd.utils"] = _ctfd_utils_mod

    import plugins.l3mon_instances as _inst_pkg
    sys.modules["CTFd.plugins.l3mon_instances"] = _inst_pkg

    import plugins.l3mon_instances.client as _client_pkg
    sys.modules["CTFd.plugins.l3mon_instances.client"] = _client_pkg

    import plugins.l3mon_instances.hosts as _hosts_pkg
    sys.modules["CTFd.plugins.l3mon_instances.hosts"] = _hosts_pkg

from CTFd.plugins.l3mon_instances.client import InstancerClient, InstancerClientError
from CTFd.plugins.l3mon_instances.hosts import (
    HostCapacityTracker,
    get_token_for_host,
    load_hosts_config,
    validate_host_entry,
)


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

    def kill_instances(self, team_id=None, challenge_id=None) -> int:
        self.killed_count += 3
        return 3


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
    # Empty list
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump([], f)
        p_empty = f.name
    try:
        with pytest.raises(ValueError, match="must not be empty"):
            load_hosts_config(p_empty)
    finally:
        os.unlink(p_empty)

    # Invalid name
    with pytest.raises(ValueError, match="Invalid host 'name'"):
        validate_host_entry({"name": "bad name!", "url": "http://10.0.0.1:8300", "router_url": "http://10.0.0.1:8090", "host": "h1.local"})

    # Invalid URL scheme
    with pytest.raises(ValueError, match="invalid 'url'"):
        validate_host_entry({"name": "h1", "url": "ftp://10.0.0.1:8300", "router_url": "http://10.0.0.1:8090", "host": "h1.local"})

    # Missing public_tcp_host / host
    with pytest.raises(ValueError, match="missing required 'public_tcp_host'"):
        validate_host_entry({"name": "h1", "url": "http://10.0.0.1:8300", "router_url": "http://10.0.0.1:8090"})

    # Invalid public_tcp_host with scheme
    with pytest.raises(ValueError, match="invalid 'public_tcp_host'"):
        validate_host_entry({"name": "h1", "url": "http://10.0.0.1:8300", "router_url": "http://10.0.0.1:8090", "host": "http://h1.local"})

    # Non-positive weight
    with pytest.raises(ValueError, match="invalid 'weight'"):
        validate_host_entry({"name": "h1", "url": "http://10.0.0.1:8300", "router_url": "http://10.0.0.1:8090", "host": "h1.local", "weight": 0})

    # Non-existent ca_cert
    with pytest.raises(ValueError, match="ca_cert file not found"):
        validate_host_entry({"name": "h1", "url": "https://10.0.0.1:8300", "router_url": "https://10.0.0.1:8090", "host": "h1.local", "ca_cert": "/nonexistent/ca.pem"})


def test_token_resolution_per_host(monkeypatch):
    monkeypatch.delenv("INSTANCER_TOKEN_H1", raising=False)
    monkeypatch.delenv("INSTANCER_TOKEN_H2", raising=False)
    monkeypatch.setenv("INSTANCER_TOKEN", "global_shared_secret")
    monkeypatch.setenv("INSTANCER_TOKEN_H1", "secret_host_1_only")

    # Host h1 gets specific token
    token_h1 = get_token_for_host("h1")
    assert token_h1 == "secret_host_1_only"

    # Multi-host mode refuses host h2 without its own token (no fallback to global)
    with pytest.raises(ValueError, match="INSTANCER_TOKEN_H2"):
        get_token_for_host("h2", allow_fallback=False)

    # With explicit allow_fallback=True (single-host/dev mode), falls back to global
    assert get_token_for_host("h2", allow_fallback=True) == "global_shared_secret"


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

    # First fetch at t=1000.0 (cache miss)
    cap1 = tracker.get_capacity("h1")
    assert cap1["slots_free"] == 40
    assert mock_client.capacity_probe_count == 1

    # Second fetch at t=1003.0 (within 5s window -> cache hit)
    current_time = 1003.0
    cap2 = tracker.get_capacity("h1")
    assert cap2["slots_free"] == 40
    assert mock_client.capacity_probe_count == 1  # No additional upstream request!

    # Third fetch at t=1006.0 (after 5s window -> cache expired)
    current_time = 1006.0
    cap3 = tracker.get_capacity("h1")
    assert cap3["slots_free"] == 40
    assert mock_client.capacity_probe_count == 2  # Probed again


def test_optimistic_local_counter_concurrent_starts():
    current_time = 1000.0
    client_h1 = MockAgentClient("h1", slots_total=50, slots_used=10)  # 40 free
    client_h2 = MockAgentClient("h2", slots_total=50, slots_used=10)  # 40 free

    clients = {"h1": client_h1, "h2": client_h2}

    hosts = [
        {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 50, "ca_cert": None},
        {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090", "host": "h2.xyz", "weight": 1.0, "max_slots": 50, "ca_cert": None},
    ]

    tracker = HostCapacityTracker(hosts=hosts, time_provider=lambda: current_time)
    tracker.set_client_factory(lambda h, tok: clients[h["name"]])

    # Initial probe: both have 40 free. Host h2 has greater name or tie-breaker
    # First start: selects h2 (or h1)
    chosen1 = tracker.select_host_for_start()
    name1 = chosen1["name"]

    # Second start: due to optimistic counter on name1, the other host now has strictly more free capacity
    chosen2 = tracker.select_host_for_start()
    name2 = chosen2["name"]
    assert name1 != name2, "Optimistic counter must cause subsequent immediate start to choose alternate host"

    # Third start: load is balanced back, picks name1
    chosen3 = tracker.select_host_for_start()
    assert chosen3["name"] == name1


def test_30s_down_rule_and_recovery():
    current_time = 1000.0
    client_h1 = MockAgentClient("h1", slots_total=50, slots_used=5, should_fail=True)  # failing
    client_h2 = MockAgentClient("h2", slots_total=50, slots_used=10, should_fail=False) # healthy, 40 free

    clients = {"h1": client_h1, "h2": client_h2}
    hosts = [
        {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 50, "ca_cert": None},
        {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090", "host": "h2.xyz", "weight": 1.0, "max_slots": 50, "ca_cert": None},
    ]

    tracker = HostCapacityTracker(hosts=hosts, time_provider=lambda: current_time)
    tracker.set_client_factory(lambda h, tok: clients[h["name"]])

    # Select host: h1 fails, is marked DOWN, scheduler falls back to h2
    chosen = tracker.select_host_for_start()
    assert chosen["name"] == "h2"
    assert tracker.is_host_down("h1", now_ts=current_time) is True

    # At t=1015.0 (15s later): h1 is still down, scheduler directly selects h2 without even probing h1
    current_time = 1015.0
    probe_count_before = client_h1.capacity_probe_count
    chosen_15 = tracker.select_host_for_start()
    assert chosen_15["name"] == "h2"
    assert client_h1.capacity_probe_count == probe_count_before

    # At t=1031.0 (31s later): 30s down penalty expired. h1 recovers!
    current_time = 1031.0
    client_h1.should_fail = False  # host recovered
    client_h1.slots_used = 2      # has 48 free vs h2's 38
    chosen_rec = tracker.select_host_for_start()
    assert chosen_rec["name"] == "h1"
    assert tracker.is_host_down("h1", now_ts=current_time) is False


def test_all_hosts_full_or_down_raises_no_capacity():
    current_time = 1000.0
    client_h1 = MockAgentClient("h1", slots_total=50, slots_used=50) # full (0 free)
    client_h2 = MockAgentClient("h2", should_fail=True)              # down

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


def test_instance_stays_on_assigned_host():
    """An instance assigned to host h2 remains on h2 for renew/stop operations,
    even when host h1 has vastly more available capacity.
    """
    current_time = 1000.0
    client_h1 = MockAgentClient("h1", slots_total=100, slots_used=10)  # 90 free
    client_h2 = MockAgentClient("h2", slots_total=20, slots_used=20)   # 0 free
    clients = {"h1": client_h1, "h2": client_h2}
    hosts = [
        {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 100},
        {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090", "host": "h2.xyz", "weight": 1.0, "max_slots": 20},
    ]
    tracker = HostCapacityTracker(hosts=hosts, time_provider=lambda: current_time)
    tracker.set_client_factory(lambda h, tok: clients[h["name"]])

    # A new start selects h1 due to capacity
    best = tracker.select_host_for_start()
    assert best["name"] == "h1"

    # But an existing instance row on h2 dispatches renew and stop strictly to h2!
    instance_host_name = "h2"
    client = tracker.get_client_for_host(instance_host_name)
    client.stop_instance("inst-xyz-42")

    assert "inst-xyz-42" in client_h2.stopped_instances
    assert len(client_h1.stopped_instances) == 0


def test_down_host_instances_reported_as_error_to_player():
    """Probe C: Player instances on a host marked DOWN report state='error'."""
    from probes.test_lead_probes import test_probe_c_down_host_instances_reported_as_error_to_player as probe_c
    probe_c()


def test_admin_kill_all_dispatches_across_all_hosts():
    """Probe D: Admin kill-all dispatches to all configured hosts and aggregates stopped counts."""
    from probes.test_lead_probes import test_probe_d_admin_kill_all_dispatches_across_all_hosts as probe_d
    probe_d()


def test_multihost_failover_and_rebalance_at_scale():
    """Scale test: 4 hosts, 60 starts, sudden dual-host failure, failover, 30s recovery, rebalancing."""
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

    # Phase 1: 40 starts evenly distributed across 4 healthy hosts
    counts: dict[str, int] = collections.defaultdict(int)
    for _ in range(40):
        chosen = tracker.select_host_for_start()
        counts[chosen["name"]] += 1
        clients[chosen["name"]].slots_used += 1

    # Verify balanced distribution (approx 10 each)
    assert all(counts[h["name"]] == 10 for h in hosts)

    # Phase 2: Sudden outage of h2 and h4 (after 5s cache expires)
    current_time += 6.0
    client_h2.should_fail = True
    client_h4.should_fail = True
    counts_phase2: dict[str, int] = collections.defaultdict(int)

    # Next 20 starts should fail over exclusively to h1 and h3
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

    # Phase 3: Fast-forward 31 seconds. h2 and h4 recover
    current_time += 31.0
    client_h2.should_fail = False
    client_h4.should_fail = False

    # Next 20 starts: h1 and h3 have 20 used (10 free), while h2 and h4 have 10 used (20 free).
    # Scheduler should prioritize h2 and h4 to rebalance!
    counts_phase3: dict[str, int] = collections.defaultdict(int)
    for _ in range(20):
        chosen = tracker.select_host_for_start()
        counts_phase3[chosen["name"]] += 1
        clients[chosen["name"]].slots_used += 1

    assert counts_phase3["h2"] == 10
    assert counts_phase3["h4"] == 10
    assert counts_phase3["h1"] == 0
    assert counts_phase3["h3"] == 0

    # System is now fully rebalanced: all 4 hosts have exactly 20 used!
    assert client_h1.slots_used == 20
    assert client_h2.slots_used == 20
    assert client_h3.slots_used == 20
    assert client_h4.slots_used == 20


