"""The CTFd version these plugins were built and tested for.

Several of our hooks depend on CTFd internals (the standings query, the dynamic-value recalculation, the plugin migration
runner, the session events). A different version could change any of them without a sign, so the plugins refuse to load on
anything but the exact pin. The pin moves together with the image digest in docker/ctfd/Dockerfile, in the same change that
re-runs CTFd's own suite with our plugins loaded.
"""
PINNED_CTFD = "3.8.8"


def check_ctfd_version(found=None):
    """Raise RuntimeError unless CTFd is exactly the pinned version. `found` is for tests; by default CTFd is asked."""
    if found is None:
        import CTFd

        found = CTFd.__version__
    if found != PINNED_CTFD:
        raise RuntimeError(
            f"The l3mon plugins are built for CTFd {PINNED_CTFD} and this is CTFd {found!r}. "
            "Refusing to load: update the plugins and their tests for the new version first."
        )
