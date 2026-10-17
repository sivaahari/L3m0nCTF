import datetime
import logging
import re

from flask import Blueprint, request

from CTFd.models import Challenges, db
from CTFd.plugins.l3mon_board import envelope, viewer
from CTFd.plugins.l3mon_core.airing import is_on_air
from CTFd.plugins.l3mon_core.clock import current_phase, now
from CTFd.plugins.l3mon_core.models import Programme
from CTFd.plugins.l3mon_core.visibility import is_visible
from CTFd.plugins.l3mon_instances.client import InstancerClient, InstancerClientError
from CTFd.plugins.l3mon_instances.flag import derive_team_flag, get_flag_secret
from CTFd.plugins.l3mon_instances.models import L3monInstance, L3monInstanceLog
from CTFd.utils.user import get_current_user

_log = logging.getLogger("l3mon.instances.api")
bp = Blueprint("l3mon_instances", __name__, url_prefix="/api/v1/l3mon")

_DIGITS_PATTERN = re.compile(r"^[0-9]{1,9}\Z")
MAX_TEAM_INSTANCES = 3
COOLDOWN_SECONDS = 60


def _parse_id(val) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        s = str(val)
    elif isinstance(val, str):
        s = val
    else:
        return None
    if not _DIGITS_PATTERN.fullmatch(s):
        return None
    return int(s)


def _now_dt() -> datetime.datetime:
    return datetime.datetime.utcfromtimestamp(now())


@bp.route("/instances", methods=["GET"])
@viewer.player
def get_instances():
    team = viewer.current_team()
    if team is None:
        return envelope.fail("no_team", 403, "Set up your studio to carry on.")

    now_dt = _now_dt()
    instances = L3monInstance.query.filter(
        L3monInstance.team_id == team.id,
        L3monInstance.active_key != None,  # noqa: E711
        L3monInstance.expires_at > now_dt,
    ).all()

    limits = {
        "live": len(instances),
        "max": MAX_TEAM_INSTANCES,
        "cooldown_seconds": COOLDOWN_SECONDS,
    }
    return envelope.ok({
        "instances": [i.to_dict() for i in instances],
        "limits": limits,
    })


@bp.route("/instances", methods=["POST"])
@viewer.player
def start_instance():
    phase = current_phase().state
    if phase != "live":
        return envelope.fail("not_live", 409, f"The contest is {phase}.", phase=phase)

    team = viewer.current_team()
    if team is None:
        return envelope.fail("no_team", 403, "Set up your studio to carry on.")

    user = get_current_user()
    data = request.get_json(silent=True) or {}
    raw_id = data.get("challenge_id")
    challenge_id = _parse_id(raw_id)
    if challenge_id is None:
        return envelope.fail("not_found", 404, "Challenge not found.")

    # Check programme delivery and visibility
    prog = Programme.query.filter_by(challenge_id=challenge_id).first()
    if prog is None or prog.delivery != "live_single":
        return envelope.fail("not_found", 404, "Challenge not found.")

    chal = Challenges.query.get(challenge_id)
    if chal is None or not is_on_air(challenge_id) or not is_visible(chal, solved_ids=viewer.current().solved):
        return envelope.fail("not_found", 404, "Challenge not found.")

    now_dt = _now_dt()
    # 1. Idempotency: if active instance already exists, return it
    active_key = f"{team.id}:{challenge_id}"
    existing = L3monInstance.query.filter_by(active_key=active_key).first()
    if existing:
        if existing.expires_at > now_dt and existing.state in ("running", "starting"):
            return envelope.ok(existing.to_dict(), status=200)

    # 2. Quota check: at most 3 live instances per team
    active_count = L3monInstance.query.filter(
        L3monInstance.team_id == team.id,
        L3monInstance.active_key != None,  # noqa: E711
        L3monInstance.expires_at > now_dt,
    ).count()
    if active_count >= MAX_TEAM_INSTANCES:
        return envelope.fail("quota", 409, f"Studio active instance quota ({MAX_TEAM_INSTANCES}) reached.")

    # 3. Cooldown check: 60s cooldown after previous stop
    last_instance = L3monInstance.query.filter_by(
        team_id=team.id,
        challenge_id=challenge_id,
    ).order_by(L3monInstance.id.desc()).first()
    if last_instance and last_instance.stopped_at:
        elapsed = (now_dt - last_instance.stopped_at).total_seconds()
        if elapsed < COOLDOWN_SECONDS:
            retry_after = max(1, int(COOLDOWN_SECONDS - elapsed))
            return envelope.fail("cooldown", 429, f"Challenge is in cooldown. Retry in {retry_after}s.", retry_after=retry_after)

    # 4. Derive per-team flag
    secret = get_flag_secret()
    if not secret:
        return envelope.fail("busy", 503, "Flag secret not available.")
    flag = derive_team_flag(secret, team.id, challenge_id)

    # 5. Call instancer client
    client = InstancerClient()
    try:
        ttl_seconds = data.get("ttl_seconds")
        inst_data = client.start_instance(
            team_id=team.id,
            challenge_id=challenge_id,
            slug=prog.slug,
            flag=flag,
            ttl_seconds=ttl_seconds,
        )
    except InstancerClientError as e:
        if e.error_code == "cooldown":
            return envelope.fail("cooldown", 429, e.message, retry_after=e.retry_after or 60)
        elif e.error_code == "quota":
            return envelope.fail("quota", 409, e.message)
        elif e.error_code == "no_capacity":
            return envelope.fail("no_capacity", 503, e.message, retry_after=e.retry_after or 30)
        elif e.error_code == "not_found":
            return envelope.fail("not_found", 404, e.message)
        else:
            return envelope.fail("busy", 503, e.message)

    # 6. Record in DB
    created_ts = inst_data.get("created_at") or int(now_dt.timestamp())
    expires_ts = inst_data.get("expires_at") or int((now_dt + datetime.timedelta(minutes=45)).timestamp())
    created_at = datetime.datetime.utcfromtimestamp(created_ts)
    expires_at = datetime.datetime.utcfromtimestamp(expires_ts)

    instance_row = L3monInstance(
        instance_id=inst_data["id"],
        team_id=team.id,
        challenge_id=challenge_id,
        slug=prog.slug,
        protocol=inst_data.get("protocol", "http"),
        state="running",
        host_name="default",
        url=inst_data.get("url"),
        host=inst_data.get("host"),
        port=inst_data.get("port"),
        created_at=created_at,
        expires_at=expires_at,
        active_key=active_key,
    )
    db.session.add(instance_row)

    # Log record for flag sharing evidence detection
    log_row = L3monInstanceLog.query.filter_by(team_id=team.id, challenge_id=challenge_id).first()
    if not log_row:
        db.session.add(L3monInstanceLog(team_id=team.id, challenge_id=challenge_id, first_seen_at=now_dt))

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        _log.warning("Database insert race for instance active_key: %s", e)
        # If another worker inserted, return the active one
        existing = L3monInstance.query.filter_by(active_key=active_key).first()
        if existing:
            return envelope.ok(existing.to_dict(), status=200)
        return envelope.fail("busy", 503, "Could not register instance.")

    return envelope.ok(instance_row.to_dict(), status=201)


