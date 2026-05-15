"""Load and validate the integration-testing config (``packages.yaml``).

The config has three top-level keys:

  - ``core_package``: the ecosystem's core package (astropy here, but
    swap it to retarget the whole harness at another ecosystem). It is
    installed into the shared venv before anything else.
  - ``columns``: a flat list of ``{python, variant}`` pairs. Each one
    is a single run and a single dashboard column; Python version and
    variant are fully decoupled.
  - ``packages``: the per-package list (one entry = one dashboard row).

Both ``run`` and ``dashboard`` read this file, so the parsing and
validation live here in one place.
"""

from pathlib import Path

import yaml

from . import status


def _load(path):
    return yaml.safe_load(Path(path).read_text()) or {}


def load_packages(path):
    return list(_load(path).get("packages", []))


def load_core_package(path):
    """Return the validated ``core_package`` block.

    ``module`` defaults to ``pypi_name`` when not given.
    """
    core = _load(path).get("core_package") or {}
    if not core.get("pypi_name"):
        raise ValueError(f"{path}: 'core_package.pypi_name' is required")
    core.setdefault("module", core["pypi_name"])
    return core


def load_columns(path):
    """Return the validated ``columns`` list as ``[{python, variant}, ...]``.

    Order is preserved: it drives both the run order and the dashboard
    column order.
    """
    raw = _load(path).get("columns") or []
    columns = []
    for i, col in enumerate(raw):
        python = col.get("python")
        variant = col.get("variant")
        if not python or not variant:
            raise ValueError(f"{path}: columns[{i}] needs both 'python' and 'variant'")
        if variant not in status.VARIANTS:
            raise ValueError(
                f"{path}: columns[{i}] has variant '{variant}'; "
                f"expected one of {', '.join(status.VARIANTS)}"
            )
        columns.append({"python": str(python), "variant": variant})
    if not columns:
        raise ValueError(f"{path}: at least one entry under 'columns' is required")
    return columns


def load_dashboard_title(path):
    return _load(path).get("dashboard_title") or "Ecosystem integration matrix"
