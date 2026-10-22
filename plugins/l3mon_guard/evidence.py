"""Evidence recording contract for l3mon_guard (SP5).

Other plugins (such as the instancer) call record() to store abuse indicators.
Contract:
- Never raises (logs any error)
- Cheap (at most one insert)
- Plain text detail, max 200 chars (markup stripped, control chars removed)
- Never stores a flag or player submitted flag text
- Rejects unknown kinds with a log line
"""
import datetime
import logging
import re
from flask import has_request_context

from CTFd.models import Challenges, Teams, Users, db
from CTFd.plugins.l3mon_core.clock import now
from CTFd.plugins.l3mon_core.text import crew_text
from CTFd.plugins.l3mon_guard.models import EVIDENCE_KINDS, Evidence

_log = logging.getLogger("l3mon.guard")

FLAG_PATTERN = re.compile(r"l3m0n\{.*?\}", re.IGNORECASE)
FLAG_PREFIX = re.compile(r"l3m0n\{", re.IGNORECASE)


def _sanitize_detail(text: str) -> str:
    if not text:
        return ""
    # Never store a flag or submitted flag text
    text = FLAG_PATTERN.sub("[redacted-flag]", text)
    text = FLAG_PREFIX.sub("[redacted]", text)
    # Strip < and > so text cannot be parsed as markup, then sanitize via crew_text
    text = text.replace("<", "").replace(">", "")
    if len(text) > 200:
        text = text[:199] + "…"
    clean, _ = crew_text(text, limit=200, required=False)
    if clean is None:
        clean = text[:199] + "…"
    return clean


def record(
    kind: str,
    *,
    team_id: int | None = None,
    user_id: int | None = None,
    challenge_id: int | None = None,
    other_team_id: int | None = None,
    detail: str = "",
    address: str | None = None,
) -> None:
    """Record an abuse evidence entry into l3mon_evidence.

    `address` is the visitor's address when the caller has it (the CAPTCHA check does); otherwise it is the request's own.
    Never raises an exception; failures are logged.
    """
    if kind not in EVIDENCE_KINDS:
        _log.warning("l3mon_guard: unknown evidence kind '%s' rejected", kind)
        return

    try:
        clean_detail = _sanitize_detail(detail)

        client_ip = address
        if client_ip is None and has_request_context():
            try:
                from CTFd.utils.user import get_ip
                client_ip = get_ip()
            except Exception:
                client_ip = None

        item = Evidence(
            kind=kind,
            team_id=team_id,
            user_id=user_id,
            challenge_id=challenge_id,
            other_team_id=other_team_id,
            detail=clean_detail,
            address=(address or None) or client_ip,
            created_at=datetime.datetime.utcfromtimestamp(now()),
            status="new",
            note="",
        )
        db.session.add(item)
        db.session.commit()
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        _log.warning("l3mon_guard: failed to record evidence '%s': %s", kind, exc)
