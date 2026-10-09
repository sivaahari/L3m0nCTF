"""The board: what a signed-in studio sees of the channels and the programmes on air, and its own score and place.

Rules (the contract's section 4 and 8, the design's section 7):

- A programme the viewer may not see is **absent**: no id, slug, number, name, category, difficulty or value. What a player may see is
  decided in one place (`l3mon_core.visibility`), which already knows the crew's plan, CTFd's own state and the prerequisites.
- Counts are allowed: a channel's `total` (the cells of its picture, on air or not), `on_air` and `coming`. A channel's storyline is
  sent only once the channel has something on air.
- Before the start there are no programmes and every total is 0. After the end every programme the plan lists is on air again.
- The answer never carries a flag, a description, a hint's text, files, the author, tries or the delivery. The only per-studio parts
  are `solved_by_me`, the studio's own numbers and its instances (SP4).
- `solves` is CTFd's own count of studios that count (not banned, not hidden), which honours the freeze. The freeze itself (the held
  tick, the studio's own later solves, the values standing still) is part 3.6; nothing here shows a live number while frozen except
  the studio's own score, as the demo does.
- Everything is read in a handful of queries, whatever the number of programmes (the speed check is part 3.7).
"""
import base64
import hashlib
import json

from CTFd.models import Challenges, Teams, db
from CTFd.plugins.l3mon_board import hooks, news
from CTFd.plugins.l3mon_core.clock import current_phase
from CTFd.plugins.l3mon_core.models import LIVE_DELIVERIES, Channel, Programme
from CTFd.plugins.l3mon_core.settings import show_coming_count
from CTFd.plugins.l3mon_core.tick import signature
from CTFd.plugins.l3mon_core.visibility import visible_challenge_ids
from CTFd.utils.challenges import get_solve_counts_for_challenges


def _signal(solved, total) -> float:
    return round(solved / total, 2) if total else 0


def _team_block(viewer, phase):
    team = viewer.team
    if team is None:
        return None
    counted = Teams.query.filter_by(hidden=False, banned=False).count()
    # The studio's own score is always live (admin=True ignores the freeze, which CTFd would apply even to its owner). The place is
    # CTFd's own; a studio that has scored nothing has no row in the standings, and while the scoreboard is frozen nobody gets one.
    place = None if phase.frozen else team.get_place(numeric=True)
    return {"id": team.id, "name": team.name, "score": int(team.get_score(admin=True) or 0), "place": place, "of": counted}


def build(viewer):
    """-> (data, etag) for a viewer (see `viewer.current`)."""
    phase = current_phase()
    before = phase.state == "before"
    channels = Channel.query.order_by(Channel.position, Channel.id).all()
    programmes, per_channel = [], {channel.id: [] for channel in channels}
    if not before:
        shown = visible_challenge_ids(solved_ids=viewer.solved)
        counts = get_solve_counts_for_challenges(admin=False) or {}
        rows = (
            db.session.query(Programme, Challenges)
            .join(Challenges, Programme.challenge_id == Challenges.id)
            .order_by(Programme.channel_id, Programme.cell, Programme.id)
            .all()
        )
        by_channel = {}
        for programme, challenge in rows:
            by_channel.setdefault(programme.channel_id, []).append((programme, challenge))
        ends = []
        for channel in channels:
            for cell, (programme, challenge) in enumerate(by_channel.get(channel.id, [])):
                per_channel[channel.id].append(programme.challenge_id)  # every programme, on air or not: the picture's cells
                if challenge.id not in shown:
                    continue  # a withheld programme is an empty cell: nothing about it leaves the server
                instance = hooks.instance_for(viewer.team, challenge.id)
                if instance is not None:
                    ends.append((challenge.id, instance.get("state"), instance.get("ends")))
                    instance = {"state": instance["state"], "expires_in": instance["expires_in"]}
                solves = int(counts.get(challenge.id, 0))
                programmes.append({
                    "id": challenge.id, "slug": programme.slug, "number": programme.number, "channel": channel.id, "order": programme.cell, "cell": cell,
                    "name": challenge.name, "category": challenge.category, "difficulty": programme.difficulty, "value": int(challenge.value or 0),
                    "solves": solves, "solved_by_me": challenge.id in viewer.solved, "first_blood_open": solves == 0,
                    "live": programme.delivery in LIVE_DELIVERIES, "instance": instance,
                })
    else:
        ends = []

    channel_rows = []
    for channel in channels:
        mine = [p for p in programmes if p["channel"] == channel.id]
        total = len(per_channel[channel.id]) if not before else 0
        solved = sum(1 for p in mine if p["solved_by_me"])
        panels = hooks.cold_open_for(channel.slug) if mine else None  # the cold-open comic (l3mon_story): only for a channel with something on air
        channel_rows.append({
            "id": channel.id, "no": channel.position, "slug": channel.slug, "name": channel.name,
            "synopsis": (channel.synopsis or "") if mine else "",  # told once the channel has something on air
            "accent": channel.accent, "picture": channel.picture_key,
            "total": total, "solved": solved, "signal": _signal(solved, total), "on_air": len(mine), "coming": total - len(mine),
            "sponsor": {"name": channel.sponsor_name, "logo": channel.sponsor_logo} if channel.kind == "sponsored" else None,
            "cold_open": {"panels": panels} if panels else None,
        })

    notif_id, notif_ver = news.state_for(viewer.team)
    data = {
        "ver": signature(), "phase": {"state": phase.state, "frozen": phase.frozen}, "team": _team_block(viewer, phase),
        "channels": channel_rows, "programmes": programmes, "notif_id": notif_id, "notif_ver": notif_ver, "show_coming": show_coming_count(),
    }
    return data, etag_of(data, ends)


def etag_of(data, ends) -> str:
    """A strong ETag over what the board draws: not the tick (it changes for every studio's news) and not the seconds left on an
    instance (its absolute end is in it instead)."""
    stable = {**data, "ver": 0, "ends": sorted(ends, key=str)}
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).digest()
    return '"b' + base64.urlsafe_b64encode(digest).decode().rstrip("=")[:22] + '"'
