"""l3mon_theme: the platform's look for the sign-in page and the error pages (SP6 slice 1).

    values.py     what only CTFd knows, for the theme's pages: the bundle addresses, the sign-in state of the viewer, the CTFtime
                  sign-in switch, the event name and the build. Each value is read when a page asks for it, so a page that is
                  not ours costs the database nothing (tests/test_theme_hook_cost.py)
    registry.py   how a page adds its own values and hooks without editing a shared file
    pages/        one module per page: pages/login.py (CTFd reads the sign-in's `next` from the address only; the theme's form
                  keeps it in the body, so a POST to /login carries a safe `next` from the body into the address, and CTFd
                  checks it as before) and the pages of the later slices

The pages themselves are generated (ui/theme/l3mon/templates/_*.html, by L3m0nCTF-ui/platform-ui/tools/export-theme.mjs) and
hand-written adapters (ui/theme/l3mon/templates/login.html and the error pages) set up the values they read.
"""


def load(app):
    from CTFd.plugins.l3mon_core.versions import check_ctfd_version

    check_ctfd_version()  # first of all: on a CTFd we were not built for, no l3mon plugin loads

    from CTFd.plugins.l3mon_theme import registry, values

    values.install(app)
    registry.discover(app)
