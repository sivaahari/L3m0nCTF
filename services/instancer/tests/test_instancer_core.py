import time
import pytest
from services.instancer.manager import InstanceManager, InstanceState, QuotaError, CooldownError, CapacityError
from services.instancer.manifest import load_manifest_from_string

MANIFEST_TOML = """
[[challenge]]
slug = "demo-web"
image = "registry.example/demo-web@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
protocol = "http"
port = 8080
mem_mb = 256
cpus = 0.5
pids = 128
ttl_minutes = 45

[[challenge]]
slug = "demo-web-2"
image = "registry.example/demo-web-2@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
protocol = "http"
port = 8080
mem_mb = 256

[[challenge]]
slug = "demo-web-3"
image = "registry.example/demo-web-3@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
protocol = "http"
port = 8080
mem_mb = 256

[[challenge]]
slug = "demo-web-4"
image = "registry.example/demo-web-4@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
protocol = "http"
port = 8080
mem_mb = 256
"""

FLAG_EXAMPLE = "L3m0n" + "{" + "0123456789abcdef0123456789abcdef" + "}"


class MockDockerClient:
    def __init__(self):
        self.containers = {}
        self.networks = {}
        self.attached_networks = {}

    def create_network(self, name, internal=True, labels=None):
        self.networks[name] = {"internal": internal, "labels": labels or {}}
        self.attached_networks[name] = []
        return name

    def remove_network(self, name):
        self.networks.pop(name, None)
        self.attached_networks.pop(name, None)

    def connect_network(self, net_id, container_id):
        if net_id in self.attached_networks:
            self.attached_networks[net_id].append(container_id)

    def disconnect_network(self, net_id, container_id):
        if net_id in self.attached_networks and container_id in self.attached_networks[net_id]:
            self.attached_networks[net_id].remove(container_id)

    def create_container(self, spec, name=None):
        inst_id = spec["Labels"]["l3mon.instance"]
        self.containers[inst_id] = {
            "spec": spec,
            "state": "created",
            "labels": spec["Labels"],
            "ip": f"10.0.{len(self.containers) + 1}.2",
        }
        return inst_id

    def start_container(self, container_id):
        if container_id in self.containers:
            self.containers[container_id]["state"] = "running"

    def stop_container(self, container_id):
        if container_id in self.containers:
            self.containers[container_id]["state"] = "stopped"

    def remove_container(self, container_id):
        self.containers.pop(container_id, None)

    def list_containers(self, label_filter=None):
        res = []
        for cid, data in self.containers.items():
            if label_filter:
                match = True
                for k, v in label_filter.items():
                    if k not in data["labels"]:
                        match = False
                        break
                    if v is not None and data["labels"].get(k) != v:
                        match = False
                        break
                if not match:
                    continue
            res.append({"id": cid, "labels": data["labels"], "state": data["state"], "ip": data["ip"]})
        return res


def test_instance_start_and_idempotence():
    manifest = load_manifest_from_string(MANIFEST_TOML, dev_mode=False)
    docker = MockDockerClient()
    now_ts = 1700000000
    mgr = InstanceManager(manifest=manifest, docker_client=docker, instance_domain="inst.example.com", time_fn=lambda: now_ts)

    # First start
    inst1, created = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    assert created is True
    assert inst1["team_id"] == 7
    assert inst1["challenge_id"] == 42
    assert inst1["slug"] == "demo-web"
    assert inst1["state"] == "running"
    assert inst1["expires_at"] == now_ts + 45 * 60
    assert inst1["url"] == f"http://{inst1['id']}.inst.example.com/"

    # Idempotent start: same team and challenge returns existing instance
    inst2, created = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    assert created is False
    assert inst2["id"] == inst1["id"]
    assert len(docker.containers) == 1


def test_team_quota_limit():
    manifest = load_manifest_from_string(MANIFEST_TOML, dev_mode=False)
    docker = MockDockerClient()
    now_ts = 1700000000
    mgr = InstanceManager(manifest=manifest, docker_client=docker, instance_domain="inst.example.com", time_fn=lambda: now_ts)

    # Start 3 challenges for Team 7 (max 3 allowed)
    mgr.start_instance(team_id=7, challenge_id=1, slug="demo-web", flag=FLAG_EXAMPLE)
    mgr.start_instance(team_id=7, challenge_id=2, slug="demo-web-2", flag=FLAG_EXAMPLE)
    mgr.start_instance(team_id=7, challenge_id=3, slug="demo-web-3", flag=FLAG_EXAMPLE)

    # 4th challenge for Team 7 must raise QuotaError
    with pytest.raises(QuotaError, match=r"(?i)quota"):
        mgr.start_instance(team_id=7, challenge_id=4, slug="demo-web-4", flag=FLAG_EXAMPLE)


