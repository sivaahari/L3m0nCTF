"""The golden scenarios: the approved demo is the reference, the real platform must tell a studio the same things.

scenario.json is a made-up world and a list of steps (before the start, a channel opening, solves, a hint, a bonus, a scheduled drop, a
hidden and a banned studio, a pull-back, a void and a restore, a pause, the end). golden.json is what the DEMO told each studio at each
step (written by the private repo's tools/golden.mjs). This test plays the same steps through the real platform, with the crew's real
calls and the studios' real routes, and compares field by field after ids are replaced by slugs.

The only differences allowed are the ones the design chose, and they are not in the compared form at all: the channel ids, the tick
(`ver`), the news numbers, the art, the instance details, the picture key, the channel number and the sponsor's logo. Where the demo and
CTFd say the same thing in different words (CTFd's own `ratelimited` where the demo said `incorrect`), the compared form is the reason.

Run through tools/run-ctfd-tests.sh:
    L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_board/test_golden.py
"""
import datetime
import json
import os
import re

import pytest
from freezegun import freeze_time

from CTFd.models import Hints, Teams, db
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.utils import set_config
from board_world import BOARD, PROGRAMMES, RELEASE, T_END, T_START, dynamic, fixed, join_client, make_app, started, team_client
from tests.helpers import destroy_ctfd, gen_hint, login_as_user

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
SCORING = "/api/v1/l3mon/admin/scoring"
GUIDE = "/api/v1/l3mon/guide"
EPG = "/api/v1/l3mon/guide/epg"
SCOREBOARD = "/api/v1/l3mon/scoreboard"
SCOREBOARD_ROWS = "/api/v1/l3mon/scoreboard/rows"
BELL = "/api/v1/notifications"
BY_DESIGN = ("Solve voided", "Solve restored")  # the real platform words these two lines itself (the crew's reason, or a fixed sentence): only the title is compared
STORY_TARGET = 12  # the same target the demo's recorder sets for the story meter
BEFORE = T_START - 3600
LIVE = T_START + 60  # minute 0 of the scenario


def load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as handle:
        return json.load(handle)


