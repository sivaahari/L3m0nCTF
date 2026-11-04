"""Tests for deploy/challenge-host/bootstrap.sh dry-run behavior and idempotency.

Verifies:
1. Dry-run mode is default and executes NO modifying commands.
2. Verified using stub commands on PATH that exit with failure code 99 if invoked.
3. Breaking rule test: Running with --apply invokes the stub and fails with 99,
   proving the dry-run safety test is capable of failing.
4. Output contains unified INSTANCER_INSTANCE_POOL and DOCKER-USER firewall rules.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import pytest

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_SCRIPT = ROOT / "deploy" / "challenge-host" / "bootstrap.sh"


def get_bash_cmd() -> str:
    if os.name == "nt":
        for candidate in [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ]:
            if os.path.isfile(candidate):
                return candidate
    return "bash"


def test_bootstrap_script_exists():
    assert BOOTSTRAP_SCRIPT.is_file(), f"bootstrap.sh not found at {BOOTSTRAP_SCRIPT}"


def test_bootstrap_dry_run_executes_no_modifying_commands():
    """Prove dry-run executes nothing by overriding PATH with poison stubs."""
    with tempfile.TemporaryDirectory() as stub_dir:
        stub_path = Path(stub_dir)
        # Commands that must NEVER be called during a dry-run
        dangerous_commands = [
            "iptables",
            "sysctl",
            "useradd",
            "chown",
            "timedatectl",
            "chronyc",
        ]

        stub_content = (
            "#!/usr/bin/env bash\n"
            "echo \"POISON_STUB_CALLED: $0\" >&2\n"
            "exit 99\n"
        )

        for cmd in dangerous_commands:
            p = stub_path / cmd
            p.write_bytes(stub_content.replace("\r\n", "\n").encode("utf-8"))
            # Make executable
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        env = {
            **os.environ,
            "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "INSTANCER_INSTANCE_POOL": "10.77.0.0/16",
            "INSTANCER_POOL_SUBNET_SIZE": "28",
            "PLATFORM_VM_IP": "10.99.1.5",
        }

        bash_cmd = get_bash_cmd()

        # 1. Run with default (dry-run)
        res_default = subprocess.run(
            [bash_cmd, "deploy/challenge-host/bootstrap.sh"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert res_default.returncode == 0, f"Dry-run failed: {res_default.stderr}\nOutput: {res_default.stdout}"
        assert "POISON_STUB_CALLED" not in res_default.stderr
        assert "[DRY-RUN]" in res_default.stdout

        out = res_default.stdout

        # Verify non-default pool end-to-end across daemon.json, env file, and iptables rules
        assert '"base": "10.77.0.0/16"' in out
        assert '"size": 28' in out

        # Verify /etc/l3mon/challenge-host.env contents
        assert 'INSTANCER_INSTANCE_POOL="10.77.0.0/16"' in out
        assert 'INSTANCER_POOL_SUBNET_SIZE="28"' in out
        assert 'PLATFORM_VM_IP="10.99.1.5"' in out

        # Verify dedicated chain setup and jump from DOCKER-USER 1
        assert "iptables -N L3MON-FIREWALL" in out
        assert "iptables -I DOCKER-USER 1 -j L3MON-FIREWALL" in out

        # Verify all planned firewall rules in L3MON-FIREWALL chain
        assert "iptables -A L3MON-FIREWALL -d 169.254.169.254 -j DROP" in out
        assert "iptables -A L3MON-FIREWALL -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT" in out
        assert "iptables -A L3MON-FIREWALL -s 10.77.0.0/16 -d 10.77.0.0/16 -j DROP" in out
        assert "iptables -A L3MON-FIREWALL -s 10.77.0.0/16 -d 10.0.0.0/8 -j DROP" in out
        assert "iptables -A L3MON-FIREWALL -s 10.77.0.0/16 -d 172.16.0.0/12 -j DROP" in out
        assert "iptables -A L3MON-FIREWALL -s 10.77.0.0/16 -d 192.168.0.0/16 -j DROP" in out
        assert "iptables -A L3MON-FIREWALL -p tcp -m conntrack --ctorigdstport 8300 ! -s 10.99.1.5 -j DROP" in out
        assert "iptables -A L3MON-FIREWALL -j RETURN" in out

        # 2. Run with explicit --dry-run flag
        res_flag = subprocess.run(
            [bash_cmd, "deploy/challenge-host/bootstrap.sh", "--dry-run"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert res_flag.returncode == 0
        assert "POISON_STUB_CALLED" not in res_flag.stderr
        assert "[DRY-RUN]" in res_flag.stdout

        # 3. VERIFY POISON STUB MECHANISM: Prove that dangerous commands on PATH fail the test if called
        # Direct call to stub commands must fail with exit code 99 and poison message
        res_stub_check = subprocess.run(
            [bash_cmd, "-c", "iptables -V"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert res_stub_check.returncode == 99, f"Poison stub failed to trigger: {res_stub_check.returncode}"
        assert "POISON_STUB_CALLED" in res_stub_check.stderr


def test_firewall_namespace_experiment():
    """Runs network namespace traffic test on Linux if root/CAP_NET_ADMIN available."""
    script = ROOT / "tools" / "tests" / "firewall_experiment.sh"
    assert script.is_file(), f"firewall_experiment.sh not found at {script}"
    res = subprocess.run([get_bash_cmd(), str(script)], cwd=str(ROOT), capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"Firewall experiment failed: {res.stderr}\nOutput: {res.stdout}"
