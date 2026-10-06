import os

import pytest


def pytest_addoption(parser):
    parser.addoption("--run-drill", action="store_true", default=False, help="run the restore drill (it destroys the development stack's data)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-drill") or os.environ.get("L3MON_DRILL") == "1":
        return
    skip = pytest.mark.skip(reason="the restore drill destroys the development data; run it with --run-drill")
    for item in items:
        if "drill" in item.keywords:
            item.add_marker(skip)
