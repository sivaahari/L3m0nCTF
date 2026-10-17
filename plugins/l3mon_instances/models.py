import datetime
from sqlalchemy import CheckConstraint, UniqueConstraint
from CTFd.models import db


def _utcnow():
    return datetime.datetime.utcnow()


class L3monInstance(db.Model):
    __tablename__ = "l3mon_instance"
    __table_args__ = (
        CheckConstraint(
            "state in ('starting', 'running', 'stopping', 'expired', 'error')",
            name="ck_l3mon_instance_state",
        ),
        CheckConstraint(
            "protocol in ('http', 'tcp')",
            name="ck_l3mon_instance_protocol",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    instance_id = db.Column(db.String(32), unique=True, nullable=False, index=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False, index=True)
    slug = db.Column(db.String(48), nullable=False)
    protocol = db.Column(db.String(8), nullable=False)
    state = db.Column(db.String(16), nullable=False, default="running")
    host_name = db.Column(db.String(64), nullable=False, default="default")
    url = db.Column(db.String(255), nullable=True)
    host = db.Column(db.String(128), nullable=True)
    port = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    stopped_at = db.Column(db.DateTime, nullable=True)
    last_renewed_at = db.Column(db.DateTime, nullable=True)
    renewals = db.Column(db.Integer, nullable=False, default=0)
    # active_key is f"{team_id}:{challenge_id}" while running; NULL when stopped/expired.
    # In MariaDB and SQLite, UNIQUE constraints allow multiple NULLs, enforcing strictly at most 1 active instance.
    active_key = db.Column(db.String(64), unique=True, nullable=True)

    def to_dict(self):
        now = _utcnow()
        expires_in = max(0, int((self.expires_at - now).total_seconds()))
        ends = int(self.expires_at.timestamp())
        return {
            "challenge_id": self.challenge_id,
            "state": self.state,
            "protocol": self.protocol,
            "url": self.url,
            "host": self.host,
            "port": self.port,
            "expires_in": expires_in,
            "ends": ends,
        }


class L3monInstanceLog(db.Model):
    """Historical tracking of every team that has held an instance of a challenge, for flag-reuse detection."""
    __tablename__ = "l3mon_instance_log"
    __table_args__ = (
        UniqueConstraint("team_id", "challenge_id", name="uq_l3mon_instance_log_team_chal"),
    )

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False, index=True)
    first_seen_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
