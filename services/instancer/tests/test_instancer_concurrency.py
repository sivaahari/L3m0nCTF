import threading
import time
from unittest.mock import Mock, call

import pytest

from services.instancer.docker_client import DockerClient
from services.instancer.manager import (
    InstanceManager,
    InstanceRecord,
    InstanceState,
    QuotaError,
    InstancerError,
)
from services.instancer.manifest import load_manifest_from_string

MANIFEST_TOML = """
[[challenge]]
slug = "concurrency-chal-1"
image = "challenge-1@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
protocol = "http"
port = 8080
cpus = 0.5
mem_mb = 128
pids = 64
ttl_minutes = 30

[[challenge]]
slug = "concurrency-chal-2"
image = "challenge-2@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
protocol = "http"
port = 8080
cpus = 0.5
mem_mb = 128
pids = 64
ttl_minutes = 30
"""


TEST_FLAG = "L3m0n" + "{" + "0123456789abcdef0123456789abcdef" + "}"
TEST_FLAG_2 = "L3m0n" + "{" + "fedcba9876543210fedcba9876543210" + "}"


def test_docker_client_default_timeout_is_15s():
    client = DockerClient()
    assert client.timeout == 15


def test_slow_docker_start_does_not_block_other_teams():
    manifest = load_manifest_from_string(MANIFEST_TOML, dev_mode=False)

    class SlowDockerClient:
        def __init__(self):
            self.lock_during_docker = []

        def create_network(self, name, internal=True, labels=None):
            return "net-id-123"

        def connect_network(self, net_id, container_id):
            pass

        def disconnect_network(self, net_id, container_id):
            pass

        def remove_network(self, net_id):
            pass

        def create_container(self, spec, name=None):
            # Sleep 0.4s to simulate slow Docker daemon
            time.sleep(0.4)
            return "cid-" + name

        def start_container(self, cid):
            pass

        def list_containers(self, label_filter=None):
            return [{"id": "cid", "ip": "10.0.0.2"}]

        def list_networks(self, label_filter=None):
            return []

    docker = SlowDockerClient()
    manager = InstanceManager(manifest=manifest, docker_client=docker)

    t1_started = threading.Event()
    t2_finished = threading.Event()
    t1_done = threading.Event()

    def run_team1():
        t1_started.set()
        manager.start_instance(team_id=1, challenge_id=101, slug="concurrency-chal-1", flag=TEST_FLAG)
        t1_done.set()

    def run_team2():
        # Wait until team 1 has entered start_instance
        t1_started.wait()
        time.sleep(0.05)  # team 1 is now in slow Docker call
        t0 = time.time()
        # Team 2 must be able to start without waiting 0.4s for Team 1 to finish
        manager.start_instance(team_id=2, challenge_id=102, slug="concurrency-chal-2", flag=TEST_FLAG_2)
        elapsed = time.time() - t0
        # If the lock was held across Docker calls, elapsed would be >= 0.35s + 0.4s = 0.75s
        # With reservation outside the lock, Team 2 runs concurrently and finishes around ~0.4s
        assert elapsed < 0.65
        t2_finished.set()

    th1 = threading.Thread(target=run_team1)
    th2 = threading.Thread(target=run_team2)

    th1.start()
    th2.start()

    th2.join(timeout=3)
    th1.join(timeout=3)

    assert t2_finished.is_set()
    assert t1_done.is_set()


