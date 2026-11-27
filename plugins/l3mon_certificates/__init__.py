"""l3mon_certificates: the qualifier certificates on the studio page (the verification portal's part 2, docs/runbooks/22-verify-portal.md).

How a qualifier participant gets their certificate: the crew issues the codes on the laptop (`python -m l3mon verify issue`), writes the
platform's list (`verify export-platform`: player id -> code, the valid qualifier codes only) and loads it on the server with one command
(loader.py). The codes stay hidden until any crew account releases them, now or at a set time (api.py, the crew page "Certificates";
every change is in the audit trail). From that moment each player who has a code sees a "Your certificate" card on their own studio page
(their code, a QR code of the certificate's address, a button to open it), and every player gets ONE notification, "Certificates are ready".
Hiding them again takes the card away and leaves the codes valid on the portal (the portal never depends on the platform).

    models.py     l3mon_certificate: one row per player, their own code (the migration is in migrations/)
    codes.py      what a platform code may look like (the same rules as tools/l3mon/verify/codes.py: a tools test keeps them equal)
    qr.py         the QR encoder (a byte-for-byte copy of tools/l3mon/verify/qr.py: a tools test keeps them equal)
    release.py    hidden / released now / released at a set time, the one notification, and the card a player sees
    loader.py     the import, run inside the platform container (python -m CTFd.plugins.l3mon_certificates.loader [--apply] < list.json)
    api.py        the crew's API and page
"""


def load(app):
    from CTFd.plugins.l3mon_core.versions import check_ctfd_version

    check_ctfd_version()  # first of all: on a CTFd we were not built for no l3mon plugin loads

    from CTFd.plugins.l3mon_certificates import models  # noqa: F401  (registers the table)
    from CTFd.plugins.migrations import upgrade

    upgrade(plugin_name="l3mon_certificates")  # MariaDB: the migration; SQLite: create_all, like CTFd's own plugins

    from CTFd.plugins import register_admin_plugin_menu_bar
    from CTFd.plugins.l3mon_certificates import api, release

    release.install(app)  # the one notification, sent by the first request after the release moment
    app.register_blueprint(api.bp)
    app.register_blueprint(api.page_bp)
    app.register_blueprint(api.player_bp)
    register_admin_plugin_menu_bar("Certificates", "/admin/l3mon/certificates")
