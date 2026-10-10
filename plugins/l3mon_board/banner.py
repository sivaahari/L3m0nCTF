"""The phase banner the Guide and the scoreboard carry (the contract: `{id, cls, lead, text}` or null).

A phase is one of before, live, paused and ended (never two at once); the freeze is a flag that only a live broadcast shows (after the end the wrap says it all, and a pause says its own piece). One banner is shown at a time, and before the start there is none.
"""
_BANNERS = {
    "wrap": {"id": "wrap", "cls": "banner-wrap", "lead": "THAT'S A WRAP.", "text": "The broadcast has ended. You can still read every programme."},
    "paused": {"id": "paused", "cls": "banner-warn", "lead": "PAUSED.", "text": "Submissions are paused for a moment. Your progress is safe."},
    "frozen": {"id": "frozen", "cls": "banner-info", "lead": "SCOREBOARD FROZEN.", "text": "Standings are frozen for the final hour. Your solves still count."},
}


def for_phase(phase):
    """-> a new dict for the phase (see l3mon_core.clock.Phase), or None."""
    if phase.state == "ended":
        key = "wrap"
    elif phase.state == "paused":
        key = "paused"
    elif phase.state == "live" and phase.frozen:
        key = "frozen"
    else:
        return None
    return dict(_BANNERS[key])
