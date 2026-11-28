"""The qualifier certificates on the platform (plugins/l3mon_certificates): the import, the crew's release, the one notification and the card.

What is pinned:
- the import refuses a bad list as a whole, writes nothing without --apply, and with it makes the table exactly the list (one audit line);
- nothing shows until a crew account releases; release now, at a set time, or hide again; every change is an audit line;
- a player sees ONLY their own code, on their own studio page, and only while released; a player without a code sees no card;
- the notification "Certificates are ready" is sent once, ever, as a [TOAST] line, by the release or by the first request after the set time;
- only the crew reaches the API and the page.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_certificates
"""
import io
import json
import os
import sys
from types import SimpleNamespace

for _candidate in ("/l3mon_tests/l3mon_board", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "l3mon_board", "tests")):
    if os.path.isdir(_candidate) and os.path.abspath(_candidate) not in sys.path:
        sys.path.insert(0, os.path.abspath(_candidate))

import pytest  # noqa: E402

from board_world import T_LIVE, clock, join_client, started, team_client  # noqa: E402
from CTFd.models import Notifications, Users, db  # noqa: E402
from CTFd.plugins.l3mon_certificates import loader, release  # noqa: E402
from CTFd.plugins.l3mon_certificates.models import Certificate  # noqa: E402
from CTFd.plugins.l3mon_core.models import Audit  # noqa: E402
from tests.helpers import create_ctfd, destroy_ctfd, login_as_user  # noqa: E402

API = "/api/v1/l3mon/admin/certificates"
BASE = "https://verify.l3m0nctf.xyz"
CODE_A, CODE_B, CODE_C = "L3M0N26-PAR-BCDFG", "L3M0N26-PAR-HJKLM", "L3M0N26-PAR-NPQRS"


def make_app():
    import datetime

    app = create_ctfd(user_mode="teams", enable_plugins=True, ctf_theme="l3mon")
    app.permanent_session_lifetime = datetime.timedelta(days=3650)
    return app


@pytest.fixture()
def play():
    app = make_app()
    with app.app_context():
        started()
        with clock(T_LIVE):
            admin = login_as_user(app, "admin")
            alice = team_client(app, "alice", "studio-a")
            abe = join_client(app, "abe", "studio-a")
            bob = team_client(app, "bob", "studio-b")
            ids = {name: Users.query.filter_by(name=name).one().id for name in ("alice", "abe", "bob")}
            yield SimpleNamespace(app=app, admin=admin, alice=alice, abe=abe, bob=bob, ids=ids)
    destroy_ctfd(app)


def the_list(*entries, base=BASE):
    return {"format": "l3mon-certificates-1", "base_url": base, "codes": [{"user_id": u, "username": n, "code": c} for u, n, c in entries]}


def load_ab(play, apply=True):
    return loader.load(the_list((play.ids["alice"], "alice", CODE_A), (play.ids["abe"], "abe", CODE_B)), apply=apply)


def put(client, at):
    return client.put(API + "/release", json={"at": at})


def page(client):
    return client.get("/team").get_data(as_text=True)


def notes():
    return Notifications.query.filter(Notifications.title.like("%Certificates are ready%")).all()


# ---- the import --------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("payload, words", [
    ([], "JSON object"),
    ({"format": "x", "base_url": BASE, "codes": []}, "format"),
    ({"format": "l3mon-certificates-1", "base_url": "http://verify.l3m0nctf.xyz", "codes": []}, "base_url"),
    ({"format": "l3mon-certificates-1", "base_url": BASE, "codes": [], "extra": 1}, "unknown keys"),
    ({"format": "l3mon-certificates-1", "base_url": BASE, "codes": [{"user_id": 1, "username": "a", "code": "L3M0N26-ORG-BCDFG"}]}, "not a qualifier code"),
    ({"format": "l3mon-certificates-1", "base_url": BASE, "codes": [{"user_id": True, "username": "a", "code": CODE_A}]}, "positive whole number"),
    ({"format": "l3mon-certificates-1", "base_url": BASE, "codes": [{"user_id": 1, "username": "a", "code": CODE_A, "x": 1}]}, "exactly"),
    ({"format": "l3mon-certificates-1", "base_url": BASE, "codes": [{"user_id": 1, "username": "a", "code": CODE_A}, {"user_id": 1, "username": "b", "code": CODE_B}]}, "twice"),
    ({"format": "l3mon-certificates-1", "base_url": BASE, "codes": [{"user_id": 1, "username": "a", "code": CODE_A}, {"user_id": 2, "username": "b", "code": CODE_A}]}, "twice"),
])
def test_a_bad_list_is_refused_as_a_whole(payload, words):
    with pytest.raises(loader.Refused, match=words):
        loader.parse(payload)


def test_without_apply_nothing_is_written(play):
    report = load_ab(play, apply=False)
    assert (report["codes"], report["new"], report["applied"]) == (2, 2, False)
    assert Certificate.query.count() == 0 and Audit.query.filter_by(action="certificates.import").count() == 0