def test_cooldown_enforcement():
    manifest = load_manifest_from_string(MANIFEST_TOML, dev_mode=False)
    docker = MockDockerClient()
    current_time = 1700000000

    def get_time():
        return current_time

    mgr = InstanceManager(manifest=manifest, docker_client=docker, instance_domain="inst.example.com", time_fn=get_time)

    inst, _ = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    mgr.stop_instance(inst["id"])

    # Attempting to start the same challenge immediately raises CooldownError
    with pytest.raises(CooldownError) as exc_info:
        mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    assert exc_info.value.retry_after > 0

    # Advance time by 61 seconds (past 60s cooldown)
    current_time += 61
    inst_new, created = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    assert created is True
    assert inst_new["id"] != inst["id"]


def test_renewal_constraints():
    manifest = load_manifest_from_string(MANIFEST_TOML, dev_mode=False)
    docker = MockDockerClient()
    current_time = 1700000000

    def get_time():
        return current_time

    mgr = InstanceManager(manifest=manifest, docker_client=docker, instance_domain="inst.example.com", time_fn=get_time)

    inst, _ = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    orig_expiry = inst["expires_at"]

    # First renewal succeeds immediately
    renewed = mgr.renew_instance(inst["id"])
    assert renewed["expires_at"] == current_time + 45 * 60
    assert renewed["renewals"] == 1

    # Refuse second renewal if called less than 60s since previous renewal
    with pytest.raises(ValueError, match=r"(?i)60 seconds"):
        mgr.renew_instance(inst["id"])

    # Advance time by 65 seconds
    current_time += 65
    renewed2 = mgr.renew_instance(inst["id"])
    assert renewed2["expires_at"] == current_time + 45 * 60
    assert renewed2["renewals"] == 2

    # Refuse renewal past absolute 3-hour cap
    current_time += 3 * 3600
    with pytest.raises(ValueError, match=r"(?i)absolute"):
        mgr.renew_instance(inst["id"])


def test_reap_expired():
    manifest = load_manifest_from_string(MANIFEST_TOML, dev_mode=False)
    docker = MockDockerClient()
    current_time = 1700000000

    def get_time():
        return current_time

    mgr = InstanceManager(manifest=manifest, docker_client=docker, instance_domain="inst.example.com", time_fn=get_time)

    inst, _ = mgr.start_instance(team_id=7, challenge_id=42, slug="demo-web", flag=FLAG_EXAMPLE)
    assert inst["id"] in docker.containers

    # Before expiry: reap does nothing
    mgr.reap_expired()
    assert inst["id"] in docker.containers

    # Advance past expiry
    current_time += 46 * 60
    reaped = mgr.reap_expired()
    assert len(reaped) == 1
    assert reaped[0] == inst["id"]
    assert inst["id"] not in docker.containers


def test_reconcile_on_start_from_labels():
    manifest = load_manifest_from_string(MANIFEST_TOML, dev_mode=False)
    docker = MockDockerClient()
    now_ts = 1700000000

    # Simulate existing containers already in Docker with labels
    docker.containers["testinst1234567890abcdef123456"] = {
        "labels": {
            "l3mon.instance": "testinst1234567890abcdef123456",
            "l3mon.team_id": "7",
            "l3mon.challenge_id": "42",
            "l3mon.slug": "demo-web",
            "l3mon.created_at": str(now_ts - 600),
            "l3mon.expires_at": str(now_ts + 2100),
            "l3mon.protocol": "http",
            "l3mon.port": "8080",
            "l3mon.renewals": "0",
        },
        "state": "running",
        "ip": "10.0.1.2",
    }

    mgr = InstanceManager(manifest=manifest, docker_client=docker, instance_domain="inst.example.com", time_fn=lambda: now_ts)
    mgr.reconcile_from_docker()

    # Verify instance was discovered from labels
    found = mgr.get_instance("testinst1234567890abcdef123456")
    assert found is not None
    assert found["team_id"] == 7
    assert found["challenge_id"] == 42
    assert found["slug"] == "demo-web"
    assert found["state"] == "running"

    # Routes include it
    routes = mgr.get_routes()
    assert len(routes) == 1
    assert routes[0]["id"] == "testinst1234567890abcdef123456"
    assert routes[0]["target_ip"] == "10.0.1.2"
    assert routes[0]["target_port"] == 8080
