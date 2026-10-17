import os
import pytest
from services.instancer.manifest import ChallengeSpec, Manifest, load_manifest_from_string

VALID_MANIFEST_TOML = """
[[challenge]]
slug = "demo-web"
image = "registry.example/demo-web@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
protocol = "http"
port = 8080
cpus = 0.5
mem_mb = 256
pids = 128
ttl_minutes = 45
read_only = true
max_per_team = 1

[[challenge]]
slug = "demo_pwn"
image = "registry.example/demo-pwn@sha256:fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"
protocol = "tcp"
port = 1337
"""


def test_valid_manifest_parsing():
    manifest = load_manifest_from_string(VALID_MANIFEST_TOML, dev_mode=False)
    assert len(manifest.challenges) == 2

    web = manifest.get("demo-web")
    assert web is not None
    assert web.slug == "demo-web"
    assert web.protocol == "http"
    assert web.port == 8080
    assert web.cpus == 0.5
    assert web.mem_mb == 256
    assert web.pids == 128
    assert web.ttl_minutes == 45
    assert web.read_only is True
    assert web.max_per_team == 1

    pwn = manifest.get("demo_pwn")
    assert pwn is not None
    assert pwn.slug == "demo_pwn"
    assert pwn.protocol == "tcp"
    assert pwn.port == 1337
    # Default values applied
    assert pwn.cpus == 0.5
    assert pwn.mem_mb == 256
    assert pwn.pids == 128
    assert pwn.ttl_minutes == 45
    assert pwn.read_only is True


def test_slug_validation():
    # Bad slugs
    bad_slugs = ["Demo-Web", "-start-dash", "_start-under", "a" * 49, "demo.web", "demo/web", "demo$web", ""]
    for slug in bad_slugs:
        toml_content = f"""
[[challenge]]
slug = "{slug}"
image = "registry.example/web@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
protocol = "http"
port = 8080
"""
        with pytest.raises(ValueError, match="slug"):
            load_manifest_from_string(toml_content, dev_mode=False)


def test_unpinned_digest_rejection():
    # Unpinned image must fail when dev_mode=False
    unpinned_toml = """
[[challenge]]
slug = "unpinned-web"
image = "registry.example/demo-web:latest"
protocol = "http"
port = 8080
"""
    with pytest.raises(ValueError, match="pinned by digest"):
        load_manifest_from_string(unpinned_toml, dev_mode=False)

    # But allowed when dev_mode=True
    manifest_dev = load_manifest_from_string(unpinned_toml, dev_mode=True)
    assert manifest_dev.get("unpinned-web").image == "registry.example/demo-web:latest"


def test_protocol_validation():
    invalid_protocol = """
[[challenge]]
slug = "bad-protocol"
image = "registry.example/web@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
protocol = "udp"
port = 8080
"""
    with pytest.raises(ValueError, match="protocol"):
        load_manifest_from_string(invalid_protocol, dev_mode=False)


def test_port_validation():
    for bad_port in [0, -1, 65536, 100000]:
        bad_port_toml = f"""
[[challenge]]
slug = "bad-port"
image = "registry.example/web@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
protocol = "http"
port = {bad_port}
"""
        with pytest.raises(ValueError, match="port"):
            load_manifest_from_string(bad_port_toml, dev_mode=False)


def test_resource_caps_validation():
    # Exceeding global ceilings (cpus > 2.0, mem_mb > 1024, pids > 256)
    excess_mem = """
[[challenge]]
slug = "excess-mem"
image = "registry.example/web@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
protocol = "http"
port = 8080
mem_mb = 2048
"""
    with pytest.raises(ValueError, match="limit"):
        load_manifest_from_string(excess_mem, dev_mode=False)


def test_duplicate_slug_rejection():
    dup_toml = """
[[challenge]]
slug = "duplicate-web"
image = "registry.example/web@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
protocol = "http"
port = 8080

[[challenge]]
slug = "duplicate-web"
image = "registry.example/web@sha256:fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"
protocol = "http"
port = 8080
"""
    with pytest.raises(ValueError, match="Duplicate"):
        load_manifest_from_string(dup_toml, dev_mode=False)