def test_reservation_starting_state_enforces_quota():
    manifest = load_manifest_from_string(MANIFEST_TOML, dev_mode=False)

    barrier = threading.Barrier(3)

    class BlockingDockerClient:
        def create_network(self, name, internal=True, labels=None):
            return "net-id"

        def connect_network(self, net_id, container_id):
            pass

        def disconnect_network(self, net_id, container_id):
            pass

        def remove_network(self, net_id):
            pass

        def create_container(self, spec, name=None):
            barrier.wait(timeout=2)
            return "cid"

        def start_container(self, cid):
            pass

        def list_containers(self, label_filter=None):
            return [{"id": "cid", "ip": "10.0.0.2"}]

        def list_networks(self, label_filter=None):
            return []

    docker = BlockingDockerClient()
    manager = InstanceManager(manifest=manifest, docker_client=docker)

    # Launch 2 starts for Team 1 (quota is 3)
    th1 = threading.Thread(target=lambda: manager.start_instance(team_id=1, challenge_id=101, slug="concurrency-chal-1", flag=TEST_FLAG))
    th2 = threading.Thread(target=lambda: manager.start_instance(team_id=1, challenge_id=102, slug="concurrency-chal-2", flag=TEST_FLAG))
    th1.start()
    th2.start()

    time.sleep(0.05)  # wait for both to reserve and enter Docker

    # Third start for Team 1
    # Manually populate third active instance to hit quota of 3
    fake_record = InstanceRecord("fake", 1, 103, "slug", "http", 8080, 0, 9999, state=InstanceState.STARTING)
    with manager._lock:
        manager._instances["fake"] = fake_record
        manager._team_index.setdefault(1, set()).add("fake")

    # 4th start for Team 1 must immediately fail with QuotaError
    with pytest.raises(QuotaError):
        manager.start_instance(team_id=1, challenge_id=104, slug="concurrency-chal-1", flag=TEST_FLAG)

    # Release barrier
    try:
        barrier.wait(timeout=0.1)
    except Exception:
        pass
    th1.join(timeout=2)
    th2.join(timeout=2)


def test_failure_during_docker_calls_cleans_up_and_releases_reservation():
    manifest = load_manifest_from_string(MANIFEST_TOML, dev_mode=False)

    class FailingDockerClient:
        def __init__(self):
            self.cleaned_network = False
            self.cleaned_container = False

        def create_network(self, name, internal=True, labels=None):
            return "net-fail"

        def connect_network(self, net_id, container_id):
            pass

        def disconnect_network(self, net_id, container_id):
            pass

        def remove_network(self, net_id):
            self.cleaned_network = True

        def create_container(self, spec, name=None):
            raise RuntimeError("Docker daemon exploded")

        def start_container(self, cid):
            pass

        def remove_container(self, name):
            self.cleaned_container = True

        def list_containers(self, label_filter=None):
            return []

        def list_networks(self, label_filter=None):
            return []

    docker = FailingDockerClient()
    manager = InstanceManager(manifest=manifest, docker_client=docker)

    with pytest.raises(InstancerError) as exc:
        manager.start_instance(team_id=1, challenge_id=101, slug="concurrency-chal-1", flag=TEST_FLAG)

    assert "Docker daemon exploded" in str(exc.value)
    assert docker.cleaned_network is True

    # Quota and challenge index must be completely clean
    with manager._lock:
        assert len(manager._instances) == 0
        assert len(manager._team_index.get(1, set())) == 0
        assert (1, 101) not in manager._challenge_index


def test_reconcile_cleans_orphan_networks():
    manifest = load_manifest_from_string(MANIFEST_TOML, dev_mode=False)

    class ReconcileDockerMock:
        def __init__(self):
            self.disconnected = []
            self.removed_networks = []

        def list_containers(self, label_filter=None):
            # Only instance 'active123' has a running container
            return [{
                "id": "cid-active",
                "labels": {
                    "l3mon.instance": "active123",
                    "l3mon.team_id": "1",
                    "l3mon.challenge_id": "101",
                    "l3mon.slug": "concurrency-chal-1",
                    "l3mon.created_at": "1000",
                    "l3mon.expires_at": "2000",
                    "l3mon.protocol": "http",
                    "l3mon.port": "8080",
                    "l3mon.renewals": "0",
                },
                "state": "running",
                "ip": "10.0.1.5",
            }]

        def list_networks(self, label_filter=None):
            # Return active network and an orphan network left behind by a crash
            return [
                {
                    "Id": "net-active",
                    "Name": "l3mon_inst_active123",
                    "Labels": {"l3mon.instance": "active123"},
                },
                {
                    "Id": "net-orphan-999",
                    "Name": "l3mon_inst_orphan999",
                    "Labels": {"l3mon.instance": "orphan999"},
                },
            ]

        def disconnect_network(self, net_id, container_name):
            self.disconnected.append((net_id, container_name))

        def remove_network(self, net_id):
            self.removed_networks.append(net_id)

    docker = ReconcileDockerMock()
    manager = InstanceManager(manifest=manifest, docker_client=docker)
    manager.reconcile_from_docker()

    # Active instance was restored
    assert "active123" in manager._instances
    # Active network was NOT removed
    assert "net-active" not in docker.removed_networks
    # Orphan network was disconnected from router and removed
    assert "net-orphan-999" in docker.removed_networks
    assert any("net-orphan-999" in item[0] for item in docker.disconnected)
