"""The one table of the certificates (created by migrations/ on MariaDB and by create_all on SQLite, CTFd's own rule).

    l3mon_certificate   one row per player: the verification code of their qualifier certificate (a PAR code of the portal)

A player has at most one code and a code belongs to one player (two unique keys). The row goes with the account (ON DELETE CASCADE). The
username column is the name the code was issued to, kept for the crew's checks (a player who renamed themself after the export still gets
their code: the row is keyed by the account, never by the name). The migration must say exactly the same (tests/test_certificates_mariadb.py).
"""
import datetime

from sqlalchemy import UniqueConstraint

from CTFd.models import db


def _utcnow():
    return datetime.datetime.utcnow()


class Certificate(db.Model):
    __tablename__ = "l3mon_certificate"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_l3mon_certificate_user"),
        UniqueConstraint("code", name="uq_l3mon_certificate_code"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    code = db.Column(db.String(32), nullable=False)
    username = db.Column(db.String(128), nullable=False)
    imported_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
