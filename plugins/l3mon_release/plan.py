"""The crew's plan: checking a request, applying it, and describing what players see right now.

validate_changes / apply_changes   change what is on air (release, withhold, schedule) for channels and programmes
validate_plan / apply_plan         load the channels and programmes from the author kit (an idempotent upsert)
release_view                       everything the crew's page shows

Rules for every request: it is checked completely first and every problem is named by its field (CTFd's own shape,
`{"field": ["message", ...]}`); only a request with no problem is applied, in one transaction; the same request twice leaves
the same plan and writes nothing the second time; text is plain text, length-limited, never markup. A programme or channel
that is new starts WITHHELD: loading a plan can never put anything on air.
"""
import re
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from CTFd.models import Challenges, db
from CTFd.plugins.l3mon_core import audit
from CTFd.plugins.l3mon_core.airing import entry_on_air, to_epoch
from CTFd.plugins.l3mon_core.clock import current_phase, ist_text, window
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.plugins.l3mon_core.text import UNSAFE_MESSAGE, UNSAFE_TEXT, plain_text  # noqa: F401  (the rule moved to core; the names stay importable from here)

MAX_CHANGES = 200
MAX_CHANNELS = 20
MAX_PROGRAMMES = 400
MAX_AHEAD = 30 * 24 * 3600  # a scheduled drop may be at most this far away: a typo for a year never schedules anything
MAX_REASON = 200
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MODES = {"release": "released", "withhold": "withheld", "schedule": "scheduled"}
KINDS = ("standard", "sponsored")

CHANNEL_FIELDS = {"slug", "name", "synopsis", "accent", "picture_key", "position", "kind", "sponsor_name", "sponsor_logo"}
PROGRAMME_FIELDS = {"challenge_id", "challenge_name", "channel", "cell", "number", "slug"}


class Problems(dict):
    """field -> [messages], the shape CTFd's own API uses for errors."""

    def add(self, field, message):
        self.setdefault(field, []).append(message)


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(problems, field, value, limit, required=False, allow_null=True, plain=False):
    """A plain-text field: a string within the limit, or null. Returns the value to store. `plain` also refuses markup characters."""
    if value is None:
        if required:
            problems.add(field, "is required")
        elif not allow_null:
            problems.add(field, "may not be null")
        return None
    if not isinstance(value, str):
        problems.add(field, "must be text")
        return None
    if required and not value.strip():
        problems.add(field, "may not be empty")
    if len(value) > limit:
        problems.add(field, f"may be at most {limit} characters")
    if plain and UNSAFE_TEXT.search(value):
        problems.add(field, UNSAFE_MESSAGE)
    return value


# ---- changing what is on air ---------------------------------------------------------------------------------------------

def validate_changes(body, t):
    """-> (clean, problems). clean = {"changes": [(kind, row, state, at_datetime|None)], "reason": str}."""
    problems = Problems()
    if not isinstance(body, dict):
        problems.add("body", "send a JSON object")
        return None, problems
    for key in body:
        if key not in ("changes", "reason"):
            problems.add(key, "is not a known field")
    changes = body.get("changes")
    if not isinstance(changes, list) or not changes:
        problems.add("changes", "send a non-empty list of changes")
    elif len(changes) > MAX_CHANGES:
        problems.add("changes", f"at most {MAX_CHANGES} changes in one request")
    reason = body.get("reason", "")
    if reason is None:
        reason = ""
    if not isinstance(reason, str):
        problems.add("reason", "must be text")
    elif len(reason) > MAX_REASON:
        problems.add("reason", f"may be at most {MAX_REASON} characters")
    if problems:
        return None, problems

    channels = {row.id: row for row in Channel.query.all()}
    programmes = {row.id: row for row in Programme.query.all()}
    seen, clean = set(), []
    for i, change in enumerate(changes):
        where = f"changes[{i}]"
        if not isinstance(change, dict):
            problems.add(where, "must be an object")
            continue
        for key in change:
            if key not in ("kind", "id", "mode", "at"):
                problems.add(f"{where}.{key}", "is not a known field")
        kind, mode, ident, at = change.get("kind"), change.get("mode"), change.get("id"), change.get("at")
        if kind not in ("channel", "programme"):
            problems.add(f"{where}.kind", "must be channel or programme")
        if mode not in MODES:
            problems.add(f"{where}.mode", "must be release, withhold or schedule")
        row = None
        if not _whole(ident):
            problems.add(f"{where}.id", "must be a whole number")
        elif kind in ("channel", "programme"):
            row = (channels if kind == "channel" else programmes).get(ident)
            if row is None:
                problems.add(f"{where}.id", f"there is no {kind} {ident}")
            elif (kind, ident) in seen:
                problems.add(where, "the same entry twice in one request")
            seen.add((kind, ident))
        when = None
        if mode == "schedule":
            if not _whole(at):
                problems.add(f"{where}.at", "a schedule needs a time as whole epoch seconds (UTC)")
            elif at <= t:
                problems.add(f"{where}.at", "a scheduled time must be later than now; use release now instead")
            elif at > t + MAX_AHEAD:
                problems.add(f"{where}.at", "a scheduled time may be at most 30 days away")
            else:
                when = datetime.utcfromtimestamp(at)
        elif at is not None:
            problems.add(f"{where}.at", "only a schedule takes a time")
        if row is not None and kind in ("channel", "programme") and mode in MODES:
            clean.append((kind, row, MODES[mode], when))
    if problems:
        return None, problems
    return {"changes": clean, "reason": reason.strip()}, problems


