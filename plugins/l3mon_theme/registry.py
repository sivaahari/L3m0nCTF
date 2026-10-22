"""How a page of the theme adds what only CTFd knows, without editing a shared file (SP6).

A page's module (pages/<page>.py) writes

    from CTFd.plugins.l3mon_theme.registry import value

    @value("studio_name", default="")          # the adapter reads {{ l3mon.studio_name }}
    def studio_name():
        ...

The function is called only when a template reads the value, so a page that is not ours costs nothing. A failure gives the
default and one line in the log (kind of error only), exactly like the shared values in values.py. A name may be taken only
once, and never one of values.py's own (a second page that wants the same name must share the first one's).
"""
import importlib
import logging
import pkgutil

_log = logging.getLogger("l3mon")
_PROVIDERS = {}  # name -> (function, default)


def value(name, default=None):
    """Register `function()` as the value `l3mon.<name>` of the theme's templates."""
    if not isinstance(name, str) or not name.isidentifier() or name.startswith("_"):
        raise ValueError(f"a theme value is a plain name, not {name!r}")

    def register(function):
        from CTFd.plugins.l3mon_theme.values import Values

        if hasattr(Values, name):
            raise ValueError(f"the theme value '{name}' belongs to values.py; ask the lead engineer, do not shadow it")
        if name in _PROVIDERS and _PROVIDERS[name][0] is not function:
            raise ValueError(f"the theme value '{name}' is already provided by {_PROVIDERS[name][0].__module__}")
        _PROVIDERS[name] = (function, default)
        return function

    return register


def provider(name):
    """(function, default) of a registered value, or None."""
    return _PROVIDERS.get(name)


def names():
    return sorted(_PROVIDERS)


def discover(app):
    """Import every module of pages/ (in the order of their names) and call its install(app), if it has one."""
    from CTFd.plugins.l3mon_theme import pages

    found = []
    for info in sorted(pkgutil.iter_modules(pages.__path__), key=lambda i: i.name):
        module = importlib.import_module(f"{pages.__name__}.{info.name}")
        install = getattr(module, "install", None)
        if callable(install):
            install(app)
        found.append(info.name)
    return found
