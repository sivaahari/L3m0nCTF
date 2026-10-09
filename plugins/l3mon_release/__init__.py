"""l3mon_release: the crew decides which channels and programmes players can see, and when (SP3 part 3.2).

    reconcile.py   CTFd's challenge state follows the plan; the announcement; the guard against hands
    scheduler.py   a drop happens on time, once, however many workers run
    guards.py      the doors CTFd leaves open
    api.py         the crew's API and page
"""


def load(app):
    from CTFd.plugins.l3mon_core.versions import check_ctfd_version

    check_ctfd_version()  # first of all: on a CTFd we were not built for no l3mon plugin loads

    from CTFd.plugins.l3mon_release import api, guards, reconcile, scheduler

    from CTFd.plugins import register_admin_plugin_menu_bar

    reconcile.install(app)
    scheduler.install(app)
    guards.install(app)
    app.register_blueprint(api.bp)
    app.register_blueprint(api.page_bp)
    register_admin_plugin_menu_bar("Release control", "/admin/l3mon/release")
