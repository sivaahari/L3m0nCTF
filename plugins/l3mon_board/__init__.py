"""l3mon_board: the board, the live tick, the programme panel's extra fields and the flag reply's extra fields (SP3 part 3.4).

    envelope.py   the answer shapes (success, error, request id, ETag)
    viewer.py     who is asking, and the gate in front of the player endpoints
    board.py      the channels, the programmes on air and the studio's own numbers
    ticks.py      what the pages poll; news.py the numbers for the bell
    api.py        GET /api/v1/l3mon/board and /ticks
    panel.py      the `l3mon` block on a challenge, and the contract's wording before the start
    replies.py    the `l3mon` block on the reply to a flag, and the phase rules for a flag and a hint
    hooks.py      the one extension point (SP4's instances)
"""


def load(app):
    from CTFd.plugins.l3mon_core.versions import check_ctfd_version

    check_ctfd_version()  # first of all: on a CTFd we were not built for no l3mon plugin loads

    from CTFd.plugins.l3mon_board import api, panel, replies

    app.register_blueprint(api.bp)
    panel.install(app)
    replies.install(app)
