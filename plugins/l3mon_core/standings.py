"""The ranking the public sees, made once.

CTFd's standings are every studio that has a score, best first, in the order its tie-break gives (the earlier last change of score
wins; hidden and banned studios are left out, and while the scoreboard is frozen the frozen scores are used). The scoreboard page and
the CTFtime feed both show the studios with a score above zero, numbered 1, 2, 3 by the rows shown (a studio left out leaves no gap).
They get their rows from this one function, so the site and the feed cannot disagree.
"""


def number(score):
    """A score as a number a person reads: whole when it is one (it nearly always is), otherwise rounded to two places."""
    value = float(score)
    return int(value) if value == int(value) else round(value, 2)


def ranked(standings):
    """-> [(position, row)] for the rows with a score above zero, in the order they were given."""
    out = []
    for row in standings:
        if row.score is None or float(row.score) <= 0:
            continue
        out.append((len(out) + 1, row))
    return out
