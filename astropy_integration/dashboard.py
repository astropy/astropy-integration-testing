"""Build the single-page integration-matrix dashboard.

Reads every results/<variant>__<python>.json that `astropy-integration run`
wrote and emits a single self-contained `site/index.html` with:

  - one row per package
  - one column per configured (python, variant) pair, in config
    order; consecutive columns that share a Python version are
    grouped under a spanning header
  - a "Failure logs" section at the bottom with collapsible <details>
    for every non-passing cell, anchored from the matching badge
"""

import json
import re
import shutil
from itertools import groupby
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from . import config, status


def _anchor_id(name, variant, python):
    # Anchors are only used inside the page so we just need
    # something HTML-id-safe and unique per (pkg, variant, python).
    safe_python = re.sub(r"[^A-Za-z0-9]", "_", python)
    return f"log-{name}__{variant}__{safe_python}"


def _load_results(results_dir):
    """Return {(variant, python): data, ...} for every results/*.json found."""
    by_combo = {}
    for f in sorted(results_dir.glob("*.json")):
        if f.name.startswith("_"):
            continue
        data = json.loads(f.read_text())
        v = data.get("variant")
        p = data.get("python_requested") or data.get("python_version") or ""
        if not v or not p:
            continue
        by_combo[(v, p)] = data
    return by_combo


def _column_groups(by_combo, config_columns):
    """Order the columns that produced results and group them for the header.

    Columns are ordered to match the `columns:` list in the config; any
    result not listed there (e.g. a leftover JSON from an old config) is
    appended afterwards, sorted. The header groups *consecutive* columns
    that share a Python version, so a config that pairs each Python with
    a single variant renders as plain columns while the classic
    python x variant layout still renders as spanning groups.

    Returns:
      columns: flat list of (python, variant) tuples in display order.
      groups:  list of {"python", "span"} dicts for the top header row.
    """
    present = set(by_combo)  # {(variant, python), ...}
    columns = []
    for col in config_columns:
        key = (col["variant"], col["python"])
        if key in present:
            columns.append((col["python"], col["variant"]))
            present.discard(key)
    for variant, python in sorted(present):
        columns.append((python, variant))

    groups = []
    for python, _variant in columns:
        if groups and groups[-1]["python"] == python:
            groups[-1]["span"] += 1
        else:
            groups.append({"python": python, "span": 1})
    return columns, groups


def _variant_meta(variant, python, data):
    if not data:
        return {"variant": variant, "python": python, "has_data": False}
    return {
        "variant": variant,
        "python": python,
        "has_data": True,
        "core_version": data["core"]["version"],
        "extra_index_urls": data["core"].get("extra_index_urls") or [],
        "python_version": data.get("python_version") or python,
        "started_at": data["started_at"],
        "finished_at": data["finished_at"],
        "installed_deps": data.get("installed_deps") or {},
    }


def _ordered_packages(by_combo):
    """Package names in the order they first appear across all results."""
    seen = []
    seen_set = set()
    for _, data in by_combo.items():
        for entry in data["packages"]:
            if entry["name"] not in seen_set:
                seen.append(entry["name"])
                seen_set.add(entry["name"])
    return seen


def _make_rows(by_combo, names, columns):
    rows = []
    for name in names:
        cells = []
        row_tier = None
        for python, variant in columns:
            data = by_combo.get((variant, python))
            entry = None
            if data:
                entry = next((e for e in data["packages"] if e["name"] == name), None)
            if entry is None:
                cells.append(
                    {
                        "status": "missing",
                        "label": "-",
                        "anchor": "",
                        "resolved_version": "",
                    }
                )
                continue
            if row_tier is None:
                row_tier = entry.get("tier", "coordinated")
            badge = status.cell_badge(entry)
            anchor = (
                _anchor_id(name, variant, python)
                if badge["status"] not in ("pass", "missing")
                else ""
            )
            cells.append(
                {
                    **badge,
                    "anchor": anchor,
                    "resolved_version": entry.get("resolved_version", ""),
                }
            )
        rows.append({"name": name, "tier": row_tier or "coordinated", "cells": cells})
    return rows


def _group_rows_by_tier(rows):
    """Group rows by tier, preserving order. Returns [(tier, [rows]), ...]."""
    return [
        (tier, list(group)) for tier, group in groupby(rows, key=lambda r: r["tier"])
    ]


def _failures(by_combo, columns):
    """Per-cell failure detail (full logs), ordered by column then row."""
    out = []
    for python, variant in columns:
        data = by_combo.get((variant, python))
        if not data:
            continue
        for entry in data["packages"]:
            badge = status.cell_badge(entry)
            if badge["status"] in ("pass", "missing"):
                continue
            out.append(
                {
                    "name": entry["name"],
                    "variant": variant,
                    "python": python,
                    "status": badge["status"],
                    "label": badge["label"],
                    "install_error": entry.get("install_error", ""),
                    "test_output": entry.get("test_output", ""),
                    "anchor": _anchor_id(entry["name"], variant, python),
                }
            )
    return out


def build(results_dir, output_dir, config_path):
    results_dir = Path(results_dir)
    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    env = Environment(
        loader=PackageLoader("astropy_integration", "templates"),
        autoescape=select_autoescape(["html"]),
    )

    config_columns = config.load_columns(config_path)
    core = config.load_core_package(config_path)
    title = config.load_dashboard_title(config_path)

    by_combo = _load_results(results_dir)
    columns, column_groups = _column_groups(by_combo, config_columns)
    names = _ordered_packages(by_combo)
    rows = _make_rows(by_combo, names, columns)
    tier_groups = _group_rows_by_tier(rows)
    failures = _failures(by_combo, columns)
    variants_meta = [
        _variant_meta(variant, python, by_combo.get((variant, python)))
        for python, variant in columns
    ]

    # If any variant ran with a per-package test limit, surface it in
    # a banner; PR previews use this to keep wall time bounded.
    limits = {
        d.get("pytest_limit_n")
        for d in by_combo.values()
        if d and d.get("pytest_limit_n")
    }
    pytest_limit = min(limits) if limits else None

    (output_dir / "index.html").write_text(
        env.get_template("index.html").render(
            title=title,
            core_name=core["pypi_name"],
            column_groups=column_groups,
            columns=columns,
            variants_meta=variants_meta,
            tier_groups=tier_groups,
            failures=failures,
            pytest_limit=pytest_limit,
        )
    )
    print(f"Wrote {output_dir}/index.html")


def add_arguments(ap):
    ap.add_argument("--config", default="packages.yaml")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--output", default="site")


def run(args):
    build(args.results_dir, args.output, args.config)