@bp.route("/instances/<challenge_id_param>/renew", methods=["POST"])
@viewer.player
def renew_instance(challenge_id_param):
    phase = current_phase().state
    if phase != "live":
        return envelope.fail("not_live", 409, f"The contest is {phase}.", phase=phase)

    challenge_id = _parse_id(challenge_id_param)
    if challenge_id is None:
        return envelope.fail("not_found", 404, "Challenge not found.")

    team = viewer.current_team()
    if team is None:
        return envelope.fail("no_team", 403, "Set up your studio to carry on.")

    prog = Programme.query.filter_by(challenge_id=challenge_id).first()
    if prog is None or prog.delivery != "live_single":
        return envelope.fail("not_found", 404, "Challenge not found.")

    chal = Challenges.query.get(challenge_id)
    if chal is None or not is_on_air(challenge_id) or not is_visible(chal, solved_ids=viewer.current().solved):
        return envelope.fail("not_found", 404, "Challenge not found.")

    active_key = f"{team.id}:{challenge_id}"
    instance_row = L3monInstance.query.filter_by(active_key=active_key).first()
    if not instance_row:
        return envelope.fail("not_found", 404, "Instance not found.")

    client = InstancerClient()
    try:
        renewed_data = client.renew_instance(instance_row.instance_id)
    except InstancerClientError as e:
        extra = {}
        if e.error_code == "cooldown":
            extra["retry_after"] = e.retry_after or 60
        elif e.retry_after is not None:
            extra["retry_after"] = e.retry_after
        return envelope.fail(e.error_code, e.status, e.message, **extra)

    expires_ts = renewed_data.get("expires_at")
    if expires_ts:
        instance_row.expires_at = datetime.datetime.utcfromtimestamp(expires_ts)
    instance_row.renewals = renewed_data.get("renewals", instance_row.renewals + 1)
    instance_row.last_renewed_at = _now_dt()
    db.session.commit()

    return envelope.ok(instance_row.to_dict(), status=200)


@bp.route("/instances/<challenge_id_param>", methods=["DELETE"])
@viewer.player
def stop_instance(challenge_id_param):
    challenge_id = _parse_id(challenge_id_param)
    if challenge_id is None:
        return envelope.fail("not_found", 404, "Challenge not found.")

    team = viewer.current_team()
    if team is None:
        return envelope.fail("no_team", 403, "Set up your studio to carry on.")

    active_key = f"{team.id}:{challenge_id}"
    instance_row = L3monInstance.query.filter_by(active_key=active_key).first()
    if not instance_row:
        # Idempotent stop: return success if already stopped
        return envelope.ok({"message": "Stopped."}, status=200)

    client = InstancerClient()
    try:
        client.stop_instance(instance_row.instance_id)
    except Exception as e:
        _log.warning("Client error stopping instance %s: %s", instance_row.instance_id, e)

    instance_row.state = "stopping"
    instance_row.stopped_at = _now_dt()
    instance_row.active_key = None
    db.session.commit()

    return envelope.ok({"message": "Stopped."}, status=200)
