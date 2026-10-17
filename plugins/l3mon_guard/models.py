"""Database models for l3mon_guard: the anti-abuse suite (SP5).

Tables:
    l3mon_evidence    evidence of abuse (flag reuse, bursts, suspicious IPs, failed CAPTCHA)
"""
import datetime
from sqlalchemy import CheckConstraint

from CTFd.models import db
from CTFd.plugins.l3mon_core.clock import now

EVIDENCE_KINDS = (
    "flag_reuse",
    "burst",
    "shared_address",
    "registration_burst",
    "captcha_unverified",
    "disposable_email_blocked",
    "name_policy_blocked",
)

EVIDENCE_STATUSES = (
    "new",
    "reviewed",
    "dismissed",
    "actioned",
)


def _current_time():
    return datetime.datetime.utcfromtimestamp(now())


class Evidence(db.Model):
    __tablename__ = "l3mon_evidence"
    __table_args__ = (
        CheckConstraint(
            "kind in ('flag_reuse', 'burst', 'shared_address', 'registration_burst', 'captcha_unverified', 'disposable_email_blocked', 'name_policy_blocked')",
            name="ck_l3mon_evidence_kind",
        ),
        CheckConstraint(
            "status in ('new', 'reviewed', 'dismissed', 'actioned')",
            name="ck_l3mon_evidence_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(40), nullable=False, index=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True)
    other_team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="SET NULL"), nullable=True)
    detail = db.Column(db.String(200), nullable=False, default="")
    address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_current_time)
    status = db.Column(db.String(20), nullable=False, default="new", index=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note = db.Column(db.String(200), nullable=False, default="")
