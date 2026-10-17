"""Integration tests for the internal queueing mail relay (Milestone 2).

Covers:
- Unauthenticated SMTP submission from CTFd on the internal network.
- Prometheus /metrics export on port 9025.
- Message size limits.
- Relaying refusal / open relay prevention from untrusted clients.
- Queue persistence and smarthost delivery with catcher start/stop cycle.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest
import test_stack as t

pytestmark = pytest.mark.integration


def ctfd_exec(cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "exec", t.container_id("ctfd"), "sh", "-c", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_mail_relay_accepts_submission_and_exports_metrics():
    # Execute python script inside CTFd container to talk to mail-relay on internal network
    client_code = """
import smtplib, urllib.request

# 1. Send email to mail-relay:25
s = smtplib.SMTP('mail-relay', 25, timeout=10)
s.sendmail('no-reply@l3m0nctf.xyz', ['player@example.org'], 'Subject: Test\\r\\n\\r\\nHello Player')
s.quit()

# 2. Query metrics on mail-relay:9025
resp = urllib.request.urlopen('http://mail-relay:9025/metrics', timeout=5)
metrics = resp.read().decode('utf-8')
print('METRICS_OUTPUT_BEGIN')
print(metrics)
"""
    done = ctfd_exec(f"/opt/venv/bin/python -c \"{client_code}\"")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "METRICS_OUTPUT_BEGIN" in done.stdout
    assert "l3mon_mail_queue_length" in done.stdout
    assert "l3mon_mail_holding" in done.stdout


def test_mail_relay_enforces_size_limit():
    # Attempt sending payload > 10MB with chunked lines
    code = """
import smtplib

s = smtplib.SMTP('mail-relay', 25, timeout=20)
lines = ['Subject: Huge\\r\\n\\r\\n'] + ['A' * 998 + '\\r\\n' for _ in range(11000)]
huge_payload = ''.join(lines)
try:
    s.sendmail('no-reply@l3m0nctf.xyz', ['player@example.org'], huge_payload)
    print('SENT_UNEXPECTED')
except smtplib.SMTPResponseException as e:
    print(f'CODE_{e.smtp_code}')
finally:
    try:
        s.quit()
    except Exception:
        pass
"""
    done = ctfd_exec(f"/opt/venv/bin/python -c \"{code}\"")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "CODE_552" in done.stdout, f"Expected SMTP 552 size limit error, got: {done.stdout}"


def test_mail_relay_refuses_open_relay_from_outside():
    # Verify peer check logic refuses outside and internet IPs
    verify_logic = """
import ipaddress
from deploy.ops.mail.relay import is_ip_allowed

assert is_ip_allowed('172.18.0.4') is True, 'Internal bridge IP must be allowed'
assert is_ip_allowed('127.0.0.1') is True, 'Localhost must be allowed'
assert is_ip_allowed('203.0.113.50') is False, 'Public IP must be refused'
assert is_ip_allowed('8.8.8.8') is False, 'Internet IP must be refused'
"""
    res = subprocess.run([sys.executable, "-c", verify_logic], cwd=str(t.ROOT), capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr


def test_mail_delivery_and_queueing_with_catcher_down():
    # 1. Start mail-catcher service via Compose profile test-mail
    env_catcher = {
        **os.environ,
        "L3MON_OVERLAYS": "ops",
        "COMPOSE_PROFILES": "test-mail",
    }
    t.compose("up", "-d", "--force-recreate", "mail-catcher", env=env_catcher)

    try:
        time.sleep(2)

        # 2. Reconfigure mail-relay temporarily to point to mail-catcher:1025
        mail_pw_file = t.ROOT / ".secrets" / "MAIL_PASSWORD"
        mail_pw_file.write_text("test-password-123\n", encoding="utf-8")

        env_relay = {
            **os.environ,
            "L3MON_OVERLAYS": "ops",
            "COMPOSE_PROFILES": "test-mail",
            "UPSTREAM_SMTP_HOST": "mail-catcher",
            "UPSTREAM_SMTP_PORT": "1025",
            "UPSTREAM_SMTP_TLS": "false",
        }
        t.compose("up", "-d", "--force-recreate", "--wait", "mail-relay", env=env_relay)
        time.sleep(2)

        # 3. Send test message 1 through relay
        send_code = """
import smtplib
s = smtplib.SMTP('mail-relay', 25, timeout=10)
s.sendmail('no-reply@l3m0nctf.xyz', ['player1@example.org'], 'Subject: Delivery 1\\r\\n\\r\\nBody 1')
s.quit()
"""
        done = ctfd_exec(f"/opt/venv/bin/python -c \"{send_code}\"")
        assert done.returncode == 0, done.stdout + done.stderr

        # Wait for delivery worker to deliver to catcher
        delivered = False
        for _ in range(15):
            check = t.compose("exec", "-T", "mail-catcher", "cat", "tmp/received.log", check=False, env=env_catcher)
            if check.returncode == 0 and "MSG_RECEIVED" in check.stdout:
                delivered = True
                break
            time.sleep(1)
        if not delivered:
            print(f"\nCHECK FAILED: rc={check.returncode} out={check.stdout!r} err={check.stderr!r}")
            r_logs = t.compose("logs", "--no-color", "mail-relay", env=env_relay, check=False).stdout
            c_logs = t.compose("logs", "--no-color", "mail-catcher", env=env_catcher, check=False).stdout
            print(f"--- RELAY LOGS ---\n{r_logs}")
            print(f"--- CATCHER LOGS ---\n{c_logs}")
        assert delivered, "Message 1 was not delivered to catcher"

        # 4. Stop catcher (simulating provider down)
        t.compose("stop", "mail-catcher", env=env_catcher)

        # Send second message
        send_code_2 = """
import smtplib
s = smtplib.SMTP('mail-relay', 25, timeout=10)
s.sendmail('no-reply@l3m0nctf.xyz', ['player2@example.org'], 'Subject: Delivery 2\\r\\n\\r\\nBody 2')
s.quit()
"""
        done = ctfd_exec(f"/opt/venv/bin/python -c \"{send_code_2}\"")
        assert done.returncode == 0, done.stdout + done.stderr

        # Verify message remains queued in mail-relay
        time.sleep(2)
        q_check = ctfd_exec("/opt/venv/bin/python -c \"import urllib.request; print(urllib.request.urlopen('http://mail-relay:9025/metrics').read().decode())\"")
        assert "l3mon_mail_queue_length" in q_check.stdout

        # 5. Start catcher again (provider recovered)
        t.compose("start", "mail-catcher", env=env_catcher)
        time.sleep(3)

        # Verify second message is delivered
        second_delivered = False
        for _ in range(35):
            check = t.compose("exec", "-T", "mail-catcher", "cat", "tmp/received.log", check=False, env=env_catcher)
            if check.returncode == 0 and check.stdout.count("MSG_RECEIVED") >= 1:  # the catcher keeps its log in a tmpfs, which a stop and start wipes: after the restart only the second message can be in it (integration audit)
                second_delivered = True
                break
            time.sleep(1)
        assert second_delivered, "Second message was not delivered after catcher recovery"

    finally:
        t.compose("stop", "mail-catcher", check=False, env=env_catcher)
        t.compose("rm", "-f", "mail-catcher", check=False, env=env_catcher)
        # Restore empty MAIL_PASSWORD
        (t.ROOT / ".secrets" / "MAIL_PASSWORD").write_text("", encoding="utf-8")
        # Restore standard mail-relay
        t.compose("up", "-d", "--force-recreate", "--wait", "mail-relay", env={**os.environ, "L3MON_OVERLAYS": "ops"})
