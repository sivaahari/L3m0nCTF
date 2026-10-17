"""l3mon_auth: Continue with Google (OpenID Connect with PKCE) (SP8).

Provides OpenID Connect authentication flow for Google sign-in.
Off until configured.
"""
from CTFd.plugins.l3mon_core.versions import check_ctfd_version


def load(app):
    check_ctfd_version()

    from CTFd.plugins.migrations import upgrade
    from CTFd.plugins.l3mon_auth.oauth import is_active, oauth

    # 1. Run database migration for l3mon_identity
    upgrade(plugin_name="l3mon_auth")

    # 2. Register OAuth blueprint
    app.register_blueprint(oauth)

    # 3. Context processor for theme templates
    @app.context_processor
    def inject_auth_context():
        return {
            "l3mon_auth": {
                "google": {
                    "active": is_active(),
                    "auth_url": "/auth/google",
                }
            }
        }
