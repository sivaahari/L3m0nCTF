"""What is on air: the one rule every endpoint asks.

A programme is on air when its channel is released, it is released, and (for a player) CTFd shows the challenge; a `scheduled`
entry whose second has come counts as released; once the broadcast has ended every programme on the plan can be read. While any
channel exists a challenge that is in no channel is not on air (default deny); with none, nothing differs from stock CTFd.

Run through tools/run-ctfd-tests.sh:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import calendar
import datetime

import pytest

from CTFd.models import Challenges, db
from CTFd.plugins.l3mon_core.airing import entry_on_air, is_on_air, on_air_ids, release_active
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.plugins.l3mon_core.visibility import is_visible, visible_challenge_ids
from CTFd.utils import set_config
from tests.helpers import create_ctfd, destroy_ctfd, gen_challenge, gen_solve, gen_user

T0 = calendar.timegm(datetime.datetime(2026, 11, 28, 3, 30, 0).timetuple())  # 09:00 IST on the day
AT = datetime.datetime(2026, 11, 28, 5, 0, 0)  # naive UTC, like CTFd's own times
AT_EPOCH = calendar.timegm(AT.timetuple())


@pytest.mark.parametrize(
    "state,at,t,expected",
    [
        ("released", None, T0, True),
        ("released", AT, T0, True),  # a time left over on a released entry means nothing
        ("withheld", None, T0, False),
        ("withheld", AT, AT_EPOCH + 3600, False),  # a withheld entry never opens by itself, whatever time it holds
        ("scheduled", AT, AT_EPOCH - 1, False),
        ("scheduled", AT, AT_EPOCH, True),  # at the second
        ("scheduled", AT, AT_EPOCH + 1, True),
        ("scheduled", None, T0, False),  # a schedule without a time is withheld
        ("odd", None, T0, False),  # a word nobody defined never counts
        (None, None, T0, False),
    ],
)
def test_one_entry_is_on_air_only_by_its_own_state_and_time(state, at, t, expected):
    assert entry_on_air(state, at, t) is expected


def force_state(challenge_id, state):
    """CTFd's own state set underneath the plugin that derives it (a bulk update does not pass the hand guard of l3mon_release).
    This is the state a missed reconcile would leave, which the rule in airing.py must still cope with."""
    Challenges.query.filter_by(id=challenge_id).update({"state": state})
    db.session.commit()


def plan(*rows, channel_state="released", channel_at=None):
    """rows: (name, programme state, programme at, challenge state). One channel."""
    made = [(name, state, at, gen_challenge(db, name=name, state="hidden")) for name, state, at, _ in rows]  # before any channel exists
    ch = Channel(slug="street", name="Street", release_state=channel_state, release_at=channel_at)
    db.session.add(ch)
    db.session.commit()
    ids = {}
    for i, ((name, state, at, chal), (_, _, _, chal_state)) in enumerate(zip(made, rows)):
        force_state(chal.id, chal_state)
        db.session.add(Programme(challenge_id=chal.id, channel_id=ch.id, cell=i, number=i + 1, slug=name, release_state=state, release_at=at))
        ids[name] = chal.id
    db.session.commit()
    return ch, ids


def test_without_any_channel_release_control_is_not_in_use_and_stock_ctfd_decides():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        shown, held = gen_challenge(db, name="shown"), gen_challenge(db, name="held", state="hidden")
        assert release_active() is False
        assert on_air_ids(T0) == set()
        assert visible_challenge_ids() == {shown.id}, "exactly what CTFd's own state says"
        assert is_visible(shown) and not is_visible(held)
    destroy_ctfd(app)


def test_a_programme_needs_its_channel_its_own_release_and_ctfds_visible_state():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        _, ids = plan(
            ("on", "released", None, "visible"),
            ("off", "withheld", None, "visible"),
            ("later", "scheduled", AT, "visible"),
            ("hidden-by-ctfd", "released", None, "hidden"),
        )
        assert release_active() is True
        assert on_air_ids(T0) == {ids["on"], ids["hidden-by-ctfd"]}, "the plan's side: released, whatever CTFd's state says"
        assert visible_challenge_ids(t=T0) == {ids["on"]}, "both sides must agree for a player"
        assert on_air_ids(AT_EPOCH) == {ids["on"], ids["later"], ids["hidden-by-ctfd"]}, "the scheduled one comes on at its second"
        assert visible_challenge_ids(t=AT_EPOCH) == {ids["on"], ids["later"]}
        assert visible_challenge_ids(admin=True, t=T0) == set(ids.values()), "the crew sees everything"
    destroy_ctfd(app)


def test_a_withheld_channel_hides_all_of_its_programmes_whatever_they_say():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        _, ids = plan(("a", "released", None, "visible"), ("b", "scheduled", AT, "visible"), channel_state="withheld")
        assert on_air_ids(AT_EPOCH + 60) == set()
        assert visible_challenge_ids(t=AT_EPOCH + 60) == set()
    destroy_ctfd(app)


def test_a_scheduled_channel_opens_its_released_programmes_at_its_second_and_not_before():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        _, ids = plan(("a", "released", None, "visible"), ("b", "withheld", None, "visible"), channel_state="scheduled", channel_at=AT)
        assert on_air_ids(AT_EPOCH - 1) == set()
        assert on_air_ids(AT_EPOCH) == {ids["a"]}, "the channel opens; only what was released inside it comes with it"
    destroy_ctfd(app)


def test_a_challenge_in_no_channel_is_not_on_air_while_any_channel_exists():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        _, ids = plan(("on", "released", None, "visible"))
        loose = gen_challenge(db, name="loose", state="hidden")
        force_state(loose.id, "visible")  # visible in CTFd, but nobody put it on a channel
        assert not is_on_air(loose.id, T0)
        assert visible_challenge_ids(t=T0) == {ids["on"]}
        assert not is_visible(loose)
        assert is_visible(loose, admin=True), "the crew can still open it to put it on the plan"
    destroy_ctfd(app)


def test_after_the_end_every_programme_on_the_plan_can_be_read_again_and_loose_challenges_still_cannot():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        _, ids = plan(("on", "released", None, "visible"), ("off", "withheld", None, "visible"), ("never", "withheld", None, "hidden"))
        loose = gen_challenge(db, name="loose", state="hidden")
        force_state(loose.id, "visible")
        end = T0 + 24 * 3600
        set_config("start", T0)
        set_config("end", end)
        assert on_air_ids(end - 1) == {ids["on"]}
        assert on_air_ids(end) == set(ids.values()), "at the end second, as CTFd says the broadcast has ended"
        assert loose.id not in on_air_ids(end + 10)
        assert visible_challenge_ids(t=end + 10) == {ids["on"], ids["off"]}, "CTFd's own hidden state still hides what CTFd hides"
    destroy_ctfd(app)


def test_prerequisites_still_apply_on_top_of_the_plan():
    app = create_ctfd(enable_plugins=True, user_mode="users")
    with app.app_context():
        a = gen_challenge(db, name="a")
        b = gen_challenge(db, name="b", requirements={"prerequisites": [a.id]})
        ch = Channel(slug="street", name="Street", release_state="released")
        db.session.add(ch)
        db.session.commit()
        for i, c in enumerate((a, b)):
            db.session.add(Programme(challenge_id=c.id, channel_id=ch.id, cell=i, number=i + 1, slug=c.name, release_state="released"))
        db.session.commit()
        assert visible_challenge_ids(t=T0) == {a.id}
        assert visible_challenge_ids(solved_ids={a.id}, t=T0) == {a.id, b.id}
        assert is_visible(b, solved_ids={a.id}) and not is_visible(b)
    destroy_ctfd(app)


def test_the_single_challenge_check_and_the_set_agree_in_every_state():
    app = create_ctfd(enable_plugins=True)
    with app.app_context():
        _, ids = plan(("on", "released", None, "visible"), ("off", "withheld", None, "visible"), ("later", "scheduled", AT, "visible"))
        from CTFd.models import Challenges

        for t in (T0, AT_EPOCH):
            wanted = visible_challenge_ids(t=t)
            for chal in Challenges.query.all():
                assert is_visible(chal, t=t) == (chal.id in wanted), (chal.name, t)
    destroy_ctfd(app)


def test_nothing_in_the_answer_depends_on_who_is_asking_except_the_crew():
    app = create_ctfd(enable_plugins=True, user_mode="users")
    with app.app_context():
        _, ids = plan(("on", "released", None, "visible"), ("off", "withheld", None, "visible"))
        someone = gen_user(db, name="p", email="p@e.com")
        gen_solve(db, user_id=someone.id, challenge_id=ids["off"])  # even a solver of a withheld programme does not get it back
        assert visible_challenge_ids(solved_ids={ids["off"]}, t=T0) == {ids["on"]}
    destroy_ctfd(app)
