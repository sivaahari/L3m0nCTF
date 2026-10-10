"""The crew's plan as one viewer reads it: the channels, every programme with its challenge, and which of them the viewer may see.

The Guide and the programme grid both start here, so they can never disagree about what is on air. What a player may see is decided in one place
(`l3mon_core.visibility`); before the start there is nothing to see and no programme is counted, as on the board.
"""
from types import SimpleNamespace

from CTFd.models import Challenges, db
from CTFd.plugins.l3mon_core.models import Channel, Programme
from CTFd.plugins.l3mon_core.visibility import visible_challenge_ids


def read(viewer, phase) -> SimpleNamespace:
    """-> `before` (the start has not come), `channels` (position order), `rows` ([(Programme, Challenges)] of the whole plan, by channel and
    cell; empty before the start), `shown` (the challenge ids this viewer may see) and `by_channel` ({channel id: [(Programme, Challenges)]})."""
    channels = Channel.query.order_by(Channel.position, Channel.id).all()
    by_channel = {channel.id: [] for channel in channels}
    if phase.state == "before":
        return SimpleNamespace(before=True, channels=channels, rows=[], shown=set(), by_channel=by_channel)
    shown = visible_challenge_ids(solved_ids=viewer.solved)
    rows = (
        db.session.query(Programme, Challenges)
        .join(Challenges, Programme.challenge_id == Challenges.id)
        .order_by(Programme.channel_id, Programme.cell, Programme.id)
        .all()
    )
    for programme, challenge in rows:
        by_channel.setdefault(programme.channel_id, []).append((programme, challenge))
    return SimpleNamespace(before=False, channels=channels, rows=rows, shown=shown, by_channel=by_channel)
