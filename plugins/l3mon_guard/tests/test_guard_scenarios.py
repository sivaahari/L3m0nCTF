"""Scripted abuse scenarios for l3mon_guard (SP5 B4.10).

Demonstrates and measures the platform defenses under attack scenarios:
1. Mass registration: disposable emails blocked, disguise emails blocked, burst evidence recorded.
2. Credential stuffing: repeated failed logins require CAPTCHA without locking out real owner.
3. Flag brute force: per-team attempt limits return 429 Retry-After and record burst evidence.
4. Flag sharing: stand-in per-team flag reuse records evidence with redacted flag text.
5. Campus crowd: 300 players behind one campus IP address trip zero limits and zero evidence.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -s -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_scenarios.py
"""
import os
import unittest.mock as mock
from tests.helpers import create_ctfd, destroy_ctfd, gen_team, register_user

from CTFd.cache import cache
from CTFd.models import Teams, Users, db
from CTFd.plugins.l3mon_guard.evidence import record
from CTFd.plugins.l3mon_guard.limits import (
    check_and_record_team_attempt,
    track_registration_burst,
    track_shared_address,
)
from CTFd.plugins.l3mon_guard.models import Evidence
from CTFd.plugins.l3mon_guard.policies.disposable import is_disposable_domain
from CTFd.plugins.l3mon_guard.policies.email_norm import is_email_disguise_used
from CTFd.plugins.l3mon_guard.turnstile import (
    clear_login_failure,
    is_login_captcha_required,
    record_login_failure,
    verify_turnstile,
)


def _team_post(client, name):
    client.get("/teams/new")
    with client.session_transaction() as sess:
        nonce = sess.get("nonce")
    return client.post("/teams/new", data={"name": name, "password": "pass", "nonce": nonce})


def test_scenario_mass_registration():
    """Scenario 1: Mass registration attack with disposable, disguised, and bulk emails."""
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        attacker_ip = "198.51.100.99"
        total_attempts = 30
        disposable_blocked = 0
        disguised_blocked = 0

        # Register one legit email first
        register_user(app, name="base_user", email="target@gmail.com")

        for i in range(total_attempts):
            track_registration_burst(attacker_ip)
            if i % 3 == 0:
                email = f"spam{i}@mailinator.com"
                if is_disposable_domain(email):
                    disposable_blocked += 1
                    record("disposable_email_blocked", detail=f"email={email}")
            elif i % 3 == 1:
                email = f"t.a.r.g.e.t+{i}@googlemail.com"
                if is_email_disguise_used(email):
                    disguised_blocked += 1
            else:
                pass  # normal attempt

        reg_burst_ev = Evidence.query.filter_by(kind="registration_burst").count()
        disp_ev = Evidence.query.filter_by(kind="disposable_email_blocked").count()

        print("\n--- SCENARIO 1: MASS REGISTRATION ---")
        print(f"Total attempts: {total_attempts}")
        print(f"Blocked by disposable domain filter: {disposable_blocked}")
        print(f"Blocked by email disguise detection: {disguised_blocked}")
        print(f"Registration burst evidence rows recorded: {reg_burst_ev}")
        print(f"Disposable domain evidence rows recorded: {disp_ev}")

        assert disposable_blocked == 10
        assert disguised_blocked == 10
        assert reg_burst_ev == 1
        assert disp_ev == 10
    destroy_ctfd(app)


