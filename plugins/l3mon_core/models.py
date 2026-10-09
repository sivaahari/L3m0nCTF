"""The tables of the SP3 design (section 4: five, and the audit trail of 3.2). Created by migrations/ on MariaDB and by create_all on SQLite (CTFd's own rule).

    l3mon_channel    a channel of the broadcast: its look, its storyline, its sponsor if it has one, and whether it is on air
    l3mon_programme  the place of one challenge in one channel (cell, number, address) and whether it is on air
    l3mon_void       a solve set aside by Revoke: who, when, why, and what happened to it since (nothing is deleted)
    l3mon_bonus      the private note behind a "Bonus" award: for the team or for one member
    l3mon_note       a private line for one studio (every void and bonus writes one)
    l3mon_audit      who did what to which target, and why: one line for every admin action (added by the second migration)

Times are naive UTC, like CTFd's. The words a column may hold are enforced by the database (CHECK), so no code path can store
a state that no query knows about. The migration (migrations/) must say exactly the same; test_migration_mariadb.py proves it.
"""
import datetime

from sqlalchemy import CheckConstraint, UniqueConstraint

from CTFd.models import db


def _utcnow():
    return datetime.datetime.utcnow()


class Channel(db.Model):
    __tablename__ = "l3mon_channel"
    __table_args__ = (
        CheckConstraint("kind in ('standard', 'sponsored')", name="ck_l3mon_channel_kind"),
        CheckConstraint("release_state in ('released', 'withheld', 'scheduled')", name="ck_l3mon_channel_release"),
    )
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(40), nullable=False, unique=True)
    name = db.Column(db.String(80), nullable=False)
    synopsis = db.Column(db.String(200))  # the storyline caption, plain words, shown once the channel has something on air
    accent = db.Column(db.String(32))
    picture_key = db.Column(db.String(64))
    position = db.Column(db.Integer, nullable=False, default=0)
    kind = db.Column(db.String(16), nullable=False, default="standard")
    sponsor_name = db.Column(db.String(80))
    sponsor_logo = db.Column(db.String(128))
    release_state = db.Column(db.String(16), nullable=False, default="withheld")  # off air until the crew puts it on
    release_at = db.Column(db.DateTime)


DIFFICULTIES = ("warmup", "easy", "medium", "hard", "insane")  # the author kit's words (private repo, challenges/l3mon/schema.py)
DELIVERIES = ("static_per_participant", "static_shared", "live_single", "live_multi")
LIVE_DELIVERIES = ("live_single", "live_multi")


class Programme(db.Model):
    __tablename__ = "l3mon_programme"
    __table_args__ = (
        UniqueConstraint("channel_id", "cell", name="uq_l3mon_programme_cell"),
        CheckConstraint("release_state in ('released', 'withheld', 'scheduled')", name="ck_l3mon_programme_release"),
        CheckConstraint("difficulty in ('warmup', 'easy', 'medium', 'hard', 'insane')", name="ck_l3mon_programme_difficulty"),
        CheckConstraint("delivery in ('static_per_participant', 'static_shared', 'live_single', 'live_multi')", name="ck_l3mon_programme_delivery"),
    )
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False, unique=True)
    channel_id = db.Column(db.Integer, db.ForeignKey("l3mon_channel.id", ondelete="CASCADE"), nullable=False)
    cell = db.Column(db.Integer, nullable=False)
    number = db.Column(db.Integer, nullable=False, unique=True)
    slug = db.Column(db.String(48), nullable=False, unique=True)
    release_state = db.Column(db.String(16), nullable=False, default="withheld")  # off air until the crew puts it on
    release_at = db.Column(db.DateTime)
    difficulty = db.Column(db.String(16), nullable=False, default="medium", server_default="medium")  # the tile and the filters show it (third revision)
    delivery = db.Column(db.String(24), nullable=False, default="static_shared", server_default="static_shared")  # how the player gets it; live ones have instances (SP4)


class Void(db.Model):
    __tablename__ = "l3mon_void"
    __table_args__ = (CheckConstraint("outcome in ('open', 'restored', 'skipped', 'superseded')", name="ck_l3mon_void_outcome"),)
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    submission_id = db.Column(db.Integer, db.ForeignKey("submissions.id", ondelete="SET NULL"))
    solved_at = db.Column(db.DateTime)
    voided_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    voided_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    reason = db.Column(db.String(500), nullable=False)
    outcome = db.Column(db.String(16), nullable=False, default="open")
    restored_at = db.Column(db.DateTime)
    restored_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))


class Bonus(db.Model):
    __tablename__ = "l3mon_bonus"
    __table_args__ = (CheckConstraint("scope in ('team', 'member')", name="ck_l3mon_bonus_scope"),)
    id = db.Column(db.Integer, primary_key=True)
    award_id = db.Column(db.Integer, db.ForeignKey("awards.id", ondelete="CASCADE"), nullable=False, unique=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    scope = db.Column(db.String(8), nullable=False)
    message = db.Column(db.String(200), nullable=False)
    given_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    given_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


class Note(db.Model):
    __tablename__ = "l3mon_note"
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    text = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)


class Audit(db.Model):
    __tablename__ = "l3mon_audit"
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    actor_name = db.Column(db.String(80), nullable=False)  # kept when the account is deleted later
    action = db.Column(db.String(40), nullable=False)
    target = db.Column(db.String(100), nullable=False)
    detail = db.Column(db.String(500), nullable=False, default="")
