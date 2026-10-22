"""CTFd's theme loader falls back to its own templates page by page (SP6 slice 1, the first test of the build).

With our theme's login.html present, GET /login is ours and GET /register is still CTFd's core page. The fallback is CTFd's
THEME_FALLBACK setting (true by default). If this stops being true, the one-theme-page-by-page design is wrong: stop and report.

Run through tools/run-ctfd-tests.sh, with L3MON_MOUNT_PLUGINS=1 so the working-tree theme is mounted:
    env -u MSYS_NO_PATHCONV L3MON_MOUNT_PLUGINS=1 tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_theme
"""
from tests.helpers import create_ctfd, destroy_ctfd

# the id of the sign-in heading in the prototype's sign-in page; CTFd's core login page has no such id
OURS = 'id="login-h"'


def test_a_page_the_theme_has_is_ours_and_a_page_it_lacks_is_still_ctfds():
    app = create_ctfd(enable_plugins=True, ctf_theme="l3mon")  # plugins on, as in production: the theme's pages read the plugin's values
    with app.app_context():
        with app.test_client() as client:
            login = client.get("/login").get_data(as_text=True)
            assert OURS in login, "GET /login should be the theme's own sign-in page"
            register = client.get("/register")
            assert register.status_code == 200
            body = register.get_data(as_text=True)
            assert OURS not in body, "GET /register should still be CTFd's own page"
            assert "<form" in body
    destroy_ctfd(app)
