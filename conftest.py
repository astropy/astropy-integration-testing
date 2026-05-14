"""Repo-level pytest plugin, auto-discovered because the runner invokes
every `pytest --pyargs <module>` with cwd set to the repo root.

Two jobs:

1. PR-preview smoke runs: when the env var `PYTEST_LIMIT_N` is set to a
   positive integer, pytest collects normally and then truncates the
   test list to the first N items. This lets the PR matrix surface a
   fast smoke signal per package (typically 10 tests each) without
   per-package configuration.

2. Supply shared test fixtures that a package defines in its own
   repo-root conftest.py but does not ship in the installed wheel.
   `pytest --pyargs` collects from site-packages, so such fixtures are
   otherwise missing and their tests error at setup. Currently:
   `tmp_cwd` (astroquery).
"""

import os
from pathlib import Path

import pytest


def pytest_collection_modifyitems(config, items):
    raw = os.environ.get("PYTEST_LIMIT_N")
    if not raw:
        return
    try:
        n = int(raw)
    except ValueError:
        return
    if n > 0 and len(items) > n:
        del items[n:]


@pytest.fixture(scope="function")
def tmp_cwd(tmp_path):
    """astroquery shim: run the test in a pristine temp working directory.

    Exists solely for astroquery, which defines this fixture in its
    repo-root conftest.py. That file is not part of the installed
    package, so the fixture is missing under `pytest --pyargs
    astroquery` and the esa/utils, esa/iso and esa/xmm_newton download
    tests error out at setup. Remove this if astroquery ever ships the
    fixture inside the package.
    """
    old_dir = Path.cwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(old_dir)
