"""Reconcile: CTFd's own challenge state follows the crew's plan, drops are announced, pull-backs are reported, and nobody can
reveal what is not on air by hand.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_release
"""
import calendar
import datetime
import logging
from unittest import mock

import pytest
from freezegun import freeze_time

from CTFd.models import Challenges, Notifications, Submissions, db
from CTFd.plugins.l3mon_core.models import Audit, Channel, Programme
from CTFd.plugins.l3mon_core.visibility import visible_challenge_ids
from CTFd.plugins.l3mon_release.reconcile import clear_pull_back_handlers, desired_states, reconcile, register_pull_back_handler
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_flag, login_as_user, register_user


def at(hour, minute=0, day=28):
    """A UTC moment on the day of the round (the start is 03:30 UTC)."""
    return datetime.datetime(2026, 11, day, hour, minute, 0)


def epoch(moment):
    return calendar.timegm(moment.timetuple())


T_BEFORE = epoch(at(3, 0))
T_START = epoch(at(3, 30))
T_LATER = epoch(at(5, 0))
T_END = T_START + 24 * 3600


@pytest.fixture(autouse=True)
def no_leftover_handlers():
    clear_pull_back_handlers()
    yield
    clear_pull_back_handlers()


def channel(slug, position, name=None, state="released", when=None):
    row = Channel(slug=slug, name=name or slug.title(), position=position, release_state=state, release_at=when)
    db.session.add(row)
    db.session.commit()
    return row


def programme(ch, name, state="released", when=None, chal_state="hidden"):
    chal = gen_challenge(db, name=name, state=chal_state)
    n = Programme.query.count()
    db.session.add(Programme(challenge_id=chal.id, channel_id=ch.id, cell=n, number=n + 1, slug=name.lower(), release_state=state, release_at=when))
    db.session.commit()
    return chal.id


def states():
    return {c.name: c.state for c in Challenges.query.order_by(Challenges.id)}


def clock(t):
    """Freeze the clock at epoch second `t`, so CTFd's own checks (is the contest on?) agree with the second the test reconciles at."""
    return freeze_time(datetime.datetime.utcfromtimestamp(t))


