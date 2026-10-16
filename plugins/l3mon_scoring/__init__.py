"""l3mon_scoring: what a programme is worth, Revoke and Restore, Bonus and the studios' private notes (SP3 part 3.3).

    values.py     the formula (CTFd's, over a count we supply), recalculate, drifted, heal
    freeze.py     the values stand still while the scoreboard is frozen (part 3.6): CTFd's two valuation functions wrapped
    triggers.py   when to recalculate: after a ban, a hide or a delete, and every minute as a safety net
    notes.py      a studio's private lines
    bonus.py      TRP with a private message
    voids.py      Revoke and Restore
    wording.py    the hint error says TRP
    api.py        the crew's API and page
"""


def load(app):
    from CTFd.plugins.l3mon_core.versions import check_ctfd_version

    check_ctfd_version()  # first of all: on a CTFd we were not built for no l3mon plugin loads

    from CTFd.plugins.l3mon_scoring import api, freeze, triggers, wording

    from CTFd.plugins import register_admin_plugin_menu_bar

    freeze.install()
    freeze.install_doors(app)
    triggers.install(app)
    wording.install(app)
    app.register_blueprint(api.bp)
    app.register_blueprint(api.page_bp)
    register_admin_plugin_menu_bar("Scoring", "/admin/l3mon/scoring")
