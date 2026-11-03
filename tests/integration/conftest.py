import os

import pytest


def pytest_addoption(parser):
    parser.addoption("--run-drill", action="store_true", default=False, help="run the restore drill (it destroys the development stack's data)")
    parser.addoption("--run-overlays", action="store_true", default=False, help="run the tests that need the instancer and operations overlays (start them first: L3MON_OVERLAYS=instancer,ops tools/compose.sh up -d --wait)")


def pytest_collection_modifyitems(config, items):
    if not (config.getoption("--run-overlays") or os.environ.get("L3MON_RUN_OVERLAYS") == "1"):
        skip_overlays = pytest.mark.skip(reason="needs the instancer and operations overlays running; start them and run with --run-overlays")
        for item in items:
            if "overlays" in item.keywords:
                item.add_marker(skip_overlays)
    if config.getoption("--run-drill") or os.environ.get("L3MON_DRILL") == "1":
        return
    skip = pytest.mark.skip(reason="the restore drill destroys the development data; run it with --run-drill")
    for item in items:
        if "drill" in item.keywords:
            item.add_marker(skip)
