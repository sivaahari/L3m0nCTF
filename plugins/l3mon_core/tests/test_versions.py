"""The plugins refuse to load on a CTFd they were not built for.

Run through tools/run-ctfd-tests.sh, which provides CTFd's own test helpers:
    tools/run-ctfd-tests.sh l3mon/ctfd:dev -- -q -p no:randomly -p no:cacheprovider /l3mon_tests/l3mon_core
"""
import pytest

import CTFd
from CTFd.plugins.l3mon_core.versions import PINNED_CTFD, check_ctfd_version
from tests.helpers import create_ctfd, destroy_ctfd


def test_the_pin_is_the_version_of_the_image():
    assert PINNED_CTFD == "3.8.8"
    assert CTFd.__version__ == PINNED_CTFD, "the image and the pin must move together"
    check_ctfd_version()  # does not raise


def test_a_different_version_is_refused_and_both_versions_are_named():
    with pytest.raises(RuntimeError) as err:
        check_ctfd_version("3.9.0")
    message = str(err.value)
    assert "3.8.8" in message and "3.9.0" in message


@pytest.mark.parametrize("found", ["3.8.9", "3.8", "4.0.0", "", "v3.8.8", "3.8.8 "])
def test_nothing_but_the_exact_pin_passes(found):
    with pytest.raises(RuntimeError):
        check_ctfd_version(found)


def test_the_app_does_not_start_on_another_version(monkeypatch):
    monkeypatch.setattr(CTFd, "__version__", "3.9.0")
    with pytest.raises(RuntimeError) as err:
        create_ctfd(enable_plugins=True)
    assert "3.8.8" in str(err.value) and "3.9.0" in str(err.value)


def test_with_plugins_off_the_version_is_not_looked_at(monkeypatch):
    monkeypatch.setattr(CTFd, "__version__", "3.9.0")
    app = create_ctfd(enable_plugins=False)
    destroy_ctfd(app)
