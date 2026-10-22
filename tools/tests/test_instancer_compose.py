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

    # Dev router published on loopback 127.0.0.1
    ports = router.get("ports", [])
    assert len(ports) > 0
    assert ports[0].get("host_ip") == "127.0.0.1"


def test_production_overlay_properties():
    data = render_compose(
        "production",
        ["instancer", "instancer-production"],
        extra_env={"INSTANCE_DOMAIN": "instances.l3m0nctf.xyz"},
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

    # 4. Router published on 0.0.0.0 (not 127.0.0.1)
    ports = router.get("ports", [])
    assert len(ports) > 0
    assert ports[0].get("host_ip") == "0.0.0.0"

    # 5. INSTANCE_DOMAIN configured
    router_env = router.get("environment", {})
    assert router_env.get("INSTANCE_DOMAIN") == "instances.l3m0nctf.xyz"



def test_production_overlay_fails_without_instance_domain():
    """Rendering production overlay without INSTANCE_DOMAIN must fail loudly."""
    files = [
        "-f", str(ROOT / "deploy" / "compose" / "compose.base.yml"),
        "-f", str(ROOT / "deploy" / "compose" / "compose.production.yml"),
        "-f", str(ROOT / "deploy" / "compose" / "compose.instancer.yml"),
        "-f", str(ROOT / "deploy" / "compose" / "compose.instancer-production.yml"),
    ]
    env = {k: v for k, v in os.environ.items() if k != "INSTANCE_DOMAIN"}
    env["INSTANCE_DOMAIN"] = ""

    cmd = ["docker", "compose", *files, "config", "--format", "json"]
    res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode != 0, "Expected docker compose config to fail when INSTANCE_DOMAIN is empty"
    combined = res.stderr + res.stdout
    assert "INSTANCE_DOMAIN must be set" in combined, f"Expected error text not found in: {combined}"

