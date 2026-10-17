"""Database models for l3mon_auth: Continue with Google (SP8 B5).

Tables:
    l3mon_identity    maps (provider, subject) -> user_id
"""
import datetime

from CTFd.models import db
from CTFd.plugins.l3mon_core.clock import now


def _current_time():
    return datetime.datetime.utcfromtimestamp(now())


class Identity(db.Model):
    __tablename__ = "l3mon_identity"
    __table_args__ = (
        db.UniqueConstraint("provider", "subject", name="uq_l3mon_identity_provider_subject"),
    )

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(32), nullable=False, default="google")
    subject = db.Column(db.String(255), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_current_time)
