import copy
import pytest
from services.instancer.safety import assert_safe, ContainerSafetyError

VALID_SPEC = {
    "Image": "registry.example/web@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "User": "10001:10001",
    "Env": ["FLAG=" + "L3m0n" + "{" + "0123456789abcdef0123456789abcdef" + "}"],
    "Labels": {
        "l3mon.instance": "0123456789abcdef0123456789abcdef",
        "l3mon.team_id": "7",
        "l3mon.challenge_id": "42",
        "l3mon.slug": "demo-web",
        "l3mon.expires_at": "1700002700",
        "l3mon.protocol": "http",
        "l3mon.port": "8080",
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
        "NanoCpus": 500_000_000,  # 0.5 CPU
        "Memory": 268_435_456,    # 256 MB
        "PidsLimit": 128,
        "PortBindings": {},
        "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
        "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}},
        "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 2048}],
    },
}


def test_valid_spec_passes():
    assert_safe(VALID_SPEC, dev_mode=False)


def test_hostile_privileged_refused():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["Privileged"] = True
    with pytest.raises(ContainerSafetyError, match=r"(?i)privileged"):
        assert_safe(spec, dev_mode=False)


def test_hostile_cap_add_refused():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["CapAdd"] = ["SYS_ADMIN"]
    with pytest.raises(ContainerSafetyError, match="capabilities"):
        assert_safe(spec, dev_mode=False)


def test_hostile_cap_drop_missing_all():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["CapDrop"] = ["NET_RAW"]
    with pytest.raises(ContainerSafetyError, match="ALL capabilities"):
        assert_safe(spec, dev_mode=False)


def test_hostile_network_mode_host():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["NetworkMode"] = "host"
    with pytest.raises(ContainerSafetyError, match="network mode"):
        assert_safe(spec, dev_mode=False)


def test_hostile_network_mode_container():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["NetworkMode"] = "container:other"
    with pytest.raises(ContainerSafetyError, match="network mode"):
        assert_safe(spec, dev_mode=False)


def test_hostile_pid_mode_host():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["PidMode"] = "host"
    with pytest.raises(ContainerSafetyError, match="pid mode"):
        assert_safe(spec, dev_mode=False)


def test_hostile_ipc_mode_host():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["IpcMode"] = "host"
    with pytest.raises(ContainerSafetyError, match="ipc mode"):
        assert_safe(spec, dev_mode=False)


def test_hostile_uts_mode_host():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["UTSMode"] = "host"
    with pytest.raises(ContainerSafetyError, match="uts mode"):
        assert_safe(spec, dev_mode=False)


def test_hostile_bind_mounts_refused():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["Binds"] = ["/etc:/etc:ro"]
    with pytest.raises(ContainerSafetyError, match="bind mount"):
        assert_safe(spec, dev_mode=False)


def test_hostile_volumes_from_refused():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["VolumesFrom"] = ["container123"]
    with pytest.raises(ContainerSafetyError, match="volumes_from"):
        assert_safe(spec, dev_mode=False)


def test_hostile_devices_refused():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["Devices"] = [{"PathOnHost": "/dev/sda"}]
    with pytest.raises(ContainerSafetyError, match="devices"):
        assert_safe(spec, dev_mode=False)


def test_hostile_docker_socket_refused():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["Binds"] = ["/var/run/docker.sock:/var/run/docker.sock"]
    with pytest.raises(ContainerSafetyError, match="docker socket|bind mount"):
        assert_safe(spec, dev_mode=False)


def test_hostile_security_opt_unconfined():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["SecurityOpt"] = ["no-new-privileges:true", "seccomp=unconfined"]
    with pytest.raises(ContainerSafetyError, match=r"(?i)security option"):
        assert_safe(spec, dev_mode=False)


def test_hostile_security_opt_missing_no_new_privileges():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["SecurityOpt"] = []
    with pytest.raises(ContainerSafetyError, match=r"(?i)no-new-privileges"):
        assert_safe(spec, dev_mode=False)


def test_hostile_unpinned_image_refused():
    spec = copy.deepcopy(VALID_SPEC)
    spec["Image"] = "registry.example/demo-web:latest"
    with pytest.raises(ContainerSafetyError, match=r"(?i)pinned by digest"):
        assert_safe(spec, dev_mode=False)


def test_unpinned_image_allowed_in_dev_mode():
    spec = copy.deepcopy(VALID_SPEC)
    spec["Image"] = "registry.example/demo-web:dev"
    assert_safe(spec, dev_mode=True)


def test_hostile_root_user_refused():
    for root_user in ["root", "0", "0:0", "0:1000", ""]:
        spec = copy.deepcopy(VALID_SPEC)
        spec["User"] = root_user
        with pytest.raises(ContainerSafetyError, match=r"(?i)user"):
            assert_safe(spec, dev_mode=False)


def test_hostile_missing_user_refused():
    spec = copy.deepcopy(VALID_SPEC)
    del spec["User"]
    with pytest.raises(ContainerSafetyError, match=r"(?i)user"):
        assert_safe(spec, dev_mode=False)


def test_hostile_missing_memory_limit():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["Memory"] = 0
    with pytest.raises(ContainerSafetyError, match=r"(?i)memory"):
        assert_safe(spec, dev_mode=False)


def test_hostile_excessive_memory_limit():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["Memory"] = 2 * 1024 * 1024 * 1024  # 2 GB > 1 GB cap
    with pytest.raises(ContainerSafetyError, match=r"(?i)memory"):
        assert_safe(spec, dev_mode=False)


def test_hostile_missing_cpu_limit():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["NanoCpus"] = 0
    with pytest.raises(ContainerSafetyError, match=r"(?i)cpu"):
        assert_safe(spec, dev_mode=False)


def test_hostile_excessive_cpu_limit():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["NanoCpus"] = 4_000_000_000  # 4 CPUs > 2 CPU cap
    with pytest.raises(ContainerSafetyError, match=r"(?i)cpu"):
        assert_safe(spec, dev_mode=False)


def test_hostile_missing_pids_limit():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["PidsLimit"] = 0
    with pytest.raises(ContainerSafetyError, match=r"(?i)pids"):
        assert_safe(spec, dev_mode=False)


def test_hostile_excessive_pids_limit():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["PidsLimit"] = 512  # > 256 cap
    with pytest.raises(ContainerSafetyError, match=r"(?i)pids"):
        assert_safe(spec, dev_mode=False)


def test_hostile_published_ports_refused():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["PortBindings"] = {"8080/tcp": [{"HostPort": "8080"}]}
    with pytest.raises(ContainerSafetyError, match=r"(?i)published ports"):
        assert_safe(spec, dev_mode=False)


def test_hostile_restart_policy_refused():
    spec = copy.deepcopy(VALID_SPEC)
    spec["HostConfig"]["RestartPolicy"] = {"Name": "always"}
    with pytest.raises(ContainerSafetyError, match=r"(?i)restart policy"):
        assert_safe(spec, dev_mode=False)
