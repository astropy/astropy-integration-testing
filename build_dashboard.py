#!/usr/bin/env python
"""Build the single-page integration-matrix dashboard.

Reads every results/<variant>__<python>.json that run_integration.py
wrote and emits a single self-contained `site/index.html` with:
  - one row per package
  - one column group per Python version, subdivided into the three
    astropy variants (stable / pre / dev)
  - a "Failure logs" section at the bottom with collapsible <details>
    for every non-passing cell, anchored from the matching badge
"""

import argparse
import json
import re
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

import status


VARIANTS = status.VARIANTS


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


def _column_groups(by_combo):
    """Discover Python versions and order columns.

    Returns:
      pythons: sorted list of Python version strings (preserves config-style
               ordering: shorter strings first, then alphabetical).
      columns: flat list of (python, variant) tuples in display order.
    """
    pythons = sorted({p for _, p in by_combo}, key=lambda s: (len(s), s))
    columns = [(p, v) for p in pythons for v in VARIANTS]
    return pythons, columns


def _variant_meta(variant, python, data):
    if not data:
        return {"variant": variant, "python": python, "has_data": False}
    return {
        "variant": variant,
        "python": python,
        "has_data": True,
        "astropy_version": data["astropy"]["version"],
        "extra_index_urls": data["astropy"].get("extra_index_urls") or [],
        "python_version": data.get("python_version") or python,
        "started_at": data["started_at"],
        "finished_at": data["finished_at"],
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
        for python, variant in columns:
            data = by_combo.get((variant, python))
            entry = None
            if data:
                entry = next((e for e in data["packages"] if e["name"] == name), None)
            if entry is None:
                cells.append({
                    "status": "missing", "label": "-",
                    "anchor": "", "resolved_version": "",
                })
                continue
            badge = status.cell_badge(entry)
            anchor = (_anchor_id(name, variant, python)
                      if badge["status"] not in ("pass", "missing") else "")
            cells.append({
                **badge,
                "anchor": anchor,
                "resolved_version": entry.get("resolved_version", ""),
            })
        rows.append({"name": name, "cells": cells})
    return rows


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
            out.append({
                "name": entry["name"],
                "variant": variant,
                "python": python,
                "status": badge["status"],
                "label": badge["label"],
                "install_error": entry.get("install_error", ""),
                "test_output": entry.get("test_output", ""),
                "anchor": _anchor_id(entry["name"], variant, python),
            })
    return out


def build(results_dir, output_dir, templates_dir):
    results_dir = Path(results_dir)
    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )

    by_combo = _load_results(results_dir)
    pythons, columns = _column_groups(by_combo)
    names = _ordered_packages(by_combo)
    rows = _make_rows(by_combo, names, columns)
    failures = _failures(by_combo, columns)
    variants_meta = [_variant_meta(v, p, by_combo.get((v, p)))
                     for p in pythons for v in VARIANTS]

    (output_dir / "index.html").write_text(
        env.get_template("index.html").render(
            pythons=pythons,
            variants=list(VARIANTS),
            variants_meta=variants_meta,
            rows=rows,
            failures=failures,
        )
    )
    print(f"Wrote {output_dir}/index.html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--output", default="site")
    ap.add_argument("--templates-dir", default="templates")
    args = ap.parse_args()
    build(args.results_dir, args.output, args.templates_dir)


if __name__ == "__main__":
    main()
