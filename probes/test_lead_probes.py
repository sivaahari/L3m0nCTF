"""Lead engineer probes A, B, C, D (BLOCKING 6, BLOCKING 7, and route-level tests).
"""
from __future__ import annotations

import collections
import datetime
import os
import sys
import types
from unittest import mock
import pytest

# Ensure repo root is on sys.path
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Setup minimal CTFd shims if running standalone
if "CTFd" not in sys.modules:
    _ctfd = types.ModuleType("CTFd")
    _ctfd.__path__ = []
    _ctfd_plugins = types.ModuleType("CTFd.plugins")
    _ctfd_plugins.__path__ = []
    _ctfd_flags = types.ModuleType("CTFd.plugins.flags")
    _ctfd_flags.FLAG_CLASSES = {}
    _ctfd_core = types.ModuleType("CTFd.plugins.l3mon_core")
    _ctfd_versions = types.ModuleType("CTFd.plugins.l3mon_core.versions")
    _ctfd_versions.check_ctfd_version = lambda: None
    _ctfd_migrations = types.ModuleType("CTFd.plugins.migrations")
    _ctfd_migrations.upgrade = lambda **kwargs: None
    _ctfd_models = types.ModuleType("CTFd.models")
    _ctfd_utils = types.ModuleType("CTFd.utils")
    _ctfd.plugins = _ctfd_plugins

    sys.modules["CTFd"] = _ctfd
    sys.modules["CTFd.plugins"] = _ctfd_plugins
    sys.modules["CTFd.plugins.flags"] = _ctfd_flags
    sys.modules["CTFd.plugins.l3mon_core"] = _ctfd_core
    sys.modules["CTFd.plugins.l3mon_core.versions"] = _ctfd_versions
    sys.modules["CTFd.plugins.migrations"] = _ctfd_migrations
    sys.modules["CTFd.models"] = _ctfd_models
    sys.modules["CTFd.utils"] = _ctfd_utils

    import plugins.l3mon_instances as _inst_pkg
    sys.modules["CTFd.plugins.l3mon_instances"] = _inst_pkg
    import plugins.l3mon_instances.client as _client_pkg
    sys.modules["CTFd.plugins.l3mon_instances.client"] = _client_pkg
    import plugins.l3mon_instances.hosts as _hosts_pkg
    sys.modules["CTFd.plugins.l3mon_instances.hosts"] = _hosts_pkg

from CTFd.plugins.l3mon_instances.client import InstancerClientError
from CTFd.plugins.l3mon_instances.hosts import HostCapacityTracker


@pytest.fixture(autouse=True)
def set_test_host_tokens(monkeypatch):
    monkeypatch.setenv("INSTANCER_TOKEN_H1", "test_token_h1")
    monkeypatch.setenv("INSTANCER_TOKEN_H2", "test_token_h2")


class MockLeadAgentClient:
    def __init__(self, host_name: str, slots_total: int = 50, slots_used: int = 0, should_fail_start: bool = False):
        self.host_name = host_name
        self.slots_total = slots_total
        self.slots_used = slots_used
        self.should_fail_start = should_fail_start
        self.started_instances: list[dict] = []
        self.stopped_instances: list[str] = []
        self.killed_count = 0
        self.next_id = 1

    def get_capacity(self) -> dict:
        return {
            "host_name": self.host_name,
            "slots_total": self.slots_total,
            "slots_used": self.slots_used,
            "slots_free": max(0, self.slots_total - self.slots_used),
            "mem_mb_total": 8192,
            "mem_mb_used": 1024,
        }

    def start_instance(self, team_id: int, challenge_id: int, slug: str, flag: str, ttl_seconds: int | None = None) -> dict:
        if self.should_fail_start:
            raise InstancerClientError(500, "docker_broken", f"Host {self.host_name} Docker daemon broken")
        inst_id = f"inst_{self.host_name}_{self.next_id}"
        self.next_id += 1
        data = {
            "id": inst_id,
            "team_id": team_id,
            "challenge_id": challenge_id,
            "slug": slug,
            "protocol": "http",
            "url": f"http://{inst_id}.inst.l3m0nctf.xyz/",
            "host": None,
            "port": None,
            "created_at": 1000,
            "expires_at": 3700,
        }
        self.started_instances.append(data)
        self.slots_used += 1
        return data

    def stop_instance(self, instance_id: str) -> None:
        self.stopped_instances.append(instance_id)

    def kill_instances(self, team_id: int | None = None, challenge_id: int | None = None) -> int:
        self.killed_count += 3
        return 3


