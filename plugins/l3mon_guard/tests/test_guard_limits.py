"""Tests for per-team rate limits, campus network allowlisting, and abuse heuristics (SP5 B4.6, B4.8).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_limits.py
"""
import os
import unittest.mock as mock
from tests.helpers import create_ctfd, destroy_ctfd, gen_team

from CTFd.cache import cache
from CTFd.models import db
from CTFd.plugins.l3mon_guard.limits import (
    check_and_record_team_attempt,
    get_campus_networks,
    is_campus_ip,
    track_registration_burst,
    track_shared_address,
)
from CTFd.plugins.l3mon_guard.models import Evidence


def test_campus_network_allowlist_parsing():
    env = {"L3MON_GUARD_CAMPUS_NETS": "192.0.2.0/24, 198.51.100.0/24, 2001:db8::/32"}
    with mock.patch.dict(os.environ, env, clear=False):
        nets = get_campus_networks()
        assert len(nets) == 3

        # Inside campus networks
        assert is_campus_ip("192.0.2.1")
        assert is_campus_ip("192.0.2.254")
        assert is_campus_ip("198.51.100.50")
        assert is_campus_ip("2001:db8::1234")

        # Outside campus networks
        assert not is_campus_ip("203.0.113.1")
        assert not is_campus_ip("192.0.3.1")
        assert not is_campus_ip("invalid_ip")


def test_team_attempt_rate_limit_and_burst_evidence():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        team = gen_team(db, name="Rate Limited Team")
        env = {"L3MON_GUARD_TEAM_LIMIT": "5"}
        with mock.patch.dict(os.environ, env, clear=False):
            cache.cache.clear()

            # First 5 attempts are allowed
            for _ in range(5):
                allowed, retry = check_and_record_team_attempt(team.id)
                assert allowed is True
                assert retry == 0

            # 6th attempt exceeds limit of 5
            allowed, retry = check_and_record_team_attempt(team.id)
            assert allowed is False
            assert retry > 0

            # Evidence was recorded once
            rows = Evidence.query.filter_by(kind="burst", team_id=team.id).all()
            assert len(rows) == 1
            assert "attempts=" in rows[0].detail

            # 7th attempt is still rejected, but burst evidence deduplicated
            allowed, retry = check_and_record_team_attempt(team.id)
            assert allowed is False
            assert len(Evidence.query.filter_by(kind="burst", team_id=team.id).all()) == 1
    destroy_ctfd(app)


def test_shared_address_heuristic_detects_multiple_teams():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        t1 = gen_team(db, name="Team 1", email="team1@example.com")
        t2 = gen_team(db, name="Team 2", email="team2@example.com")
        t3 = gen_team(db, name="Team 3", email="team3@example.com")
        cache.cache.clear()

        non_campus_ip = "203.0.113.42"

        # 1st team on IP -> no evidence
        track_shared_address(t1.id, non_campus_ip)
        assert Evidence.query.filter_by(kind="shared_address").count() == 0

        # 2nd team on IP -> no evidence
        track_shared_address(t2.id, non_campus_ip)
        assert Evidence.query.filter_by(kind="shared_address").count() == 0

        # 3rd distinct team on IP -> triggers shared_address evidence
        track_shared_address(t3.id, non_campus_ip)
        rows = Evidence.query.filter_by(kind="shared_address").all()
        assert len(rows) == 1
        assert "distinct_teams=3" in rows[0].detail

        # 4th activity on same IP is deduplicated
        track_shared_address(t1.id, non_campus_ip)
        assert Evidence.query.filter_by(kind="shared_address").count() == 1
    destroy_ctfd(app)


def test_campus_ip_exempt_from_shared_address_and_registration_burst():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        campus_ip = "192.0.2.100"
        env = {"L3MON_GUARD_CAMPUS_NETS": "192.0.2.0/24"}
        with mock.patch.dict(os.environ, env, clear=False):
            cache.cache.clear()

            # 10 distinct teams from campus IP
            for i in range(10):
                t = gen_team(db, name=f"Campus Team {i}", email=f"campus{i}@example.com")
                track_shared_address(t.id, campus_ip)

            # 50 registrations from campus IP
            for _ in range(50):
                track_registration_burst(campus_ip)

            # Zero evidence created for campus IP!
            assert Evidence.query.filter_by(kind="shared_address").count() == 0
            assert Evidence.query.filter_by(kind="registration_burst").count() == 0
    destroy_ctfd(app)


def test_registration_burst_heuristic():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        non_campus_ip = "203.0.113.88"
        env = {"L3MON_GUARD_REG_BURST_THRESHOLD": "3"}
        with mock.patch.dict(os.environ, env, clear=False):
            cache.cache.clear()

            track_registration_burst(non_campus_ip)
            track_registration_burst(non_campus_ip)
            assert Evidence.query.filter_by(kind="registration_burst").count() == 0

            # 3rd registration hits threshold
            track_registration_burst(non_campus_ip)
            rows = Evidence.query.filter_by(kind="registration_burst").all()
            assert len(rows) == 1
            assert "registrations=3" in rows[0].detail
    destroy_ctfd(app)
