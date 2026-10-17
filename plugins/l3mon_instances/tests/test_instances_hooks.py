"""Tests for board hooks and release pull-back integration in l3mon_instances."""
import datetime
from unittest import mock

import pytest
from flask import g
from sqlalchemy import event

from CTFd.models import Challenges, Teams, db
from CTFd.plugins.l3mon_core.clock import now
from CTFd.plugins.l3mon_instances.hooks import instance_summary_for, on_pull_back
from CTFd.plugins.l3mon_instances.models import L3monInstance
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge


def test_instance_summary_single_query_budget():
    """Verify that instance_summary_for runs at most 1 SQL query per request regardless of number of programmes."""
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        team = Teams(name="team_hook", email="team_hook@example.com")
        db.session.add(team)
        db.session.commit()

        # Create 10 challenges
        now_dt = datetime.datetime.utcfromtimestamp(now())
        expires = now_dt + datetime.timedelta(minutes=45)
        for i in range(1, 11):
            chal = gen_challenge(db, name=f"Chal {i}")
            inst = L3monInstance(
                instance_id=f"inst{i:028d}",
                team_id=team.id,
                challenge_id=chal.id,
                slug=f"chal-{i}",
                protocol="http",
                state="running",
                host_name="default",
                created_at=now_dt,
                expires_at=expires,
                active_key=f"{team.id}:{chal.id}",
            )
            db.session.add(inst)
        db.session.commit()

        # Inside request context, query statements should be at most 1 across all 10 calls
        queries = []

        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        event.listen(db.engine, "before_cursor_execute", before_cursor_execute)
        try:
            with app.test_request_context():
                # Call for all 10 challenges
                for i in range(1, 11):
                    res = instance_summary_for(team, i)
                    assert res is not None
                    assert res["state"] == "running"
                    assert "expires_in" in res
                    assert "ends" in res

                # Call for non-existent challenge
                assert instance_summary_for(team, 9999) is None

                # Query count for instances must be exactly 1!
                inst_queries = [q for q in queries if "l3mon_instance" in q]
                assert len(inst_queries) == 1, f"Expected 1 instance query, got {len(inst_queries)}: {inst_queries}"
        finally:
            event.remove(db.engine, "before_cursor_execute", before_cursor_execute)

    destroy_ctfd(app)


def test_instance_summary_none_for_no_team():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        assert instance_summary_for(None, 42) is None
    destroy_ctfd(app)


def test_on_pull_back_marks_instances_stopping():
    app = create_ctfd(user_mode="teams", enable_plugins=True)
    with app.app_context():
        team = Teams(name="team_pb", email="team_pb@example.com")
        db.session.add(team)
        db.session.commit()

        chal1 = gen_challenge(db, name="PB Chal 1")
        chal2 = gen_challenge(db, name="PB Chal 2")
        now_dt = datetime.datetime.utcfromtimestamp(now())
        expires = now_dt + datetime.timedelta(minutes=45)

        inst1 = L3monInstance(
            instance_id="instpb01000000000000000000000000",
            team_id=team.id,
            challenge_id=chal1.id,
            slug="pb-chal-1",
            protocol="http",
            state="running",
            host_name="default",
            created_at=now_dt,
            expires_at=expires,
            active_key=f"{team.id}:{chal1.id}",
        )
        inst2 = L3monInstance(
            instance_id="instpb02000000000000000000000000",
            team_id=team.id,
            challenge_id=chal2.id,
            slug="pb-chal-2",
            protocol="http",
            state="running",
            host_name="default",
            created_at=now_dt,
            expires_at=expires,
            active_key=f"{team.id}:{chal2.id}",
        )
        db.session.add_all([inst1, inst2])
        db.session.commit()

        # Pull back chal1
        on_pull_back([chal1.id])

        # Verify inst1 is marked stopping and active_key is cleared
        db.session.expire_all()
        refreshed1 = L3monInstance.query.filter_by(instance_id="instpb01000000000000000000000000").one()
        assert refreshed1.state == "stopping"
        assert refreshed1.active_key is None
        assert refreshed1.stopped_at is not None

        # chal2 should remain active
        refreshed2 = L3monInstance.query.filter_by(instance_id="instpb02000000000000000000000000").one()
        assert refreshed2.state == "running"
        assert refreshed2.active_key == f"{team.id}:{chal2.id}"

        # Calling on_pull_back with empty list does nothing and does not fail
        on_pull_back([])

    destroy_ctfd(app)