def test_the_import_makes_the_table_exactly_the_list_and_writes_one_audit_line(play):
    load_ab(play)
    assert {(r.user_id, r.code) for r in Certificate.query.all()} == {(play.ids["alice"], CODE_A), (play.ids["abe"], CODE_B)}
    report = loader.load(the_list((play.ids["alice"], "alice", CODE_C), (play.ids["bob"], "bob", CODE_B)), apply=True)
    assert (report["new"], report["changed"], report["removed"], report["unchanged"]) == (1, 1, 1, 0), "abe's code moved to bob in one import"
    assert {(r.user_id, r.code) for r in Certificate.query.all()} == {(play.ids["alice"], CODE_C), (play.ids["bob"], CODE_B)}
    assert Audit.query.filter_by(action="certificates.import").count() == 2
    assert release.base_url() == BASE


def test_a_missing_account_is_left_out_and_a_renamed_one_keeps_its_code(play):
    report = loader.load(the_list((play.ids["alice"], "alice-before-rename", CODE_A), (99999, "ghost", CODE_B)), apply=True)
    assert report["no_such_player"] == [99999] and report["renamed_since_export"] == [play.ids["alice"]]
    assert [r.user_id for r in Certificate.query.all()] == [play.ids["alice"]]


def test_the_report_and_the_audit_line_name_no_code_and_no_username(play, capsys):
    report = load_ab(play)
    loader._print(report)
    printed = capsys.readouterr().out
    line = Audit.query.filter_by(action="certificates.import").one().detail
    for secret in (CODE_A, CODE_B, "alice", "abe"):
        assert secret not in printed and secret not in line


def test_the_command_refuses_garbage_and_unknown_arguments(play, monkeypatch, capsys):
    assert loader.main(["--force"]) == 2
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"not json")))
    assert loader.main([]) == 1 and "nothing was changed" in capsys.readouterr().err


# ---- the release -------------------------------------------------------------------------------------------------------------------------

def test_nothing_shows_before_the_release(play):
    load_ab(play)
    assert "Your certificate" not in page(play.alice)
    state = play.admin.get(API).get_json()["data"]
    assert (state["codes"], state["released"], state["release_at"], state["announced_at"]) == (2, False, None, None)


def test_releasing_with_no_codes_is_refused(play):
    r = put(play.admin, "now")
    assert r.status_code == 409 and "import" in json.dumps(r.get_json())


def test_release_now_shows_each_player_only_their_own_code_and_sends_one_toast(play):
    load_ab(play)
    r = put(play.admin, "now")
    assert r.status_code == 200 and r.get_json()["data"]["released"] is True
    a, b = page(play.alice), page(play.abe)
    assert CODE_A in a and CODE_B not in a and CODE_B in b and CODE_A not in b, "a player sees only their own code"
    assert f'href="{BASE}/{CODE_A}"' in a and f'src="{release.QR_PATH}"' in a and "data:image" not in a, "the picture is not inside the page"
    assert "Your certificate" not in page(play.bob), "no code, no card"
    assert [n.title for n in notes()] == ["[TOAST] Certificates are ready"]
    put(play.admin, None)
    put(play.admin, "now")
    play.alice.get("/team")
    assert len(notes()) == 1, "the notification is sent once, ever"


def test_the_qr_code_is_of_the_certificates_address(play):
    import re

    from CTFd.plugins.l3mon_certificates.qr import matrix, svg

    load_ab(play)
    put(play.admin, "now")
    r = play.alice.get(release.QR_PATH)
    assert r.status_code == 200 and r.mimetype == "image/svg+xml"
    assert r.headers["Cache-Control"] == "private, no-store" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]
    picture = r.get_data(as_text=True)
    assert picture == svg(f"{BASE}/{CODE_A}", dark="#000000", light="#ffffff", label="QR code of your certificate's address")
    assert play.abe.get(release.QR_PATH).get_data(as_text=True) == svg(f"{BASE}/{CODE_B}", dark="#000000", light="#ffffff",
                                                                          label="QR code of your certificate's address"), "each their own"
    # the runs draw exactly the dark modules of the symbol, nothing more or less
    m, q = matrix(f"{BASE}/{CODE_A}"), 4
    drawn = set()
    for x, y, w in re.findall(r"M(\d+),(\d+)h(\d+)v1h-\3z", re.search(r'<path d="([^"]+)"', picture).group(1)):
        drawn |= {(int(x) - q + i, int(y) - q) for i in range(int(w))}
    assert drawn == {(x, y) for y in range(len(m)) for x in range(len(m)) if m[y][x]}