class Round:
    """The real platform in the scenario's world, with a clock the steps move."""

    def __init__(self, app, scenario):
        self.app, self.scenario = app, scenario
        self.phase, self.offset = "live", 0
        self.challenges, self.clients, self.teams = {}, {}, {}
        self.admin = None

    # -- the clock ----------------------------------------------------------------------------------------------------------
    def now(self):
        return {"before": BEFORE, "ended": T_END + 60}.get(self.phase, LIVE + self.offset)

    def at(self):
        return freeze_time(datetime.datetime.utcfromtimestamp(self.now()))

    # -- the world ----------------------------------------------------------------------------------------------------------
    def build(self):
        s = self.scenario
        set_config("view_after_ctf", True)  # the event settings turn it on (3.2): the programmes can be read after the end
        set_config("l3mon_story_air_target", STORY_TARGET)
        started()
        with self.at():
            self.admin = login_as_user(self.app, "admin")
            for p in s["programmes"]:
                flag = f"{p['slug']}-answer"
                if p["type"] == "dynamic":
                    chal = dynamic(p["name"], p["value"], 200, 15, p["category"], flag=flag)
                    chal.attribution = p["author"]
                else:
                    chal = fixed(p["name"], p["value"], p["category"], flag=flag, attribution=p["author"], max_attempts=p["max_attempts"])
                db.session.commit()
                self.challenges[p["slug"]] = chal.id
                for cost in p["hints"]:
                    gen_hint(db, chal.id, content="a hint", cost=cost)
            plan = {
                "channels": [
                    {"slug": c["slug"], "name": c["name"], "position": c["position"], "accent": c["accent"], "synopsis": c["synopsis"],
                     **({"kind": "sponsored", "sponsor_name": c["sponsor"]} if c["sponsor"] else {})}
                    for c in s["channels"]
                ],
                "programmes": [
                    {"challenge_id": self.challenges[p["slug"]], "channel": p["channel"], "cell": p["order"], "number": p["number"], "slug": p["slug"],
                     "difficulty": p["difficulty"], "delivery": p["delivery"]}
                    for p in s["programmes"]
                ],
            }
            r = self.admin.put(PROGRAMMES, json=plan)
            assert r.status_code == 200, r.get_data(as_text=True)
            for team in s["teams"]:
                captain, *others = team["members"]
                self.clients[captain] = team_client(self.app, captain, team["name"])
                for name in others:
                    self.clients[name] = join_client(self.app, name, team["name"])
                self.teams[team["name"]] = Teams.query.filter_by(name=team["name"]).first().id
            self.hint_ids = {}
            for p in s["programmes"]:
                self.hint_ids[p["slug"]] = [h.id for h in Hints.query.filter_by(challenge_id=self.challenges[p["slug"]]).order_by(Hints.id)]
            self.programme_ids = {row.slug: row.id for row in Programme.query.all()}
            self.channel_ids = {row.slug: row.id for row in Channel.query.all()}
            self.channel_slug = {row_id: slug for slug, row_id in self.channel_ids.items()}

    # -- the crew's actions --------------------------------------------------------------------------------------------------
    def do(self, kind, args):
        if kind == "phase":
            self.phase = args[0]
            set_config("paused", args[0] == "paused")
            return None
        if kind == "advance":
            self.offset += args[0] * 60
            return None
        if kind == "release":
            what, ident, mode = args[:3]
            row_id = self.channel_ids[ident] if what == "channel" else self.programme_ids[ident]
            change = {"kind": what, "id": row_id, "mode": {"on": "release", "off": "withhold", "at": "schedule"}[mode]}
            if mode == "at":
                change["at"] = LIVE + args[3] * 60
            with self.at():
                r = self.admin.put(RELEASE, json={"changes": [change]})
            assert r.status_code == 200, r.get_data(as_text=True)
            return None
        if kind in ("hide", "ban"):
            with self.at():
                r = self.admin.patch(f"/api/v1/teams/{self.teams[args[0]]}", json={"hidden" if kind == "hide" else "banned": True})
            assert r.status_code == 200, r.get_data(as_text=True)
            return None
        if kind == "bonus":
            with self.at():
                r = self.admin.post(f"{SCORING}/bonus", json={"team_id": self.teams[args[0]], "trp": args[1], "message": args[2]})
            assert r.status_code == 200, r.get_data(as_text=True)
            return None
        if kind in ("void", "restore"):
            body = {"challenge_id": self.challenges[args[0]]}
            if kind == "void":
                body["reason"] = args[1]
            with self.at():
                r = self.admin.post(f"{SCORING}/{'revoke' if kind == 'void' else 'restore'}", json=body)
            assert r.status_code == 200, r.get_data(as_text=True)
            return None
        if kind == "attempt":
            who, slug, which = args
            self.offset += 1
            with self.at():
                r = self.clients[who].post("/api/v1/challenges/attempt", json={"challenge_id": self.challenges[slug], "submission": f"{slug}-answer" if which == "right" else "wrong-flag"})
            return self.reply(r)
        if kind == "hint":
            who, slug, index = args
            self.offset += 1
            with self.at():
                r = self.clients[who].post("/api/v1/unlocks", json={"target": self.hint_ids[slug][index], "type": "hints"})
            body = r.get_json(silent=True) or {}
            if r.status_code == 200:
                return {"http": 200, "kind": "unlocked", "extra": body["data"]["l3mon"]}
            out = {"http": r.status_code, "kind": body.get("error") or "refused"}
            if body.get("phase"):
                out["phase"] = body["phase"]
            return out
        raise AssertionError(f"unknown action {kind}")

    # -- what a studio is told ------------------------------------------------------------------------------------------------
    @staticmethod
    def reply(r):
        body = r.get_json(silent=True) or {}
        data = body.get("data") or {}
        extra = dict(data.get("l3mon") or {})
        reason = extra.pop("reason", None)
        out = {"http": r.status_code, "kind": body.get("error") or reason or data.get("status")}
        if body.get("phase"):
            out["phase"] = body["phase"]
        if extra:
            out["extra"] = extra
        return out

    def board(self, who):
        with self.at():
            r = self.clients[who].get(BOARD)
        assert r.status_code == 200, r.get_data(as_text=True)
        data = r.get_json()["data"]
        slug_of = {c["id"]: c["slug"] for c in data["channels"]}
        team = data["team"]
        return {
            "phase": data["phase"],
            "team": {"name": team["name"], "score": team["score"], "place": team["place"], "of": team["of"]} if team else None,
            "channels": [
                {"slug": c["slug"], "name": c["name"], "synopsis": c["synopsis"], "accent": c["accent"], "total": c["total"], "solved": c["solved"],
                 "signal": c["signal"], "on_air": c["on_air"], "coming": c["coming"], "sponsor": c["sponsor"]["name"] if c["sponsor"] else None}
                for c in data["channels"]
            ],
            "programmes": [
                {"slug": p["slug"], "number": p["number"], "channel": slug_of[p["channel"]], "order": p["order"], "cell": p["cell"], "name": p["name"],
                 "category": p["category"], "difficulty": p["difficulty"], "value": p["value"], "solves": p["solves"], "solved_by_me": p["solved_by_me"],
                 "first_blood_open": p["first_blood_open"], "live": p["live"]}
                for p in data["programmes"]
            ],
        }

    def get(self, who, path):
        with self.at():
            r = self.clients[who].get(path)
        assert r.status_code == 200, (path, r.status_code, r.get_data(as_text=True))
        return r

    def guide(self, who):
        d = self.get(who, GUIDE).get_json()["data"]
        t = d["team"]
        return {
            "phase": d["phase"], "banner": d["banner"], "has_grid": d["epg_sig"] != "none",
            "team": {
                "name": self.team_of(who),
                "score": t["score"], "place": t["place"], "of": t["of"], "solves": t["solves"], "hints_used": t["hints_used"], "instances_live": t["instances_live"],
                "members": [{k: m[k] for k in ("name", "captain", "you", "solves", "trp", "pct")} for m in t["members"]],
                "bonus": t["bonus"], "notes": [self.line(n) for n in t["notes"]],
                "by_channel": [{"slug": self.channel_slug[c["channel"]], "name": c["name"], "sponsor": c["sponsor"]["name"] if c["sponsor"] else None, "solved": c["solved"], "total": c["total"]}
                               for c in t["by_channel"]],
            } if t else None,
            "story": d["story"],
        }

    def team_of(self, who):
        return next(team["name"] for team in self.scenario["teams"] if who in team["members"])

    def scoreboard(self, who):
        d = self.get(who, SCOREBOARD).get_json()["data"]
        markup = self.get(who, SCOREBOARD_ROWS).get_data(as_text=True)
        rows = []
        for cls, body in re.findall(r'<li class="sb-row([^"]*)">(.*?)</li>', markup, re.S):
            rows.append({
                "pos": int(re.search(r'<b class="sb-pos">(\d+)</b>', body).group(1)), "name": re.search(r"<bdi>(.*?)</bdi>", body).group(1),
                "solves": int(re.search(r'<span class="sb-solves">(\d+) solves?</span>', body).group(1)),
                "score": int(re.search(r'<span class="sb-trp"><b>(-?\d+)</b>', body).group(1)), "you": "is-you" in cls,
            })
        return {"phase": d["phase"], "banner": d["banner"], "total": d["total"], "shown": d["shown"], "me": d["me"], "rows": rows}

    def epg(self, who):
        markup = self.get(who, EPG).get_data(as_text=True)
        if not markup:
            return None
        rows = []
        for chunk in re.findall(r'<div class="epg-row.*?(?=<div class="epg-row|\Z)', markup, re.S):
            head = re.search(r'<span class="epg-name">(.*?)</span><span class="epg-meta">(.*?)</span><span class="epg-soon">(.*?)</span><span class="epg-ad">(.*?)</span>', chunk, re.S)
            tiers = []
            for number in range(1, 6):
                cell = re.search(rf'<div class="epg-cell t-{number}">(.*?)</div>', chunk, re.S).group(1)
                tiers.append([
                    {"slug": slug, "name": name, "value": value, "cls": cls, "label": label}
                    for cls, slug, label, name, value in re.findall(
                        r'<a class="epg-b ([^"]*)" href="/board/([^"]*)" aria-label="([^"]*)"><span class="epg-n">(.*?)</span><span class="epg-v" aria-hidden="true">(\d+)</span></a>', cell)
                ])
            rows.append({"name": head.group(1), "meta": head.group(2), "soon": head.group(3), "ad": head.group(4), "tiers": tiers})
        return rows

    @staticmethod
    def line(note):
        return {"title": note["title"], "content": "" if note["title"] in BY_DESIGN else note["content"]}

    def bell(self, who):
        """The private lines only: the public "New on air" lines are the release control's (it makes one for each change the crew sends and
        numbers channels by position, the demo's recorder one for each batch and by id); the release plugin's tests cover them."""
        return [self.line(n) for n in self.get(who, BELL).get_json()["data"] if n["title"] != "New on air"]

    def panel(self, who, slug):
        with self.at():
            r = self.clients[who].get(f"/api/v1/challenges/{self.challenges[slug]}")
        if r.status_code != 200:
            body = r.get_json(silent=True) or {}
            if body.get("error"):
                out = {"http": r.status_code, "kind": body["error"]}
                if body.get("phase"):
                    out["phase"] = body["phase"]
                return out
            return {"http": r.status_code, "kind": "not_found" if r.status_code == 404 else "refused"}
        d = r.get_json()["data"]
        m = d["l3mon"]
        return {
            "http": 200, "slug": m["slug"], "number": m["number"], "name": d["name"], "category": d["category"], "channel_name": m["channel_name"],
            "sponsor": m["sponsor"]["name"] if m["sponsor"] else None, "difficulty": m["difficulty"], "author": m["author"], "live": m["live"],
            "decay": m["decay"], "start": m["start"], "floor": m["floor"], "value": d["value"], "solves": d["solves"], "solved_by_me": d["solved_by_me"],
            "first_blood_open": m["first_blood_open"], "max_attempts": d["max_attempts"], "tries_left": m["tries_left"], "score": m["score"],
            "phase": m["phase"],
        }

    def ask(self, ask):
        key = next(iter(ask))
        if key in ("board", "guide", "scoreboard", "epg", "bell"):
            return getattr(self, key)(ask[key])
        if key == "panel":
            return self.panel(*ask["panel"])
        return self.do(key, ask[key])


