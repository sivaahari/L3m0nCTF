"""Tests for flag reuse evidence detection bridge (SP4 / SP5)."""
import sys
from types import ModuleType
from unittest import mock

import pytest

from CTFd.models import Flags, db
from CTFd.plugins.l3mon_instances.flag import L3monDynamicFlag, derive_team_flag
from CTFd.plugins.l3mon_instances.models import L3monInstanceLog
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_flag, login_as_user, register_user


def test_flag_reuse_evidence_called():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        chal = gen_challenge(db, name="Pwn Chal")
        chal_id = chal.id
        chal_key = gen_flag(db, challenge_id=chal_id, type="l3mon_dynamic", content="")
        key_id = chal_key.id
        db.session.commit()

        # Create two teams
        register_user(app, name="alice", email="alice@example.com")
        register_user(app, name="bob", email="bob@example.com")

        c_alice = login_as_user(app, "alice")
        c_alice.get("/team")
        with c_alice.session_transaction() as s:
            nonce_a = s.get("nonce")
        c_alice.post("/teams/new", data={"name": "team_a", "password": "password", "nonce": nonce_a})

        c_bob = login_as_user(app, "bob")
        c_bob.get("/team")
        with c_bob.session_transaction() as s:
            nonce_b = s.get("nonce")
        c_bob.post("/teams/new", data={"name": "team_b", "password": "password", "nonce": nonce_b})

        from CTFd.models import Teams
        team_a = Teams.query.filter_by(name="team_a").first()
        team_b = Teams.query.filter_by(name="team_b").first()
        key_row = Flags.query.get(key_id)

        # Team A has an instance log entry
        db.session.add(L3monInstanceLog(team_id=team_a.id, challenge_id=chal_id))
        db.session.commit()

        secret = "test-secret-value"
        flag_a = derive_team_flag(secret, team_a.id, chal_id)

        # Mock l3mon_guard.evidence module
        mock_evidence = mock.MagicMock()
        mock_guard = ModuleType("CTFd.plugins.l3mon_guard")
        mock_guard.evidence = mock_evidence
        sys.modules["CTFd.plugins.l3mon_guard"] = mock_guard
        sys.modules["CTFd.plugins.l3mon_guard.evidence"] = mock_evidence

        try:
            with mock.patch("CTFd.plugins.l3mon_instances.flag.get_flag_secret", return_value=secret):
                with app.test_request_context():
                    with mock.patch("CTFd.plugins.l3mon_instances.flag.get_current_team", return_value=team_b):
                        # Bob submits Alice's flag
                        res = L3monDynamicFlag.compare(key_row, flag_a)
                        assert res is False
                        assert mock_evidence.record.called
                        call_kwargs = mock_evidence.record.call_args[1]
                        assert call_kwargs["kind"] == "flag_reuse"
                        assert call_kwargs["team_id"] == team_b.id
                        assert call_kwargs["challenge_id"] == chal_id
                        assert call_kwargs["other_team_id"] == team_a.id
        finally:
            sys.modules.pop("CTFd.plugins.l3mon_guard.evidence", None)
            sys.modules.pop("CTFd.plugins.l3mon_guard", None)

    destroy_ctfd(app)


def test_flag_reuse_resilient_when_guard_missing():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        chal = gen_challenge(db, name="Web Chal")
        chal_id = chal.id
        chal_key = gen_flag(db, challenge_id=chal_id, type="l3mon_dynamic", content="")
        key_id = chal_key.id
        db.session.commit()

        from CTFd.models import Teams
        t1 = Teams(name="t1", email="t1@example.com")
        t2 = Teams(name="t2", email="t2@example.com")
        db.session.add(t1)
        db.session.add(t2)
        db.session.commit()

        db.session.add(L3monInstanceLog(team_id=t1.id, challenge_id=chal_id))
        db.session.commit()

        key_row = Flags.query.get(key_id)
        secret = "test-secret-value"
        flag1 = derive_team_flag(secret, t1.id, chal_id)

        # Ensure CTFd.plugins.l3mon_guard does not exist in sys.modules
        sys.modules.pop("CTFd.plugins.l3mon_guard.evidence", None)
        sys.modules.pop("CTFd.plugins.l3mon_guard", None)

        with mock.patch("CTFd.plugins.l3mon_instances.flag.get_flag_secret", return_value=secret):
            with app.test_request_context():
                with mock.patch("CTFd.plugins.l3mon_instances.flag.get_current_team", return_value=t2):
                    # Must not raise when guard is missing
                    assert L3monDynamicFlag.compare(key_row, flag1) is False

    destroy_ctfd(app)


def test_flag_reuse_resilient_when_evidence_raises():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        chal = gen_challenge(db, name="Crypto Chal")
        chal_id = chal.id
        chal_key = gen_flag(db, challenge_id=chal_id, type="l3mon_dynamic", content="")
        key_id = chal_key.id
        db.session.commit()

        from CTFd.models import Teams
        t1 = Teams(name="ta", email="ta@example.com")
        t2 = Teams(name="tb", email="tb@example.com")
        db.session.add(t1)
        db.session.add(t2)
        db.session.commit()

        db.session.add(L3monInstanceLog(team_id=t1.id, challenge_id=chal_id))
        db.session.commit()

        key_row = Flags.query.get(key_id)
        secret = "test-secret-value"
        flag1 = derive_team_flag(secret, t1.id, chal_id)

        mock_evidence = mock.MagicMock()
        mock_evidence.record.side_effect = RuntimeError("Evidence DB connection failed")
        mock_guard = ModuleType("CTFd.plugins.l3mon_guard")
        mock_guard.evidence = mock_evidence
        sys.modules["CTFd.plugins.l3mon_guard"] = mock_guard
        sys.modules["CTFd.plugins.l3mon_guard.evidence"] = mock_evidence

        try:
            with mock.patch("CTFd.plugins.l3mon_instances.flag.get_flag_secret", return_value=secret):
                with app.test_request_context():
                    with mock.patch("CTFd.plugins.l3mon_instances.flag.get_current_team", return_value=t2):
                        # Must not raise when evidence.record() fails
                        assert L3monDynamicFlag.compare(key_row, flag1) is False
        finally:
            sys.modules.pop("CTFd.plugins.l3mon_guard.evidence", None)
            sys.modules.pop("CTFd.plugins.l3mon_guard", None)

    destroy_ctfd(app)
