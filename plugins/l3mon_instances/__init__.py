"""l3mon_instances: per-team challenge instances and dynamic per-team flags (SP4).

    models.py      Database models (L3monInstance, L3monInstanceLog)
    flag.py        Per-team HMAC-derived flag type (l3mon_dynamic) and flag sharing evidence detection
    api.py         Player API for starting, querying, renewing, and stopping instances
    admin.py       Crew admin API for listing and killing instances with audit logs
    hooks.py       Integration hooks for l3mon_board (instance_summary) and l3mon_release (on_pull_back)
    client.py      Client talking to the instancer daemon (port 8300)
"""
import logging
from CTFd.plugins.flags import FLAG_CLASSES
from CTFd.plugins.l3mon_core.versions import check_ctfd_version
from CTFd.plugins.migrations import upgrade

_log = logging.getLogger("l3mon.instances")


def load(app):
    check_ctfd_version()

    from CTFd.plugins.l3mon_instances import models  # noqa: F401
    from CTFd.plugins.l3mon_instances.admin import admin_bp
    from CTFd.plugins.l3mon_instances.api import bp as player_bp
    from CTFd.plugins.l3mon_instances.flag import L3monDynamicFlag
    from CTFd.plugins.l3mon_instances.hooks import instance_summary_for, on_pull_back

    # Run DB migration for SQLite and MariaDB
    upgrade(plugin_name="l3mon_instances")

    # Register dynamic per-team flag class
    FLAG_CLASSES["l3mon_dynamic"] = L3monDynamicFlag

    # Register blueprints
    app.register_blueprint(player_bp)
    app.register_blueprint(admin_bp)

    # Register board hook
    try:
        import CTFd.plugins.l3mon_board.hooks as board_hooks
        board_hooks.instance_summary = instance_summary_for
    except ImportError:
        pass

    # Register release pull-back hook
    try:
        from CTFd.plugins.l3mon_release.reconcile import register_pull_back_handler
        register_pull_back_handler(on_pull_back)
    except ImportError:
        pass

    try:
        from CTFd.plugins import register_plugin_assets_directory
        register_plugin_assets_directory(app, base_path="/plugins/l3mon_instances/assets/")
    except Exception as e:
        _log.warning("Could not register plugin assets directory: %s", e)
