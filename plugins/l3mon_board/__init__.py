"""l3mon_board: the board, the live tick, the programme panel's extra fields and the flag reply's extra fields (SP3 part 3.4), and the Guide, the
scoreboard, the bell and the bought-hint reply (part 3.5).

    envelope.py    the answer shapes (success, error, request id, ETag, the fragment, the strong ETag)
    viewer.py      who is asking, and the gate in front of the player endpoints
    board.py       the channels, the programmes on air and the studio's own numbers
    ticks.py       what the pages poll; news.py the viewer's bell list, its ids and its two numbers
    notices.py     CTFd's own notification routes (list, one, page, event stream) closed to everyone but the crew; the bell's list
    plan.py        the crew's plan as one viewer reads it (shared by the Guide and the grid)
    guide.py       the studio's own numbers, its members, the channel progress and the story meter
    epg.py         the programme grid (markup, with the signature the Guide carries)
    scoreboard.py  the first 100 studios of the CTFtime feed's list, and the studio's own line
    markup.py      the Jinja environment of the fragments (autoescape on); figures.py, banner.py the small shared rules
    api.py         GET /api/v1/l3mon/{board, ticks, guide, guide/epg, scoreboard, scoreboard/rows}
    panel.py       the `l3mon` block on a challenge, and the contract's wording before the start
    replies.py     the `l3mon` block on the reply to a flag and to a bought hint, and the phase rules for a flag and a hint
    hooks.py       the one extension point (SP4's instances)
"""


def load(app):
    from CTFd.plugins.l3mon_core.versions import check_ctfd_version

    check_ctfd_version()  # first of all: on a CTFd we were not built for no l3mon plugin loads

    from CTFd.plugins.l3mon_board import api, notices, panel, replies

    app.register_blueprint(api.bp)
    panel.install(app)
    replies.install(app)
    notices.install(app)