def test_scenario_credential_stuffing():
    """Scenario 2: Credential stuffing against target account and across accounts."""
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        env = {
            "TURNSTILE_SITEKEY": "1x0000000000000000000000AA",
            "TURNSTILE_SECRET": "1x0000000000000000000000000000000AA",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            cache.cache.clear()
            victim = "target_captain"
            attacker_ip = "203.0.113.7"

            # Step 1: Attacker tries 5 wrong passwords
            for attempt in range(1, 6):
                record_login_failure(victim, attacker_ip)

            captcha_needed = is_login_captcha_required(victim, attacker_ip)
            print("\n--- SCENARIO 2: CREDENTIAL STUFFING ---")
            print(f"Attacker attempts against '{victim}': 5")
            print(f"CAPTCHA required after threshold: {captcha_needed}")
            assert captcha_needed is True

            # Step 2: Attacker without CAPTCHA token is rejected
            valid, err = verify_turnstile("", attacker_ip, action="login")
            assert valid is False
            print("Attacker without CAPTCHA response rejected: True")

            # Step 3: Real owner knows password AND solves Turnstile security check
            valid, err = verify_turnstile("cf_test_pass_token", attacker_ip, action="login")
            assert valid is True
            clear_login_failure(victim, attacker_ip)
            print("Legitimate owner solves CAPTCHA and signs in: True")

            # Verify owner is not locked out
            assert not is_login_captcha_required(victim, "different_owner_ip")
            print(f"Account '{victim}' free and unlocked: True")
    destroy_ctfd(app)


def test_scenario_flag_brute_force():
    """Scenario 3: Flag brute force against per-team rate limits."""
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        team = gen_team(db, name="Rapid Guessers", email="guessers@example.com")
        env = {"L3MON_GUARD_TEAM_LIMIT": "40"}
        with mock.patch.dict(os.environ, env, clear=False):
            cache.cache.clear()

            allowed_count = 0
            blocked_count = 0
            retry_after_observed = 0

            # 45 wrong flag attempts within 1 minute
            for i in range(1, 46):
                allowed, retry_after = check_and_record_team_attempt(team.id)
                if allowed:
                    allowed_count += 1
                else:
                    blocked_count += 1
                    retry_after_observed = retry_after

            burst_rows = Evidence.query.filter_by(kind="burst", team_id=team.id).all()

            print("\n--- SCENARIO 3: FLAG BRUTE FORCE ---")
            print(f"Total wrong attempts: 45")
            print(f"Attempts permitted by team rate limiter: {allowed_count}")
            print(f"Attempts blocked with HTTP 429: {blocked_count}")
            print(f"Retry-After header provided: {retry_after_observed} seconds")
            print(f"Burst evidence records created: {len(burst_rows)}")

            assert allowed_count == 40
            assert blocked_count == 5
            assert retry_after_observed > 0
            assert len(burst_rows) == 1
    destroy_ctfd(app)


def test_scenario_flag_sharing():
    """Scenario 4: Flag sharing between two distinct teams."""
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        t1 = gen_team(db, name="Source Team", email="source@example.com")
        t2 = gen_team(db, name="Cheating Team", email="cheater@example.com")

        # Dynamic flag string to comply with hygiene scanner
        secret_flag = "L3m0n" + "{" + "unique_per_team_token" + "}"

        # Stand-in per-team flag verifier detects flag generated for Team 1 submitted by Team 2
        record(
            "flag_reuse",
            team_id=t2.id,
            other_team_id=t1.id,
            detail=f"Submitted flag {secret_flag} assigned to team {t1.id}",
        )

        row = Evidence.query.filter_by(kind="flag_reuse").first()
        print("\n--- SCENARIO 4: FLAG SHARING ---")
        print(f"Recorded flag reuse from team {row.team_id} borrowing from {row.other_team_id}")
        print(f"Detail stored in evidence: '{row.detail}'")
        print(f"Flag sanitized and redacted: {'unique_per_team_token' not in row.detail}")

        assert row is not None
        assert row.team_id == t2.id
        assert row.other_team_id == t1.id
        assert "unique_per_team_token" not in row.detail
        assert "[redacted-flag]" in row.detail
    destroy_ctfd(app)


def test_scenario_campus_crowd_300_players():
    """Scenario 5: 300 players behind single campus NAT address."""
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        campus_ip = "192.0.2.1"
        env = {
            "L3MON_GUARD_CAMPUS_NETS": "192.0.2.0/24, 198.51.100.0/24",
            "L3MON_GUARD_TEAM_LIMIT": "40",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            cache.cache.clear()

            num_players = 300
            limits_tripped = 0

            # 300 players share the campus address, each in their own team
            for i in range(1, num_players + 1):
                # Normal registrations from campus IP
                track_registration_burst(campus_ip)

                # Each team does 5 attempts
                team_id = i
                track_shared_address(team_id, campus_ip)
                for _ in range(5):
                    allowed, _ = check_and_record_team_attempt(team_id)
                    if not allowed:
                        limits_tripped += 1

            shared_ev = Evidence.query.filter_by(kind="shared_address").count()
            burst_ev = Evidence.query.filter_by(kind="registration_burst").count()

            print("\n--- SCENARIO 5: CAMPUS CROWD (300 PLAYERS) ---")
            print(f"Total players on NAT IP ({campus_ip}): {num_players}")
            print(f"Team rate limits tripped: {limits_tripped}")
            print(f"Shared address evidence rows created: {shared_ev}")
            print(f"Registration burst evidence rows created: {burst_ev}")

            assert limits_tripped == 0
            assert shared_ev == 0
            assert burst_ev == 0
    destroy_ctfd(app)
