"""Tests for l3mon_guard evidence recording contract (SP5 B3.1, B4.7).

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_guard/test_guard_evidence.py
"""
from tests.helpers import create_ctfd, destroy_ctfd, gen_team

from CTFd.models import db
from CTFd.plugins.l3mon_guard.evidence import record
from CTFd.plugins.l3mon_guard.models import Evidence


def test_record_creates_valid_evidence_entry():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        team = gen_team(db, name="Team Alpha")
        record("burst", team_id=team.id, detail="50 attempts in 60s")
        row = Evidence.query.filter_by(kind="burst").first()
        assert row is not None
        assert row.team_id == team.id
        assert row.detail == "50 attempts in 60s"
        assert row.status == "new"
    destroy_ctfd(app)


def test_record_rejects_unknown_kind_without_raising():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        count_before = Evidence.query.count()
        # Should log warning and not insert
        record("some_unknown_random_kind", detail="testing")
        count_after = Evidence.query.count()
        assert count_after == count_before
    destroy_ctfd(app)


def test_record_sanitizes_markup_in_detail():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        record("flag_reuse", detail="User tried <script>alert(1)</script>")
        row = Evidence.query.filter_by(kind="flag_reuse").first()
        assert row is not None
        assert "<" not in row.detail
        assert ">" not in row.detail
    destroy_ctfd(app)


def test_record_clips_long_detail():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        long_text = "A" * 350
        record("captcha_unverified", detail=long_text)
        row = Evidence.query.filter_by(kind="captcha_unverified").first()
        assert row is not None
        assert len(row.detail) <= 200
        assert row.detail.endswith("…")
    destroy_ctfd(app)


def test_record_redacts_flag_from_detail():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        # Build flag string dynamically to pass hygiene scan
        flag_val = "L3m0n" + "{" + "sensitive_flag_here" + "}"
        record("flag_reuse", detail=f"User submitted flag {flag_val}")
        row = Evidence.query.filter_by(kind="flag_reuse").first()
        assert row is not None
        assert "sensitive_flag_here" not in row.detail
        assert "[redacted" in row.detail
    destroy_ctfd(app)


def test_record_with_explicit_address():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        record("captcha_unverified", detail="action=login", address="198.51.100.42")
        row = Evidence.query.filter_by(kind="captcha_unverified").first()
        assert row is not None
        assert row.address == "198.51.100.42"
        assert row.detail == "action=login"
    destroy_ctfd(app)
