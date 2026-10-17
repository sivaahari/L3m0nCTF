import hashlib
import hmac
import logging
import os
import re

from CTFd.plugins.flags import BaseFlag
from CTFd.utils.user import get_current_team

_log = logging.getLogger("l3mon.instances.flag")
_WRAPPER_PREFIX = "L3m0n" + "{"
_WRAPPER_SUFFIX = "}"
_FLAG_SHAPE = re.compile(re.escape("L3m0n") + r"\{[0-9a-f]{32}\}\Z")


def get_flag_secret() -> str:
    path = "/run/secrets/FLAG_HMAC_SECRET"
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
        except Exception as e:
            _log.warning("Could not read secret file %s: %s", path, e)
    return os.getenv("FLAG_HMAC_SECRET", "").strip()


def derive_team_flag(key: str | bytes, team_id: int, challenge_id: int) -> str:
    if isinstance(key, str):
        key_bytes = key.strip().encode("utf-8")
    else:
        key_bytes = key.strip()

    msg = f"{int(team_id)}:{int(challenge_id)}".encode("utf-8")
    digest = hmac.new(key_bytes, msg, hashlib.sha256).hexdigest()[:32].lower()
    return f"{_WRAPPER_PREFIX}{digest}{_WRAPPER_SUFFIX}"


def is_valid_flag_shape(flag: str) -> bool:
    if not isinstance(flag, str):
        return False
    return bool(_FLAG_SHAPE.fullmatch(flag))


class L3monDynamicFlag(BaseFlag):
    name = "l3mon_dynamic"
    templates = {
        "create": "/plugins/l3mon_instances/assets/create.html",
        "update": "/plugins/l3mon_instances/assets/edit.html",
    }

    @staticmethod
    def compare(chal_key_obj, provided):
        if not provided or not isinstance(provided, str):
            return False

        team = get_current_team()
        if team is None:
            return False

        secret = get_flag_secret()
        if not secret:
            _log.error("FLAG_HMAC_SECRET is missing or empty; cannot verify per-team flag")
            return False

        challenge_id = chal_key_obj.challenge_id
        expected = derive_team_flag(secret, team.id, challenge_id)

        # Constant-time comparison for the correct team flag
        if hmac.compare_digest(expected, provided):
            return True

        # Check for flag reuse evidence if format matches exact flag shape
        if is_valid_flag_shape(provided):
            try:
                from CTFd.plugins.l3mon_instances.models import L3monInstanceLog
                # Query teams that held an instance for this challenge, max 2000 teams
                logs = L3monInstanceLog.query.filter_by(challenge_id=challenge_id).limit(2000).all()
                for entry in logs:
                    if entry.team_id != team.id:
                        cand = derive_team_flag(secret, entry.team_id, challenge_id)
                        if hmac.compare_digest(cand, provided):
                            # Record flag reuse evidence
                            try:
                                from CTFd.plugins.l3mon_guard import evidence
                                evidence.record(
                                    kind="flag_reuse",
                                    team_id=team.id,
                                    challenge_id=challenge_id,
                                    other_team_id=entry.team_id,
                                    detail=f"Team {team.id} submitted flag of team {entry.team_id}"[:200],
                                )
                            except ImportError:
                                pass
                            except Exception as ev_err:
                                _log.warning("Failed to record flag reuse evidence: %s", ev_err)
                            break
            except Exception as e:
                _log.warning("Error checking flag reuse: %s", e)

        return False