@pytest.fixture()
def play():
    app = make_app()
    with app.app_context():
        round_ = Round(app, load("scenario.json"))
        round_.build()
        yield round_
    destroy_ctfd(app)


def test_the_real_platform_tells_every_studio_what_the_approved_demo_told_it_at_every_step(play):
    golden = load("golden.json")
    assert golden["scenario"] == "v1" and len(golden["steps"]) == len(play.scenario["steps"])
    problems = []
    for step, want in zip(play.scenario["steps"], golden["steps"]):
        assert step["label"] == want["label"], "the golden file was written from another scenario: run tools/golden.mjs again"
        for action in step["do"]:
            key = next(iter(action))
            got = play.do(key, action[key] if isinstance(action[key], list) else [action[key]])
            if key in ("attempt", "hint"):
                expected = want["results"].pop(0)
                if got != expected["value"]:
                    problems.append((step["label"], action, expected["value"], got))
        assert len(want["results"]) == len(step["ask"]), (step["label"], "answers recorded", len(want["results"]), "asked", len(step["ask"]))
        for ask, expected in zip(step["ask"], want["results"]):
            assert ask == expected["ask"], "the golden file was written from another scenario: run tools/golden.mjs again"
            key = next(iter(ask))
            if key in ("board", "panel", "guide", "scoreboard", "epg", "bell", "attempt", "hint"):
                got = play.ask(ask) if key in ("board", "panel", "guide", "scoreboard", "epg", "bell") else play.do(key, ask[key])
                if got != expected["value"]:
                    problems.append((step["label"], ask, expected["value"], got))
    assert not problems, "\n\n".join(f"{label}\n  asked {ask}\n  demo {want}\n  real {got}" for label, ask, want, got in problems[:6]) + f"\n\n({len(problems)} differences)"
