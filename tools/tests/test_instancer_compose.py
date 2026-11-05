"""Tests for instancer compose configurations: dev overlay vs production overlay.

Verifies:
1. Production configuration has INSTANCER_DEV == "0".
2. Production configuration mounts external manifest (/etc/l3mon/challenges.toml), NOT challenges.example.toml.
3. Production configuration does not grant PRESET_ADMIN_TOKEN to any service.
4. Production router binds to 0.0.0.0 (or configured host), while dev defaults to 127.0.0.1.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def render_compose(env_name: str, overlays: list[str], extra_env: dict[str, str] | None = None) -> dict:
    """Render compose config via docker compose config and parse the JSON (no PyYAML: CI installs only pytest)."""
    files = [
        "-f", str(ROOT / "deploy" / "compose" / "compose.base.yml"),
        "-f", str(ROOT / "deploy" / "compose" / f"compose.{env_name}.yml"),
    ]
    for overlay in overlays:
        files.extend(["-f", str(ROOT / "deploy" / "compose" / f"compose.{overlay}.yml")])

    env = {**os.environ}
    if extra_env:
        env.update(extra_env)

    cmd = ["docker", "compose", *files, "config", "--format", "json"]
    res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 0, f"docker compose config failed: {res.stderr}\nOutput:\n{res.stdout}"
    return json.loads(res.stdout)


def test_dev_overlay_properties():
    data = render_compose("dev", ["instancer"])
    services = data.get("services", {})
    instancer = services.get("instancer", {})
    router = services.get("router", {})

    # In dev, INSTANCER_DEV is "0" by default unless set, but example manifest is mounted
    volumes = instancer.get("volumes", [])
    vol_sources = [v.get("source", "") for v in volumes]
    assert any("challenges.example.toml" in src for src in vol_sources), f"Expected example manifest in dev, got {vol_sources}"

    # Dev router published on loopback 127.0.0.1 for HTTP and all TCP ports
    ports = router.get("ports", [])
    assert len(ports) == 201, f"Expected 201 published ports (1 HTTP + 200 TCP), got {len(ports)}"
    assert all(p.get("host_ip") == "127.0.0.1" for p in ports)
    published_ports = {int(p.get("target")) for p in ports}
    assert 8090 in published_ports
    assert 30000 in published_ports
    assert 30199 in published_ports

    # Both instancer and router receive the same INSTANCER_INSTANCE_POOL
    inst_pool = instancer.get("environment", {}).get("INSTANCER_INSTANCE_POOL")
    router_pool = router.get("environment", {}).get("INSTANCER_INSTANCE_POOL")
    assert inst_pool is not None and inst_pool == router_pool


def test_production_overlay_properties():
    data = render_compose(
        "production",
        ["instancer", "instancer-production"],
        extra_env={
            "INSTANCE_DOMAIN": "instances.l3m0nctf.xyz",
            "INSTANCER_INSTANCE_POOL": "10.200.0.0/16",
        },
    )
    services = data.get("services", {})
    instancer = services.get("instancer", {})
    router = services.get("router", {})

    # 1. INSTANCER_DEV must be "0"
    env_vars = instancer.get("environment", {})
    assert env_vars.get("INSTANCER_DEV") == "0", f"Expected INSTANCER_DEV='0', got {env_vars.get('INSTANCER_DEV')}"

    # 2. No example manifest in volumes
    volumes = instancer.get("volumes", [])
    vol_sources = [v.get("source", "") for v in volumes]
    assert not any("challenges.example.toml" in src for src in vol_sources), f"Found example manifest in production: {vol_sources}"
    assert any("/etc/l3mon/challenges.toml" in src for src in vol_sources), f"Expected external manifest in production, got {vol_sources}"

    # 3. No development token given to any service
    for sname, sdef in services.items():
        secrets_list = sdef.get("secrets", [])
        sec_names = [s.get("source") if isinstance(s, dict) else s for s in secrets_list]
        assert "PRESET_ADMIN_TOKEN" not in sec_names, f"Service {sname} received PRESET_ADMIN_TOKEN in production!"

    # 4. Router published on 0.0.0.0 (not 127.0.0.1) for HTTP and TCP range
    ports = router.get("ports", [])
    assert len(ports) == 201, f"Expected 201 published ports (1 HTTP + 200 TCP), got {len(ports)}"
    assert all(p.get("host_ip") == "0.0.0.0" for p in ports)
    published_ports = {int(p.get("target")) for p in ports}
    assert 8090 in published_ports
    assert 30000 in published_ports
    assert 30199 in published_ports

    # 5. INSTANCE_DOMAIN configured
    router_env = router.get("environment", {})
    assert router_env.get("INSTANCE_DOMAIN") == "instances.l3m0nctf.xyz"

    # 6. Both instancer and router receive the same INSTANCER_INSTANCE_POOL
    inst_pool = instancer.get("environment", {}).get("INSTANCER_INSTANCE_POOL")
    router_pool = router.get("environment", {}).get("INSTANCER_INSTANCE_POOL")
    assert inst_pool is not None and inst_pool == router_pool

    # 7. A platform with ONE instancer (every deployment today) needs no multi-host file at all: nothing of the optional
    #    multi-host overlay (compose.instancer-multihost.yml) is in the production overlay. A mount or a secret here that points at a
    #    file an operator does not have makes `docker compose create` fail ("secret file does not exist").
    ctfd = services.get("ctfd", {})
    ctfd_vol_sources = [v.get("source", "") for v in ctfd.get("volumes", [])]
    assert not any("hosts.json" in src for src in ctfd_vol_sources), f"single-host production must not mount hosts.json into ctfd: {ctfd_vol_sources}"
    router_vol_sources = [v.get("source", "") for v in router.get("volumes", [])]
    assert not any("hosts.json" in src for src in router_vol_sources), f"single-host production must not mount hosts.json into the router: {router_vol_sources}"
    assert "L3MON_HOSTS_PATH" not in ctfd.get("environment", {})
    assert "ROUTER_HOSTS_PATH" not in router.get("environment", {})
    for svc in (ctfd, router):
        names = [s.get("source") if isinstance(s, dict) else s for s in svc.get("secrets", [])]
        assert not any("TOKEN_H" in n for n in names), f"single-host production must not require per-host secrets: {names}"
    assert not [k for k in data.get("secrets", {}) if "TOKEN_H" in k], "single-host production must declare no per-host secrets"


def test_multihost_overlay_mounts_hosts_and_two_token_folders(tmp_path):
    """The optional overlay: hosts.json into ctfd and the router; one folder of INSTANCER_TOKEN_<NAME> files into ctfd ONLY and one
    folder of ROUTER_TOKEN_<NAME> files into the router ONLY (least privilege), for any number of hosts, with no edit of a compose file."""
    hosts = tmp_path / "hosts.json"
    inst_dir = tmp_path / "instancer-tokens"
    router_dir = tmp_path / "router-tokens"
    for d in (inst_dir, router_dir):
        d.mkdir()
    hosts.write_text("[]", encoding="utf-8")
    env = {
        "INSTANCE_DOMAIN": "instances.l3m0nctf.xyz",
        "INSTANCER_INSTANCE_POOL": "10.200.0.0/16",
        "L3MON_HOSTS_FILE": str(hosts),
        "L3MON_INSTANCER_TOKENS_DIR": str(inst_dir),
        "L3MON_ROUTER_TOKENS_DIR": str(router_dir),
    }
    data = render_compose("dev", ["instancer", "instancer-multihost"], extra_env=env)
    services = data.get("services", {})
    ctfd, router = services["ctfd"], services["router"]

    def mounts(svc):
        return {v.get("target"): Path(v.get("source", "")).resolve() for v in svc.get("volumes", [])}

    ctfd_m, router_m = mounts(ctfd), mounts(router)
    assert ctfd_m["/etc/l3mon/hosts.json"] == hosts.resolve()
    assert router_m["/etc/l3mon/hosts.json"] == hosts.resolve()
    assert ctfd_m["/run/host-secrets"] == inst_dir.resolve(), "ctfd gets the INSTANCER token folder"
    assert router_m["/run/host-secrets"] == router_dir.resolve(), "the router gets the ROUTER token folder"
    assert all(v.get("read_only") for svc in (ctfd, router) for v in svc.get("volumes", []) if v.get("target") in ("/etc/l3mon/hosts.json", "/run/host-secrets"))
    assert ctfd["environment"]["L3MON_HOSTS_PATH"] == "/etc/l3mon/hosts.json"
    assert router["environment"]["ROUTER_HOSTS_PATH"] == "/etc/l3mon/hosts.json"
    assert ctfd["environment"]["L3MON_HOST_SECRETS_DIR"] == "/run/host-secrets"
    assert router["environment"]["L3MON_HOST_SECRETS_DIR"] == "/run/host-secrets"
    # nothing in the overlay is tied to a host name: a second host is a line in hosts.json and two files, not an edit here
    lines = (ROOT / "deploy" / "compose" / "compose.instancer-multihost.yml").read_text(encoding="utf-8").splitlines()
    config = " ".join(line for line in lines if not line.lstrip().startswith("#"))  # the comments may use host names as examples
    assert "_H1" not in config and "_H2" not in config


@pytest.mark.parametrize("missing", ["L3MON_HOSTS_FILE", "L3MON_INSTANCER_TOKENS_DIR", "L3MON_ROUTER_TOKENS_DIR"])
def test_multihost_overlay_fails_loudly_without_its_paths(tmp_path, missing):
    env = {k: v for k, v in os.environ.items() if k not in ("L3MON_HOSTS_FILE", "L3MON_INSTANCER_TOKENS_DIR", "L3MON_ROUTER_TOKENS_DIR")}
    env.update({"INSTANCE_DOMAIN": "x.example", "L3MON_HOSTS_FILE": str(tmp_path), "L3MON_INSTANCER_TOKENS_DIR": str(tmp_path), "L3MON_ROUTER_TOKENS_DIR": str(tmp_path)})
    del env[missing]
    files = []
    for name in ("compose.base.yml", "compose.dev.yml", "compose.instancer.yml", "compose.instancer-multihost.yml"):
        files += ["-f", str(ROOT / "deploy" / "compose" / name)]
    res = subprocess.run(["docker", "compose", *files, "config", "--format", "json"], cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode != 0
    assert missing in res.stderr + res.stdout


def test_production_overlay_fails_without_instance_domain():
    """Rendering production overlay without INSTANCE_DOMAIN must fail loudly."""
    files = [
        "-f", str(ROOT / "deploy" / "compose" / "compose.base.yml"),
        "-f", str(ROOT / "deploy" / "compose" / "compose.production.yml"),
        "-f", str(ROOT / "deploy" / "compose" / "compose.instancer.yml"),
        "-f", str(ROOT / "deploy" / "compose" / "compose.instancer-production.yml"),
    ]
    env = {k: v for k, v in os.environ.items() if k not in ("INSTANCE_DOMAIN", "INSTANCER_INSTANCE_POOL")}
    env["INSTANCE_DOMAIN"] = ""
    env["INSTANCER_INSTANCE_POOL"] = "10.200.0.0/16"

    cmd = ["docker", "compose", *files, "config", "--format", "json"]
    res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode != 0, "Expected docker compose config to fail when INSTANCE_DOMAIN is empty"
    combined = res.stderr + res.stdout
    assert "INSTANCE_DOMAIN must be set" in combined, f"Expected error text not found in: {combined}"


def test_production_overlay_fails_without_instance_pool():
    """Rendering production overlay without INSTANCER_INSTANCE_POOL must fail loudly."""
    files = [
        "-f", str(ROOT / "deploy" / "compose" / "compose.base.yml"),
        "-f", str(ROOT / "deploy" / "compose" / "compose.production.yml"),
        "-f", str(ROOT / "deploy" / "compose" / "compose.instancer.yml"),
        "-f", str(ROOT / "deploy" / "compose" / "compose.instancer-production.yml"),
    ]
    env = {k: v for k, v in os.environ.items() if k not in ("INSTANCE_DOMAIN", "INSTANCER_INSTANCE_POOL")}
    env["INSTANCE_DOMAIN"] = "instances.l3m0nctf.xyz"
    env["INSTANCER_INSTANCE_POOL"] = ""

    cmd = ["docker", "compose", *files, "config", "--format", "json"]
    res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode != 0, "Expected docker compose config to fail when INSTANCER_INSTANCE_POOL is empty"
    combined = res.stderr + res.stdout
    assert "INSTANCER_INSTANCE_POOL must be set in production" in combined, f"Expected error text not found in: {combined}"


def test_challenge_host_compose_properties():
    """Validates deploy/compose/compose.challenge-host.yml standalone configuration."""
    ch_file = ROOT / "deploy" / "compose" / "compose.challenge-host.yml"
    assert ch_file.is_file(), f"Missing compose.challenge-host.yml at {ch_file}"

    cmd = ["docker", "compose", "-f", str(ch_file), "config", "--format", "json"]
    res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, f"docker compose config failed for challenge host: {res.stderr}\nOutput:\n{res.stdout}"

    data = json.loads(res.stdout)
    services = data.get("services", {})
    assert set(services.keys()) == {"agent", "socket-proxy", "router"}

    agent = services["agent"]
    socket_proxy = services["socket-proxy"]
    router = services["router"]

    # 1. Agent configuration and hardening
    assert agent.get("read_only") is True
    assert agent.get("cap_drop") == ["ALL"]
    assert "no-new-privileges:true" in agent.get("security_opt", [])
    assert agent.get("environment", {}).get("INSTANCER_AGENT_MODE") == "1"
    assert agent.get("environment", {}).get("ROUTER_CONTAINER_NAME") == "l3mon-challenge-host-router-1"
    assert int(agent.get("mem_limit")) == 268435456 or agent.get("mem_limit") == "256m"  # 256MB
    assert agent.get("pids_limit") == 128

    # 2. Socket proxy image pinned by digest and hardened (must NOT be read_only due to haproxy runtime config)
    assert "@sha256:" in socket_proxy.get("image", "")
    assert socket_proxy.get("read_only") is not True
    assert socket_proxy.get("cap_drop") == ["ALL"]
    assert socket_proxy.get("pids_limit") == 64

    # 3. Router configuration and hardening
    assert router.get("read_only") is True
    assert router.get("cap_drop") == ["ALL"]
    assert "no-new-privileges:true" in router.get("security_opt", [])
    ports = router.get("ports", [])
    assert len(ports) == 201, f"Expected 201 ports, got {len(ports)}"
    tcp_ports = [p for p in ports if int(p.get("target", 0)) in range(30000, 30200)]
    assert len(tcp_ports) == 200
    assert all(p.get("host_ip") == "0.0.0.0" for p in tcp_ports)

    # 4. Same INSTANCER_INSTANCE_POOL and INSTANCER_POOL_SUBNET_SIZE for agent and router
    agent_pool = agent.get("environment", {}).get("INSTANCER_INSTANCE_POOL")
    router_pool = router.get("environment", {}).get("INSTANCER_INSTANCE_POOL")
    assert agent_pool is not None and agent_pool == router_pool

    agent_sub_size = agent.get("environment", {}).get("INSTANCER_POOL_SUBNET_SIZE")
    router_sub_size = router.get("environment", {}).get("INSTANCER_POOL_SUBNET_SIZE")
    assert agent_sub_size == "28" and router_sub_size == "28"
    assert "INSTANCER_TRUSTED_FORWARDERS" in router.get("environment", {})

    # 5. Logging rotation explicitly configured on all services
    for sname, sdef in services.items():
        logging_opts = sdef.get("logging", {}).get("options", {})
        assert logging_opts.get("max-size") in ("10m", "50m"), f"Service {sname} missing max-size logging"
        assert logging_opts.get("max-file") in ("3", "5"), f"Service {sname} missing max-file logging"