def test_the_qr_picture_is_only_ever_the_viewers_own_and_only_while_released(play):
    load_ab(play)
    assert play.alice.get(release.QR_PATH).status_code == 404, "nothing before the release"
    put(play.admin, "now")
    assert play.bob.get(release.QR_PATH).status_code == 404, "no code of one's own: nothing (never somebody else's)"
    anonymous = play.app.test_client()
    assert anonymous.get(release.QR_PATH).status_code in (302, 401, 403), "a visitor gets nothing"
    put(play.admin, None)
    assert play.alice.get(release.QR_PATH).status_code == 404, "hidden again: gone"


def test_hiding_takes_the_card_away_and_is_audited(play):
    load_ab(play)
    put(play.admin, "now")
    r = put(play.admin, None)
    assert r.status_code == 200 and r.get_json()["data"]["released"] is False
    assert CODE_A not in page(play.alice)
    details = [a.detail for a in Audit.query.filter_by(action="certificates.release").order_by(Audit.id).all()]
    assert details[0].startswith("released now (2 codes)") and details[1].startswith("hidden again")
    assert all(a.actor_name == "admin" for a in Audit.query.filter_by(action="certificates.release").all()), "who did it is recorded"


def test_a_set_time_shows_them_and_sends_the_toast_with_the_first_request_after_it(play):
    load_ab(play)
    r = put(play.admin, T_LIVE + 3600)
    assert r.status_code == 200 and r.get_json()["data"]["released"] is False
    assert CODE_A not in page(play.alice) and notes() == []
    with clock(T_LIVE + 3601):
        assert CODE_A in page(play.alice)
        play.bob.get("/team")
        assert len(notes()) == 1


@pytest.mark.parametrize("at", [T_LIVE - 10, T_LIVE + 61 * 24 * 3600, "soon", 1.5, True, {"x": 1}])
def test_a_moment_in_the_past_too_far_ahead_or_not_a_number_is_refused(play, at):
    load_ab(play)
    assert put(play.admin, at).status_code == 400 and release.release_at() is None


def test_an_unknown_key_or_a_missing_moment_is_refused(play):
    load_ab(play)
    assert play.admin.put(API + "/release", json={"at": "now", "who": "x"}).status_code == 400
    assert play.admin.put(API + "/release", json={}).status_code == 400
    assert release.release_at() is None


def test_only_the_crew_reaches_the_api_and_the_page(play):
    load_ab(play)
    for path in (API, "/admin/l3mon/certificates", "/plugins/l3mon_certificates/assets/certificates.js"):
        assert play.alice.get(path).status_code in (302, 403), path
    assert put(play.alice, "now").status_code in (302, 403) and release.release_at() is None
    assert play.admin.get("/admin/l3mon/certificates").status_code == 200


def test_a_deleted_account_takes_its_code_with_it(play):
    load_ab(play)
    play.admin.delete(f"/api/v1/users/{play.ids['abe']}", json={})
    assert [r.user_id for r in Certificate.query.all()] == [play.ids["alice"]]


# ---- the independent review of 27 Nov 2026 -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("at", ["now", None])
def test_a_release_or_hide_is_saved_only_together_with_its_audit_line(play, monkeypatch, at):
    load_ab(play)
    if at is None:
        put(play.admin, "now")
    before = release.release_at()
    from CTFd.plugins.l3mon_certificates import api

    def broken(*args, **kwargs):
        raise RuntimeError("the audit line could not be written")

    monkeypatch.setattr(api.audit, "record", broken)
    with pytest.raises(RuntimeError):
        play.app.config["PROPAGATE_EXCEPTIONS"] = True
        put(play.admin, at)
    db.session.rollback()
    assert release.release_at() == before, "no change without its audit line"


def test_an_unexpected_error_in_the_import_prints_no_code_or_username(play, monkeypatch, capsys):
    def broken(payload, apply=False):
        raise RuntimeError(f"(IntegrityError) [parameters: (7, 'alice', '{CODE_A}')]")

    monkeypatch.setattr(loader, "load", broken)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(the_list()).encode())))
    assert loader.main(["--apply"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "nothing was changed" in err and CODE_A not in err and "alice" not in err


def test_a_set_time_with_no_codes_left_sends_nothing_until_codes_are_loaded(play):
    load_ab(play)
    put(play.admin, T_LIVE + 3600)
    Certificate.query.delete()
    db.session.commit()
    with clock(T_LIVE + 3601):
        play.bob.get("/team")
        assert notes() == [] and release.announced_at() is None, "the one notification is not spent on zero cards"
        load_ab(play)
        play.bob.get("/team")
        assert len(notes()) == 1


def test_a_notification_that_keeps_failing_is_retried_at_most_every_30_seconds(play, monkeypatch):
    calls = []

    def broken(t=None):
        calls.append(t)
        raise RuntimeError("cannot send")

    monkeypatch.setattr(release, "announce", broken)
    release._failed_at = None
    with clock(T_LIVE + 10):
        play.bob.get("/team")
        play.bob.get("/team")
    assert len(calls) == 1
    with clock(T_LIVE + 41):
        play.bob.get("/team")
    assert len(calls) == 2
    release._failed_at = None