def _describe(state, moment):
    return state if moment is None else f"{state} for {ist_text(to_epoch(moment))}"


def apply_changes(clean) -> int:
    """Set the states and write one audit line per real change. Returns how many entries changed (not committed here)."""
    changed = 0
    for kind, row, state, when in clean["changes"]:
        old_when = row.release_at if row.release_state == "scheduled" else None
        if row.release_state == state and old_when == when:
            continue
        old = _describe(row.release_state, old_when)
        row.release_state = state
        row.release_at = when
        detail = f"{old} -> {_describe(state, when)}"
        if clean["reason"]:
            detail += f"; {clean['reason']}"
        audit.record("release.set", f"{kind}:{row.slug}", detail)
        changed += 1
    return changed


# ---- loading the plan ----------------------------------------------------------------------------------------------------

def _listed(problems, body, key, limit):
    items = body.get(key, [])
    if not isinstance(items, list):
        problems.add(key, "must be a list")
        return []
    if len(items) > limit:
        problems.add(key, f"at most {limit} in one request")
        return []
    return items


def validate_plan(body):
    """-> (clean, problems). clean = {"channels": [dict], "programmes": [dict]} with every programme's challenge resolved."""
    problems = Problems()
    if not isinstance(body, dict):
        problems.add("body", "send a JSON object")
        return None, problems
    for key in body:
        if key not in ("channels", "programmes"):
            problems.add(key, "is not a known field")
    channels = _listed(problems, body, "channels", MAX_CHANNELS)
    programmes = _listed(problems, body, "programmes", MAX_PROGRAMMES)
    if problems:
        return None, problems

    known = {row.slug: row for row in Channel.query.all()}
    clean_channels, slugs = [], set()
    for i, item in enumerate(channels):
        where = f"channels[{i}]"
        if not isinstance(item, dict):
            problems.add(where, "must be an object")
            continue
        for key in item:
            if key not in CHANNEL_FIELDS:
                problems.add(f"{where}.{key}", "is not a known field")
        out = {}
        slug = _text(problems, f"{where}.slug", item.get("slug"), 40, required=True, allow_null=False)
        if isinstance(slug, str):
            if not SLUG.match(slug):
                problems.add(f"{where}.slug", "use lower-case letters, digits and hyphens, starting with a letter or digit")
            if slug in slugs:
                problems.add(f"{where}.slug", "the same channel twice in one request")
            slugs.add(slug)
            out["slug"] = slug
        existing = known.get(slug) if isinstance(slug, str) else None
        if "name" in item or existing is None:  # a new channel needs a name; an existing one keeps its own unless told
            out["name"] = _text(problems, f"{where}.name", item.get("name"), 80, required=True, allow_null=False, plain=True)
        for key, limit in (("synopsis", 200), ("accent", 32), ("picture_key", 64), ("sponsor_name", 80), ("sponsor_logo", 128)):
            if key in item:
                out[key] = _text(problems, f"{where}.{key}", item[key], limit, plain=key in ("synopsis", "sponsor_name"))
        if "position" in item:
            if not _whole(item["position"]) or not 0 <= item["position"] <= 99:
                problems.add(f"{where}.position", "must be a whole number from 0 to 99")
            else:
                out["position"] = item["position"]
        if "kind" in item:
            if item["kind"] not in KINDS:
                problems.add(f"{where}.kind", "must be standard or sponsored")
            else:
                out["kind"] = item["kind"]
        kind = out.get("kind") or (existing.kind if existing else "standard")
        sponsor = out["sponsor_name"] if "sponsor_name" in out else (existing.sponsor_name if existing else None)
        if kind == "sponsored" and not sponsor:
            problems.add(f"{where}.sponsor_name", "a sponsored channel needs the sponsor's name")
        if kind == "standard" and (item.get("sponsor_name") or item.get("sponsor_logo")):
            problems.add(f"{where}.kind", "only a sponsored channel has a sponsor")
        clean_channels.append(out)

    channel_slugs = set(known) | slugs
    names = {}
    for chal_id, name in db.session.query(Challenges.id, Challenges.name).all():
        names.setdefault(name, []).append(chal_id)
    existing_ids = {cid for (cid,) in db.session.query(Challenges.id).all()}

    clean_programmes, placed = [], {}
    for i, item in enumerate(programmes):
        where = f"programmes[{i}]"
        if not isinstance(item, dict):
            problems.add(where, "must be an object")
            continue
        for key in item:
            if key not in PROGRAMME_FIELDS:
                problems.add(f"{where}.{key}", "is not a known field")
        out = {}
        has_id, has_name = item.get("challenge_id") is not None, item.get("challenge_name") is not None
        if has_id == has_name:
            problems.add(f"{where}.challenge", "give either challenge_id or challenge_name")
        elif has_id:
            if not _whole(item["challenge_id"]) or item["challenge_id"] not in existing_ids:
                problems.add(f"{where}.challenge", f"there is no challenge {item['challenge_id']!r}")
            else:
                out["challenge_id"] = item["challenge_id"]
        else:
            found = names.get(item["challenge_name"], []) if isinstance(item["challenge_name"], str) else []
            if len(found) != 1:
                problems.add(f"{where}.challenge", "no challenge has exactly this name" if not found else "more than one challenge has this name; use challenge_id")
            else:
                out["challenge_id"] = found[0]
        if "challenge_id" in out:
            if out["challenge_id"] in placed:
                problems.add(where, "this challenge is placed twice in one request")
            placed[out["challenge_id"]] = i
        channel = item.get("channel")
        if channel not in channel_slugs:
            problems.add(f"{where}.channel", f"there is no channel {channel!r}")
        else:
            out["channel"] = channel
        if not _whole(item.get("cell")) or not 0 <= item["cell"] <= 999:
            problems.add(f"{where}.cell", "must be a whole number from 0 to 999")
        else:
            out["cell"] = item["cell"]
        if not _whole(item.get("number")) or not 1 <= item["number"] <= 9999:
            problems.add(f"{where}.number", "must be a whole number from 1 to 9999")
        else:
            out["number"] = item["number"]
        slug = _text(problems, f"{where}.slug", item.get("slug"), 48, required=True, allow_null=False)
        if isinstance(slug, str):
            if not SLUG.match(slug):
                problems.add(f"{where}.slug", "use lower-case letters, digits and hyphens, starting with a letter or digit")
            out["slug"] = slug
        clean_programmes.append(out)

    if not problems:
        _check_final_table(problems, clean_programmes, known)
    if problems:
        return None, problems
    return {"channels": clean_channels, "programmes": clean_programmes}, problems


