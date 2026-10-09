"""l3mon_story: the cold-open comic (a channel's short motion comic, opened from a button once the channel is on air).

    format.py   the file format and every check on it (standard library only; the build tool imports this very file)
    store.py    the checked stories on disk, read from the folder L3MON_STORY_DIR
    api.py      the gate: who may open which story and when; the page that plays it
    assets/     the player (comic.js, comic.css, sounds.js), engine only: no story is in the image
"""


def load(app):
    from CTFd.plugins.l3mon_core.versions import check_ctfd_version

    check_ctfd_version()  # first of all: on a CTFd we were not built for no l3mon plugin loads

    from CTFd.plugins.l3mon_board import hooks
    from CTFd.plugins.l3mon_story import api, store

    app.register_blueprint(api.bp)
    app.register_blueprint(api.page_bp)
    api.install_assets(app)
    hooks.cold_open = store.panels_of  # the board shows a "Cold open" button for a channel that is on air and has a story
