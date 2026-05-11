"""Shared status vocabulary for integration-test results.

Both run_integration.py (which writes statuses into results JSON) and
build_dashboard.py (which renders them into the dashboard) use these
constants and helpers, so the strings are defined in exactly one place.
"""

# The three astropy variants. Listed once here so both scripts agree.
VARIANTS = ("stable", "pre", "dev")

# install_status values, used in results/<variant>.json
INSTALLED = "installed"
SKIPPED = "skipped"           # resolver couldn't satisfy alongside the existing venv
INSTALL_FAIL = "install-fail"  # install itself crashed (build error, network, etc.)
NO_SPEC = "no-spec"            # couldn't build an install spec (no repo_url / no PyPI release)

# test_status values
PASS = "pass"
FAIL = "fail"
NO_TESTS = "no-tests"
TIMEOUT = "timeout"


# Tabular maps used on the per-variant page.
INSTALL_BADGE = {
    INSTALLED: {"status": "pass", "label": "installed"},
    SKIPPED: {"status": "skipped", "label": "skipped"},
    INSTALL_FAIL: {"status": "install-fail", "label": "install fail"},
    NO_SPEC: {"status": "missing", "label": "no spec"},
    "": {"status": "missing", "label": "-"},
}

TEST_BADGE = {
    PASS: {"status": "pass", "label": "PASS"},
    FAIL: {"status": "fail", "label": "FAIL"},
    NO_TESTS: {"status": "no-tests", "label": "no tests"},
    TIMEOUT: {"status": "timeout", "label": "timeout"},
    "": {"status": "missing", "label": "-"},
}


def cell_badge(entry):
    """Map a result entry to the {status, label} for its matrix cell.

    Install failures (including no-spec) take precedence over test status.
    """
    install = entry.get("install_status", "")
    if install != INSTALLED:
        return INSTALL_BADGE.get(install, INSTALL_BADGE[""])
    return TEST_BADGE.get(entry.get("test_status", ""), TEST_BADGE[""])