def _check_final_table(problems, clean_programmes, known_channels):
    """The plan as it will be after this request (what is stored, overlaid with what is sent) must have no two programmes with
    the same slug, number or place; so two programmes can swap places in one request."""
    by_id = {row.id: row.slug for row in known_channels.values()}
    final = {}
    for row in Programme.query.all():
        final[row.challenge_id] = (by_id.get(row.channel_id), row.cell, row.number, row.slug)
    for item in clean_programmes:
        final[item["challenge_id"]] = (item["channel"], item["cell"], item["number"], item["slug"])
    for i, item in enumerate(clean_programmes):
        mine = final[item["challenge_id"]]
        for other_id, theirs in final.items():
            if other_id == item["challenge_id"]:
                continue
            if theirs[3] == mine[3]:
                problems.add(f"programmes[{i}].slug", f"the slug {mine[3]!r} is used by another programme")
            if theirs[2] == mine[2]:
                problems.add(f"programmes[{i}].number", f"the number {mine[2]} is used by another programme")
            if theirs[0] == mine[0] and theirs[1] == mine[1]:
                problems.add(f"programmes[{i}].cell", f"cell {mine[1]} of {mine[0]} is used by another programme")


def apply_plan(clean):
    """Create and update channels and programmes. -> counts. Raises IntegrityError if the database refuses (the caller rolls back)."""
    counts = {"channels": {"created": 0, "updated": 0, "unchanged": 0}, "programmes": {"created": 0, "updated": 0, "unchanged": 0}}
    by_slug = {row.slug: row for row in Channel.query.all()}
    for item in clean["channels"]:
        row = by_slug.get(item["slug"])
        if row is None:
            row = Channel(release_state="withheld", **item)  # never on air by being loaded
            db.session.add(row)
            by_slug[item["slug"]] = row
            counts["channels"]["created"] += 1
            continue
        changed = {key: value for key, value in item.items() if getattr(row, key) != value}
        for key, value in changed.items():
            setattr(row, key, value)
        counts["channels"]["updated" if changed else "unchanged"] += 1
    db.session.flush()

    existing = {row.challenge_id: row for row in Programme.query.all()}
    updating = []
    for item in clean["programmes"]:
        row = existing.get(item["challenge_id"])
        want = {"channel_id": by_slug[item["channel"]].id, "cell": item["cell"], "number": item["number"], "slug": item["slug"]}
        if row is None:
            db.session.add(Programme(challenge_id=item["challenge_id"], release_state="withheld", **want))
            counts["programmes"]["created"] += 1
        elif any(getattr(row, key) != value for key, value in want.items()):
            updating.append((row, want))
            counts["programmes"]["updated"] += 1
        else:
            counts["programmes"]["unchanged"] += 1
    # two programmes may swap places: park the ones that move out of the way first, so no unique key is ever met twice
    for row, _ in updating:
        row.cell, row.number, row.slug = -row.id, -row.id, f"~moving{row.id}"
    db.session.flush()
    for row, want in updating:
        for key, value in want.items():
            setattr(row, key, value)
    db.session.flush()
    return counts


