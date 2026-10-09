"""The one extension point of the board that a later part fills.

SP4 (instances) sets `instance_summary` to a function `(team, challenge_id) -> dict | None`. The dict is
`{"state": "starting|running|stopping|expired|error", "expires_in": seconds, "ends": absolute_epoch_second}`. `expires_in` counts down
and is not part of the board's ETag; `ends` is, and is never sent to the page. Until SP4 exists there are no instances, so the
board says `instance: null` for every programme.
"""

instance_summary = None  # replaced by SP4


def instance_for(team, challenge_id):
    if instance_summary is None or team is None:
        return None
    return instance_summary(team, challenge_id)

cold_open = None  # replaced by l3mon_story: a function (channel slug) -> number of panels of the channel's cold-open comic, or None


def cold_open_for(slug):
    """The number of panels of a channel's cold open, or None when there is none. Only asked for a channel that has something on air."""
    if cold_open is None:
        return None
    return cold_open(slug)