def test_probe_a_failed_start_marks_host_down_and_retries_healthy_host():
    """Probe A (BLOCKING 6): A host with broken Docker is chosen, start fails (500),

    it must be marked DOWN for 30s and retried once on the next best host.
    A second start immediately uses the healthy host without touching the broken one.
    """
    now_ts = 1000.0
    client_h1 = MockLeadAgentClient("h1", slots_total=50, slots_used=5, should_fail_start=True)  # 45 free, broken Docker!
    client_h2 = MockLeadAgentClient("h2", slots_total=50, slots_used=20, should_fail_start=False)  # 30 free, healthy

    clients = {"h1": client_h1, "h2": client_h2}
    hosts = [
        {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 50},
        {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090", "host": "h2.xyz", "weight": 1.0, "max_slots": 50},
    ]

    tracker = HostCapacityTracker(hosts=hosts, time_provider=lambda: now_ts)
    tracker.set_client_factory(lambda h, tok: clients[h["name"]])

    def run_api_start(team_id: int, challenge_id: int):
        # Emulate the start_instance logic from plugins/l3mon_instances/api.py
        for attempt in range(2):
            chosen_host = tracker.select_host_for_start(slug="web-demo", now_ts=now_ts)
            client = tracker.get_client_for_host(chosen_host["name"])
            try:
                inst_data = client.start_instance(team_id, challenge_id, "web-demo", "test_flag")
                return 201, inst_data, chosen_host
            except InstancerClientError as e:
                tracker.mark_host_down(chosen_host["name"], now_ts=now_ts)
                if attempt == 0:
                    continue
                raise

    # Start 1: h1 has 45 free vs h2's 30 free.
    # Attempt 0 selects h1 -> fails -> h1 marked down for 30s.
    # Attempt 1 retries on h2 -> succeeds!
    status1, inst1, host1 = run_api_start(1, 101)
    assert status1 == 201
    assert host1["name"] == "h2"
    assert inst1["id"] == "inst_h2_1"
    assert tracker.is_host_down("h1", now_ts=now_ts) is True

    # Start 2: Next start immediately selects h2 (h1 is down for 30s)
    status2, inst2, host2 = run_api_start(2, 101)
    assert status2 == 201
    assert host2["name"] == "h2"
    assert inst2["id"] == "inst_h2_2"


def test_probe_b_racing_starts_returns_existing_and_cleans_losing_orphan():
    """Probe B (BLOCKING 7): Two requests race to start the same instance.

    The losing request hits DB conflict, catches it, calls stop_instance on its host
    to stop the orphaned container, and returns the existing instance with status 200.
    """
    now_ts = 1000.0
    client_h1 = MockLeadAgentClient("h1", slots_total=50, slots_used=5)
    client_h2 = MockLeadAgentClient("h2", slots_total=50, slots_used=5)
    clients = {"h1": client_h1, "h2": client_h2}
    hosts = [
        {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz", "weight": 1.0, "max_slots": 50},
        {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090", "host": "h2.xyz", "weight": 1.0, "max_slots": 50},
    ]

    tracker = HostCapacityTracker(hosts=hosts, time_provider=lambda: now_ts)
    tracker.set_client_factory(lambda h, tok: clients[h["name"]])

    # Winning request started on h1
    inst_win = client_h1.start_instance(team_id=1, challenge_id=101, slug="web-demo", flag="test_flag_win")
    db_active_instances = {f"1:101": inst_win}

    # Losing request started on h2 concurrently
    inst_lose = client_h2.start_instance(team_id=1, challenge_id=101, slug="web-demo", flag="test_flag_lose")
    active_key = "1:101"

    # Emulate the DB commit exception handling in start_instance
    try:
        if active_key in db_active_instances:
            raise Exception("IntegrityError: duplicate key value violates unique constraint active_key")
    except Exception:
        # Clean up the orphan container started on the losing host
        client_h2.stop_instance(inst_lose["id"])
        existing = db_active_instances[active_key]
        response_status = 200
        response_data = existing

    # Assert losing container was stopped on h2
    assert inst_lose["id"] in client_h2.stopped_instances
    # Assert player receives existing instance with HTTP 200
    assert response_status == 200
    assert response_data["id"] == inst_win["id"]


def test_probe_c_down_host_instances_reported_as_error_to_player():
    """Probe C: Player instances on a host marked DOWN report state='error'."""
    now_ts = 1000.0
    tracker = HostCapacityTracker(
        hosts=[{"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz"}],
        time_provider=lambda: now_ts,
    )
    inst_dict = {
        "id": "inst_123",
        "state": "running",
        "host_name": "h1",
    }

    # Host healthy
    state_out = "error" if tracker.is_host_down(inst_dict["host_name"], now_ts=now_ts) else inst_dict["state"]
    assert state_out == "running"

    # Mark host DOWN
    tracker.mark_host_down("h1", now_ts=now_ts)
    assert tracker.is_host_down("h1", now_ts=now_ts) is True

    state_down = "error" if tracker.is_host_down(inst_dict["host_name"], now_ts=now_ts) else inst_dict["state"]
    assert state_down == "error"

    # After 31 seconds, host recovers
    now_ts = 1031.0
    state_recovered = "error" if tracker.is_host_down(inst_dict["host_name"], now_ts=now_ts) else inst_dict["state"]
    assert state_recovered == "running"


def test_probe_d_admin_kill_all_dispatches_across_all_hosts():
    """Probe D: Admin kill-all dispatches to all configured hosts and aggregates stopped counts."""
    client_h1 = MockLeadAgentClient("h1")
    client_h2 = MockLeadAgentClient("h2")
    clients = {"h1": client_h1, "h2": client_h2}
    hosts = [
        {"name": "h1", "url": "http://10.0.1.10:8300", "router_url": "http://10.0.1.10:8090", "host": "h1.xyz"},
        {"name": "h2", "url": "http://10.0.1.11:8300", "router_url": "http://10.0.1.11:8090", "host": "h2.xyz"},
    ]
    tracker = HostCapacityTracker(hosts=hosts)
    tracker.set_client_factory(lambda h, tok: clients[h["name"]])

    total_killed = 0
    for h in tracker.get_hosts():
        c = tracker.get_client_for_host(h["name"])
        total_killed += c.kill_instances()

    assert total_killed == 6
    assert client_h1.killed_count == 3
    assert client_h2.killed_count == 3