# ---- what the crew's page shows ------------------------------------------------------------------------------------------

def _entry(state, moment):
    scheduled = state == "scheduled" and moment is not None
    at = to_epoch(moment) if scheduled else None
    return {"state": state, "at": at, "at_ist": ist_text(at) if at else None}


def release_view(t):
    """The whole plan as the crew sees it at second `t`: each entry's own setting, whether players see it, and the newest audit."""
    phase = current_phase(t)
    ended = phase.state == "ended"
    states = dict(db.session.query(Challenges.id, Challenges.state).all())
    chal_names = dict(db.session.query(Challenges.id, Challenges.name).all())
    channels, on_air_total, programme_total, placed = [], 0, 0, set()
    for ch in Channel.query.order_by(Channel.position, Channel.id).all():
        channel_on = ended or entry_on_air(ch.release_state, ch.release_at, t)
        items = []
        for p in Programme.query.filter_by(channel_id=ch.id).order_by(Programme.cell, Programme.id).all():
            placed.add(p.challenge_id)
            on = ended or (channel_on and entry_on_air(p.release_state, p.release_at, t))
            programme_total += 1
            on_air_total += 1 if on else 0
            items.append({
                "id": p.id, "challenge_id": p.challenge_id, "name": chal_names.get(p.challenge_id, ""), "slug": p.slug,
                "number": p.number, "cell": p.cell, **_entry(p.release_state, p.release_at),
                "on_air": on, "visible": bool(on and states.get(p.challenge_id) == "visible"),
            })
        channels.append({
            "id": ch.id, "slug": ch.slug, "name": ch.name, "position": ch.position, "kind": ch.kind, "synopsis": ch.synopsis,
            "sponsor_name": ch.sponsor_name, **_entry(ch.release_state, ch.release_at), "on_air": bool(channel_on), "programmes": items,
        })
    w = window()
    return {
        "now": int(t), "start": w.start, "end": w.end, "phase": {"state": phase.state, "frozen": phase.frozen},
        "channels": channels,
        "loose": [{"id": cid, "name": chal_names[cid]} for cid in sorted(chal_names) if cid not in placed],
        "counts": {"channels": len(channels), "programmes": programme_total, "on_air": on_air_total, "coming": programme_total - on_air_total},
        "audit": audit.recent(20),
    }


def refused(error: IntegrityError) -> Problems:
    """What the database refused, as a problem the caller can show (the validation above should have caught it first)."""
    problems = Problems()
    problems.add("plan", "the database refused this plan because of a clash with what is already stored; nothing was changed")
    return problems
