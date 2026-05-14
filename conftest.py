"""Repo-level pytest plugin used during PR-preview runs.

When the env var `PYTEST_LIMIT_N` is set to a positive integer, pytest
collects normally and then truncates the test list to the first N
items. This lets the PR matrix surface a fast smoke signal per
package (typically 10 tests each) without per-package configuration.

Discovered automatically by pytest because cwd is the repo root for
every `pytest --pyargs <module>` invocation in the runner.
"""

import os


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
