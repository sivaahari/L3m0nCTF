import datetime
import logging
from flask import g, has_request_context

from CTFd.models import db
from CTFd.plugins.l3mon_core.clock import now
from CTFd.plugins.l3mon_instances.models import L3monInstance

_log = logging.getLogger("l3mon.instances.hooks")


def _now_dt() -> datetime.datetime:
    return datetime.datetime.utcfromtimestamp(now())


def instance_summary_for(team, challenge_id):
    """Board extension hook: returns instance summary for a programme.

    Executes at most one query per request by caching all active instances
    for the team in Flask g.
    """
    if not team or getattr(team, "id", None) is None:
        return None
    if challenge_id is None:
        return None

    now_dt = _now_dt()

    if has_request_context():
        cache = getattr(g, "_l3mon_instance_summaries", None)
        if cache is None:
            cache = {}
            g._l3mon_instance_summaries = cache

        if team.id not in cache:
            # Single query per request for all active instances for this team
            active = L3monInstance.query.filter(
                L3monInstance.team_id == team.id,
                L3monInstance.active_key != None,  # noqa: E711
                L3monInstance.expires_at > now_dt,
            ).all()

            team_cache = {}
            for inst in active:
                expires_in = max(0, int((inst.expires_at - now_dt).total_seconds()))
                ends = int(inst.expires_at.timestamp())
                team_cache[inst.challenge_id] = {
                    "state": inst.state,
                    "expires_in": expires_in,
                    "ends": ends,
                }
            cache[team.id] = team_cache

        return cache[team.id].get(challenge_id)

    # Outside request context fallback
    inst = L3monInstance.query.filter(
        L3monInstance.team_id == team.id,
        L3monInstance.challenge_id == challenge_id,
        L3monInstance.active_key != None,  # noqa: E711
        L3monInstance.expires_at > now_dt,
    ).first()
    if not inst:
        return None
    expires_in = max(0, int((inst.expires_at - now_dt).total_seconds()))
    ends = int(inst.expires_at.timestamp())
    return {
        "state": inst.state,
        "expires_in": expires_in,
        "ends": ends,
    }


def on_pull_back(challenge_ids):
    """Release pull-back handler: marks active instances for withheld challenges as stopping and returns immediately."""
    if not challenge_ids:
        return
    try:
        now_dt = _now_dt()
        L3monInstance.query.filter(
            L3monInstance.challenge_id.in_(challenge_ids),
            L3monInstance.active_key != None,  # noqa: E711
        ).update(
            {
                L3monInstance.state: "stopping",
                L3monInstance.stopped_at: now_dt,
                L3monInstance.active_key: None,
            },
            synchronize_session=False,
        )
        db.session.commit()
    except Exception as e:
        _log.warning("on_pull_back failed to mark instances: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass
