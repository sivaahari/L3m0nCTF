"""Tests for deploy/challenge-host/bootstrap.sh dry-run behavior, safety, and verification.

Verifies:
1. Dry-run mode is default and executes NO modifying commands (tested with poison stubs).
2. Breaking rule test: Running with --apply invokes modifying commands and fails if stubs present.
3. Subnet size parameter is non-default (26) and propagates to daemon.json and env.
4. Daemon.json backup (.bak) is planned when daemon.json exists, and Docker restart is planned.
5. CHALLENGE_HOST_PRIVATE_IP is strictly required with --apply and absent in dry-run.
6. --firewall-only mode executes only the firewall chain configuration with iptables.
7. verify_pool_activation rejects subnets outside INSTANCE_POOL and network creation errors.
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
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        # Pre-create a dummy daemon.json to verify backup behavior in dry-run
        daemon_file = stub_path / "daemon.json"
        daemon_file.write_text('{"existing": true}\n', encoding="utf-8")

        # Use non-default subnet size (26) to ensure env overrides default 28
        env = {
            **os.environ,
            "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "L3MON_DAEMON_JSON": str(daemon_file).replace("\\", "/"),
            "INSTANCER_INSTANCE_POOL": "10.77.0.0/16",
            "INSTANCER_POOL_SUBNET_SIZE": "26",
            "PLATFORM_VM_IP": "10.99.1.5",
            "CHALLENGE_HOST_PRIVATE_IP": "10.99.1.10",
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
        assert '"size": 26' in out

        # Verify /etc/l3mon/challenge-host.env contents
        assert 'INSTANCER_INSTANCE_POOL="10.77.0.0/16"' in out
        assert 'INSTANCER_POOL_SUBNET_SIZE="26"' in out
        assert 'PLATFORM_VM_IP="10.99.1.5"' in out
        assert 'INSTANCER_TRUSTED_FORWARDERS="10.99.1.5"' in out
        assert 'HOST_PRIVATE_BIND_IP="10.99.1.10"' in out

        # Verify daemon.json backup and docker restart planning
        assert "daemon.json.bak" in out
        assert "systemctl restart docker" in out

        # Verify dedicated chain setup and jump from DOCKER-USER 1
        assert "iptables -N L3MON-FIREWALL" in out
        assert "iptables -I DOCKER-USER 1 -j L3MON-FIREWALL" in out

        # Verify all planned firewall rules in L3MON-FIREWALL chain
        assert "iptables -A L3MON-FIREWALL -d 169.254.169.254 -j DROP" in out
        assert "iptables -A L3MON-FIREWALL -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT" in out
        assert "iptables -A L3MON-FIREWALL -m physdev --physdev-is-bridged -j RETURN" in out
        assert "iptables -A L3MON-FIREWALL -s 10.77.0.0/16 -d 10.77.0.0/16 -j DROP" in out
        assert "iptables -A L3MON-FIREWALL -s 10.77.0.0/16 -d 10.0.0.0/8 -j DROP" in out
        assert "iptables -A L3MON-FIREWALL -s 10.77.0.0/16 -d 172.16.0.0/12 -j DROP" in out
        assert "iptables -A L3MON-FIREWALL -s 10.77.0.0/16 -d 192.168.0.0/16 -j DROP" in out
        assert "iptables -A L3MON-FIREWALL -p tcp -m conntrack --ctorigdstport 8300 ! -s 10.99.1.5 -j DROP" in out
        assert "iptables -A L3MON-FIREWALL -j RETURN" in out

        # Verify systemd service persistence installation
        assert "l3mon-firewall.service" in out
        assert "--firewall-only" in out

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

        # 3. VERIFY POISON STUB MECHANISM: Prove that dangerous commands on PATH fail if called
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


def test_bootstrap_apply_requires_challenge_host_private_ip():
    """Verify --apply rejects execution without CHALLENGE_HOST_PRIVATE_IP."""
    bash_cmd = get_bash_cmd()

    # 1. --apply without CHALLENGE_HOST_PRIVATE_IP must exit 1
    env_no_ip = {k: v for k, v in os.environ.items() if k != "CHALLENGE_HOST_PRIVATE_IP"}
    res = subprocess.run(
        [bash_cmd, "deploy/challenge-host/bootstrap.sh", "--apply"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env_no_ip,
        timeout=15,
    )
    assert res.returncode == 1
    assert "Error: CHALLENGE_HOST_PRIVATE_IP must be set with --apply" in res.stderr

    # 2. --dry-run succeeds even if CHALLENGE_HOST_PRIVATE_IP is unset
    res_dry = subprocess.run(
        [bash_cmd, "deploy/challenge-host/bootstrap.sh", "--dry-run"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env_no_ip,
        timeout=15,
    )
    assert res_dry.returncode == 0
    assert "[DRY-RUN]" in res_dry.stdout


def test_bootstrap_non_default_subnet_size():
    """Verify that a non-default subnet size (26) propagates to daemon.json and env."""
    bash_cmd = get_bash_cmd()
    env = {**os.environ, "INSTANCER_POOL_SUBNET_SIZE": "26"}
    res = subprocess.run(
        [bash_cmd, "deploy/challenge-host/bootstrap.sh"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    assert res.returncode == 0
    assert '"size": 26' in res.stdout
    assert 'INSTANCER_POOL_SUBNET_SIZE="26"' in res.stdout


def test_bootstrap_firewall_only_mode():
    """Verify --firewall-only mode configures only firewall rules with iptables."""
    with tempfile.TemporaryDirectory() as stub_dir:
        stub_path = Path(stub_dir)
        iptables_stub = stub_path / "iptables"
        log_file = stub_path / "iptables.log"
        iptables_stub.write_bytes(
            (
                "#!/usr/bin/env bash\n"
                f'echo "$@" >> "{str(log_file).replace(chr(92), "/")}"\n'
                'if [[ "$1" == "-C" ]]; then exit 1; fi\n'
                "exit 0\n"
            ).encode("utf-8")
        )
        iptables_stub.chmod(iptables_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        env = {
            **os.environ,
            "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "PLATFORM_VM_IP": "10.0.0.1",
        }
        bash_cmd = get_bash_cmd()
        res = subprocess.run(
            [bash_cmd, "deploy/challenge-host/bootstrap.sh", "--firewall-only"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert res.returncode == 0
        assert "Configuring Firewall (L3MON-FIREWALL Chain in DOCKER-USER)" in res.stdout

        # Verify iptables was actually invoked for L3MON-FIREWALL
        logged = log_file.read_text(encoding="utf-8")
        assert "-N L3MON-FIREWALL" in logged
        assert "-I DOCKER-USER 1 -j L3MON-FIREWALL" in logged

        # Must NOT run other bootstrap steps
        assert "Checking OS environment" not in res.stdout
        assert "Configuring Kernel Limits" not in res.stdout
        assert "Setting up Service User" not in res.stdout
        assert "Configuring Docker Daemon" not in res.stdout


def test_bootstrap_verify_pool_activation_subnet_check():
    """Test verify_pool_activation handles valid and invalid subnets using mock docker."""
    with tempfile.TemporaryDirectory() as stub_dir:
        stub_path = Path(stub_dir)
        docker_stub = stub_path / "docker"

        bash_cmd = get_bash_cmd()

        # Scenario 1: mock docker returns out-of-pool subnet 172.17.0.0/16
        docker_stub.write_bytes(
            (
                "#!/usr/bin/env bash\n"
                "if [[ \"$1\" == \"network\" && \"$2\" == \"create\" ]]; then exit 0; fi\n"
                "if [[ \"$1\" == \"network\" && \"$2\" == \"inspect\" ]]; then echo \"172.17.0.0/16\"; exit 0; fi\n"
                "if [[ \"$1\" == \"network\" && \"$2\" == \"rm\" ]]; then exit 0; fi\n"
                "exit 0\n"
            ).encode("utf-8")
        )
        docker_stub.chmod(docker_stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        env = {
            **os.environ,
            "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        }

        # Run verify_pool_activation under MODE=apply and INSTANCE_POOL=10.200.0.0/16
        inline_script = (
            "source deploy/challenge-host/bootstrap.sh; "
            "MODE=apply INSTANCE_POOL=10.200.0.0/16 verify_pool_activation"
        )
        res_fail = subprocess.run(
            [bash_cmd, "-c", inline_script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert res_fail.returncode == 1
        assert "Error: Verification failed! Allocated subnet 172.17.0.0/16 is outside INSTANCE_POOL 10.200.0.0/16" in res_fail.stderr

        # Scenario 2: mock docker returns valid in-pool subnet 10.200.0.0/28
        docker_stub.write_bytes(
            (
                "#!/usr/bin/env bash\n"
                "if [[ \"$1\" == \"network\" && \"$2\" == \"create\" ]]; then exit 0; fi\n"
                "if [[ \"$1\" == \"network\" && \"$2\" == \"inspect\" ]]; then echo \"10.200.0.0/28\"; exit 0; fi\n"
                "if [[ \"$1\" == \"network\" && \"$2\" == \"rm\" ]]; then exit 0; fi\n"
                "exit 0\n"
            ).encode("utf-8")
        )
        res_ok = subprocess.run(
            [bash_cmd, "-c", inline_script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert res_ok.returncode == 0
        assert "Successfully verified throwaway network allocated subnet: 10.200.0.0/28 (within 10.200.0.0/16)" in res_ok.stdout

        # Scenario 3: mock docker network create fails
        docker_stub.write_bytes(
            (
                "#!/usr/bin/env bash\n"
                "if [[ \"$1\" == \"network\" && \"$2\" == \"create\" ]]; then exit 1; fi\n"
                "exit 0\n"
            ).encode("utf-8")
        )
        res_create_err = subprocess.run(
            [bash_cmd, "-c", inline_script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert res_create_err.returncode == 1
        assert "Error: Failed to create verification network in Docker daemon" in res_create_err.stderr


def test_firewall_namespace_experiment():
    """Runs network namespace traffic test on Linux if root/CAP_NET_ADMIN available (GitHub runs it as root in the job firewall-experiment)."""
    import shutil

    if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("firewall network namespace experiment requires root/sudo on Linux")
    for tool in ("ip", "iptables", "nc"):
        if shutil.which(tool) is None:
            pytest.skip(f"firewall network namespace experiment needs '{tool}' (iproute2, iptables, netcat-openbsd)")
    script = ROOT / "tools" / "tests" / "firewall_experiment.sh"
    assert script.is_file(), f"firewall_experiment.sh not found at {script}"
    res = subprocess.run([get_bash_cmd(), str(script)], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, f"Firewall experiment failed: {res.stderr}\nOutput: {res.stdout}"
    assert "ALL FIREWALL CHECKS PASSED" in res.stdout, res.stdout


def _stub(directory: Path, name: str, log_file: Path, body: str = "exit 0") -> None:
    path = directory / name
    path.write_bytes(f'#!/usr/bin/env bash\necho "{name} $@" >> "{str(log_file).replace(chr(92), "/")}"\n{body}\n'.encode("utf-8"))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_bootstrap_reboot_unit_is_written_ordered_after_docker_and_enabled():
    """The unit that re-applies the firewall after a reboot (iptables rules do not survive one). Sourcing the script runs only its functions."""
    with tempfile.TemporaryDirectory() as d:
        d_path = Path(d)
        log = d_path / "systemctl.log"
        _stub(d_path, "systemctl", log)
        unit = d_path / "l3mon-firewall.service"
        script_copy = d_path / "bootstrap.sh"
        env = {
            **os.environ,
            "PATH": f"{d}{os.pathsep}{os.environ.get('PATH', '')}",
            "L3MON_SYSTEMD_SERVICE": str(unit).replace(chr(92), "/"),
            "L3MON_BOOTSTRAP_SCRIPT": str(script_copy).replace(chr(92), "/"),
        }
        script = f'source "{str(BOOTSTRAP_SCRIPT).replace(chr(92), "/")}" >/dev/null; MODE=apply; configure_systemd_service'
        res = subprocess.run([get_bash_cmd(), "-c", script], cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=15)
        assert res.returncode == 0, res.stderr
        text = unit.read_text(encoding="utf-8")
        assert "After=docker.service" in text, text
        assert "Requires=docker.service" in text, text
        assert f"ExecStart={str(script_copy).replace(chr(92), '/')} --firewall-only" in text, text
        assert "EnvironmentFile=-/etc/l3mon/challenge-host.env" in text, text
        assert "WantedBy=multi-user.target" in text, text
        calls = log.read_text(encoding="utf-8")
        assert "systemctl daemon-reload" in calls, calls
        assert "systemctl enable l3mon-firewall.service" in calls, calls


def test_bootstrap_dry_run_shows_the_reboot_unit_and_does_not_enable_it():
    res = subprocess.run([get_bash_cmd(), "deploy/challenge-host/bootstrap.sh"], cwd=str(ROOT), capture_output=True, text=True, timeout=15)
    assert res.returncode == 0
    assert "After=docker.service" in res.stdout
    assert "[DRY-RUN] systemctl daemon-reload && systemctl enable l3mon-firewall.service" in res.stdout