class Lines(logging.Handler):
    """CTFd's migrations reconfigure the root logger, which removes pytest's own capture; this collects what the l3mon logger says."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.text = []

    def emit(self, record):
        self.text.append(self.format(record))


def run_round(app_kwargs=None):
    app = create_ctfd(enable_plugins=True, **(app_kwargs or {}))
    return app


def started():
    set_config("start", T_START)
    set_config("end", T_END)


def test_without_a_plan_nothing_is_touched_and_nothing_is_announced():
    app = run_round()
    with app.app_context():
        gen_challenge(db, name="shown", state="visible")
        gen_challenge(db, name="held", state="hidden")
        gen_challenge(db, name="locked", state="locked")
        assert desired_states(T_LATER) == {}
        result = reconcile(T_LATER)
        assert (result.shown, result.hidden) == ([], [])
        assert states() == {"shown": "visible", "held": "hidden", "locked": "locked"}
        assert Notifications.query.count() == 0 and Audit.query.count() == 0
    destroy_ctfd(app)


def test_every_challenge_follows_the_plan_released_withheld_scheduled_and_not_on_it():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "on", "released")
        programme(ch, "off", "withheld", chal_state="visible")  # CTFd shows it; the plan says it is withheld
        programme(ch, "later", "scheduled", at(5, 0))
        gen_challenge(db, name="loose", state="visible")  # on no channel
        gen_challenge(db, name="locked-and-loose", state="locked")
        reconcile(T_START + 60)
        assert states() == {"on": "visible", "off": "hidden", "later": "hidden", "loose": "hidden", "locked-and-loose": "hidden"}
        reconcile(T_LATER)  # the scheduled second
        assert states()["later"] == "visible" and states()["off"] == "hidden"
    destroy_ctfd(app)


def test_a_withheld_or_scheduled_channel_takes_its_released_programmes_down_and_back_up():
    app = run_round()
    with app.app_context():
        started()
        a = channel("a", 1)
        b = channel("b", 2, state="scheduled", when=at(5, 0))
        programme(a, "pa", "released")
        programme(b, "pb", "released")
        reconcile(T_START + 60)
        assert states() == {"pa": "visible", "pb": "hidden"}
        reconcile(T_LATER)
        assert states() == {"pa": "visible", "pb": "visible"}
        a.release_state = "withheld"
        db.session.commit()
        reconcile(T_LATER + 5)
        assert states() == {"pa": "hidden", "pb": "visible"}
    destroy_ctfd(app)


def test_reconciling_again_changes_and_announces_nothing_more():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 3, name="Gadget Galaxy")
        programme(ch, "one", "released")
        first = reconcile(T_START + 60)
        assert len(first.shown) == 1 and Notifications.query.count() == 1
        audit_lines = Audit.query.count()
        second = reconcile(T_START + 61)
        assert (second.shown, second.hidden) == ([], [])
        assert Notifications.query.count() == 1 and Audit.query.count() == audit_lines
    destroy_ctfd(app)


def test_the_stock_challenge_list_shows_exactly_what_the_plan_allows_at_every_moment():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "on", "released")
        programme(ch, "off", "withheld")
        programme(ch, "later", "scheduled", at(5, 0))
        gen_challenge(db, name="loose", state="visible")
        with clock(T_START + 30):  # sign in inside the frozen day: a session made on the real day would have expired by then
            register_user(app)
            player = login_as_user(app)
        for t in (T_START + 60, T_LATER):
            with clock(t):
                reconcile(t)
                listed = {c["name"] for c in player.get("/api/v1/challenges").get_json()["data"]}
                ours = {Challenges.query.get(i).name for i in visible_challenge_ids(t=t)}
                assert listed == ours, (t, listed, ours)
        with clock(T_START + 60):
            reconcile(T_START + 60)
            assert {c["name"] for c in player.get("/api/v1/challenges").get_json()["data"]} == {"on"}
            # a withheld one is a 404 by id, as for an id that never existed
            off_id = Challenges.query.filter_by(name="off").first().id
            assert player.get(f"/api/v1/challenges/{off_id}").status_code == 404
            assert player.get("/api/v1/challenges/99999").status_code == 404
        with clock(T_END - 5):
            reconcile(T_END - 5)
        # CTFd itself closes the challenge routes for players once the contest has ended (unless it is told players may look
        # after the end); what the plan does at the end is shown by the states, here and in test_reconcile above
        reconcile(T_END)
        assert states() == {"on": "visible", "off": "visible", "later": "visible", "loose": "hidden"}
    destroy_ctfd(app)


def test_the_end_brings_everything_on_the_plan_back_and_a_later_end_takes_it_away_again():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "on", "released")
        programme(ch, "never", "withheld")
        reconcile(T_END - 1)
        assert states() == {"on": "visible", "never": "hidden"}
        reconcile(T_END)
        assert states() == {"on": "visible", "never": "visible"}
        set_config("end", T_END + 7200)  # the crew extends the broadcast
        reconcile(T_END + 5)
        assert states() == {"on": "visible", "never": "hidden"}
    destroy_ctfd(app)


# ---- announcements -------------------------------------------------------------------------------------------------------

def test_a_drop_is_announced_once_with_one_line_per_channel_in_channel_order_and_counts_only():
    app = run_round()
    with app.app_context():
        started()
        late = channel("late", 5, name="Chase Club")
        early = channel("early", 3, name="Gadget Galaxy")
        for n in ("Wrestler Padding", "Midnight Mango"):
            programme(late, n, "scheduled", at(5, 0))
        programme(early, "Secret Sauce", "scheduled", at(5, 0))
        reconcile(T_START + 60)
        assert Notifications.query.count() == 0, "nothing is due yet"
        events = mock.Mock()
        app.events_manager.publish = events
        reconcile(T_LATER)
        note = Notifications.query.one()
        assert note.title == "New on air"
        assert note.content == "CH 3 · Gadget Galaxy has 1 new programme. CH 5 · Chase Club has 2 new programmes."
        for secret in ("Wrestler", "Mango", "Secret Sauce", "wrestler padding"):
            assert secret not in note.content and secret not in note.title
        assert note.team_id is None and note.user_id is None, "it is public"
        assert events.call_count == 1 and events.call_args.kwargs["type"] == "notification"
        pushed = events.call_args.kwargs["data"]
        assert pushed["title"] == "New on air" and pushed["sound"] is True and "Gadget Galaxy" in pushed["content"]
        reconcile(T_LATER + 30)
        assert Notifications.query.count() == 1, "once"
    destroy_ctfd(app)


def test_nothing_is_announced_before_the_start_or_after_the_end_but_a_pause_does_not_hold_a_drop_back():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "early", "released")
        reconcile(T_BEFORE)
        assert states()["early"] == "visible" and Notifications.query.count() == 0, "before the start: put on air quietly"
        ch2 = channel("late", 2)
        programme(ch2, "later", "scheduled", at(5, 0))
        set_config("paused", True)
        reconcile(T_LATER)
        assert states()["later"] == "visible" and Notifications.query.count() == 1, "the clock goes on during a pause"
        ch3 = channel("lastly", 3)
        programme(ch3, "reveal", "withheld")
        reconcile(T_END + 5)
        assert states()["reveal"] == "visible" and Notifications.query.count() == 1, "after the end: everything readable, no announcement"
    destroy_ctfd(app)


def test_a_programme_pulled_back_and_released_again_is_announced_again():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1, name="Street")
        cid = programme(ch, "fixed", "released")
        reconcile(T_START + 60)
        p = Programme.query.filter_by(challenge_id=cid).one()
        p.release_state = "withheld"
        db.session.commit()
        reconcile(T_START + 120)
        assert states()["fixed"] == "hidden"
        p.release_state = "released"
        db.session.commit()
        reconcile(T_START + 180)
        assert [n.content for n in Notifications.query.order_by(Notifications.id)] == ["CH 1 · Street has 1 new programme."] * 2
    destroy_ctfd(app)


# ---- pull-backs ----------------------------------------------------------------------------------------------------------

def test_pull_back_handlers_hear_exactly_the_programmes_that_were_on_air_and_no_longer_are():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1)
        on = programme(ch, "on", "released")
        also_on = programme(ch, "also", "released")
        never = programme(ch, "never", "withheld")
        reconcile(T_START + 60)
        heard = []
        register_pull_back_handler(lambda ids: heard.append(sorted(ids)))
        Programme.query.filter_by(challenge_id=on).update({"release_state": "withheld"})
        db.session.commit()
        reconcile(T_START + 120)
        assert heard == [[on]]
        reconcile(T_START + 121)
        assert heard == [[on]], "nothing changed, nobody is told again"
        ch.release_state = "withheld"
        db.session.commit()
        reconcile(T_START + 180)
        assert heard == [[on], [also_on]], "a pulled-back channel takes its programmes with it; the one that was never on air is not reported"
        assert never not in sum(heard, [])
    destroy_ctfd(app)


def test_a_failing_handler_is_logged_and_never_stops_the_reconcile_or_the_other_handlers():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1)
        cid = programme(ch, "one", "released")
        reconcile(T_START + 60)
        heard = []

        def broken(ids):
            raise RuntimeError("the instance host is down")

        register_pull_back_handler(broken)
        register_pull_back_handler(lambda ids: heard.append(ids))
        Programme.query.update({"release_state": "withheld"})
        db.session.commit()
        lines = Lines()
        logging.getLogger("l3mon").addHandler(lines)
        try:
            result = reconcile(T_START + 120)
        finally:
            logging.getLogger("l3mon").removeHandler(lines)
        assert result.hidden == [cid] and heard == [[cid]] and states()["one"] == "hidden"
        assert "the instance host is down" in "\n".join(lines.text)
    destroy_ctfd(app)


# ---- a flag for a withheld programme -------------------------------------------------------------------------------------

def test_a_correct_flag_for_a_withheld_programme_is_refused_and_records_nothing():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1)
        open_id = programme(ch, "open", "released")
        held = programme(ch, "held", "withheld")
        gen_flag(db, open_id, content="the-open-flag")
        gen_flag(db, held, content="the-held-flag")
        reconcile(T_START + 60)
        with clock(T_START + 30):  # sign in on the frozen day, or the session would have expired and every answer would be a refusal
            register_user(app)
            player = login_as_user(app)
        with clock(T_START + 60):
            control = player.post("/api/v1/challenges/attempt", json={"challenge_id": open_id, "submission": "the-open-flag"})
            assert control.status_code == 200 and control.get_json()["data"]["status"] == "correct", "the control: a programme on air takes its flag"
            assert Submissions.query.count() == 1
            r = player.post("/api/v1/challenges/attempt", json={"challenge_id": held, "submission": "the-held-flag"})
            gone = player.post("/api/v1/challenges/attempt", json={"challenge_id": 99999, "submission": "the-held-flag"})
        assert r.status_code == gone.status_code and r.status_code in (403, 404), (r.status_code, gone.status_code)
        assert r.get_json() == gone.get_json(), "the same answer as for an id that never existed"
        assert Submissions.query.count() == 1, "nothing was recorded for the withheld programme"
    destroy_ctfd(app)


# ---- nobody can reveal what is not on air by hand ------------------------------------------------------------------------

def test_showing_a_programme_that_is_not_on_air_by_hand_ends_hidden_and_leaves_an_audit_line():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1)
        cid = programme(ch, "held", "withheld")
        reconcile(T_START + 60)
        chal = Challenges.query.get(cid)
        chal.state = "visible"  # what CTFd's editor would do
        db.session.commit()
        assert Challenges.query.get(cid).state == "hidden"
        line = Audit.query.filter_by(action="release.refuse").one()
        assert line.target == f"challenge:{cid}" and "not on air" in line.detail
    destroy_ctfd(app)


def test_a_brand_new_challenge_that_is_on_no_channel_starts_hidden_once_a_plan_exists():
    app = run_round()
    with app.app_context():
        started()
        channel("street", 1)
        made = gen_challenge(db, name="made-by-hand", state="visible")
        assert Challenges.query.get(made.id).state == "hidden"
    destroy_ctfd(app)


def test_hiding_a_programme_in_ctfds_editor_withholds_it_in_the_plan_so_it_stays_hidden():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1)
        cid = programme(ch, "on", "released")
        other = programme(ch, "other", "released")
        reconcile(T_START + 60)
        assert states() == {"on": "visible", "other": "visible"}
        Challenges.query.get(cid).state = "hidden"  # the crew hides it in CTFd's own editor in a hurry
        db.session.commit()
        p = Programme.query.filter_by(challenge_id=cid).one()
        assert (p.release_state, p.release_at) == ("withheld", None)
        assert Audit.query.filter_by(action="release.withhold").one().target == f"programme:on"
        reconcile(T_START + 120)
        assert states() == {"on": "hidden", "other": "visible"}, "the next reconcile does not put it back"
        assert Programme.query.filter_by(challenge_id=other).one().release_state == "released"
    destroy_ctfd(app)


def test_editing_other_fields_of_a_challenge_changes_nothing_in_the_plan():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1)
        cid = programme(ch, "on", "released")
        reconcile(T_START + 60)
        before = Audit.query.count()
        chal = Challenges.query.get(cid)
        chal.description = "better words"
        chal.value = 123
        db.session.commit()
        assert Challenges.query.get(cid).state == "visible" and Programme.query.one().release_state == "released"
        assert Audit.query.count() == before
    destroy_ctfd(app)


def test_the_challenge_editor_is_told_plainly_when_it_tries_to_reveal_what_is_not_on_air():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1)
        held = programme(ch, "held", "withheld")
        on = programme(ch, "on", "released")
        reconcile(T_START + 60)
        admin = login_as_user(app, "admin")
        r = admin.patch(f"/api/v1/challenges/{held}", json={"state": "visible"})
        assert r.status_code == 400 and r.get_json()["success"] is False
        assert "release" in r.get_json()["errors"]["state"][0].lower()
        assert Challenges.query.get(held).state == "hidden"
        ok = admin.patch(f"/api/v1/challenges/{on}", json={"description": "still editable"})
        assert ok.status_code == 200, "ordinary edits are untouched"
    destroy_ctfd(app)


def test_a_channel_name_with_markup_in_the_database_cannot_make_the_announcement_render_markup():
    """The plan refuses such names, but a row can be put there by other means; the notice that every player reads must still be plain."""
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1, name="Snack <b>*Sq*</b> [win](http://evil.example/x) {ctf_name} `c` _u_ #h")
        programme(ch, "one", "released")
        reconcile(T_START + 60)
        content = Notifications.query.one().content
        for forbidden in ("<", ">", "[", "]", "{", "}", "*", "`", "_", "#", "://"):  # "http:evil.example" without the slashes is no link
            assert forbidden not in content, (forbidden, content)
        assert content.startswith("CH 1 · Snack ") and content.endswith(" has 1 new programme.")
    destroy_ctfd(app)


def test_a_reconcile_records_who_dropped_what_for_the_crew_and_only_there():
    app = run_round()
    with app.app_context():
        started()
        ch = channel("street", 1)
        programme(ch, "alpha", "scheduled", at(5, 0))
        programme(ch, "beta", "scheduled", at(5, 0))
        reconcile(T_LATER)
        line = Audit.query.filter_by(action="release.drop").one()
        assert line.actor_name == "system" and "alpha" in line.detail and "beta" in line.detail
    destroy_ctfd(app)
