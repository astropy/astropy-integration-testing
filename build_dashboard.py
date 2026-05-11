#!/usr/bin/env python
"""Build the single-page integration-matrix dashboard.

Reads the per-variant JSONs from results/ (stable.json, pre.json,
dev.json - any missing variant just shows as missing cells) and emits
a single self-contained `site/index.html` with the matrix, a legend,
and collapsible per-cell failure logs.
"""

import argparse
import json
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

import status


VARIANTS = status.VARIANTS


def _anchor_id(name, variant):
    return f"log-{name}__{variant}"


def _variant_meta(name, data):
    if not data:
        return {"name": name, "has_data": False}
    return {
        "name": name,
        "has_data": True,
        "astropy_version": data["astropy"]["version"],
        "extra_index_urls": data["astropy"].get("extra_index_urls") or [],
        "python_version": data["python_version"],
        "started_at": data["started_at"],
        "finished_at": data["finished_at"],
    }


def _ordered_packages(by_variant):
    """Return package names in the order they first appear across variants."""
    seen = []
    seen_set = set()
    for v in VARIANTS:
        if not by_variant[v]:
            continue
        for entry in by_variant[v]["packages"]:
            if entry["name"] not in seen_set:
                seen.append(entry["name"])
                seen_set.add(entry["name"])
    return seen


def _make_rows(by_variant, names):
    rows = []
    for name in names:
        cells = {}
        for v in VARIANTS:
            data = by_variant[v]
            entry = None
            if data:
                entry = next((e for e in data["packages"] if e["name"] == name), None)
            if entry is None:
                cells[v] = {"status": "missing", "label": "-",
                            "anchor": "", "resolved_version": ""}
            else:
                badge = status.cell_badge(entry)
                anchor = (_anchor_id(name, v)
                          if badge["status"] not in ("pass", "missing") else "")
                cells[v] = {
                    **badge,
                    "anchor": anchor,
                    "resolved_version": entry.get("resolved_version", ""),
                }
        rows.append({"name": name, "cells": cells})
    return rows


def _failures(by_variant):
    """List non-passing cells with their full logs."""
    out = []
    for v in VARIANTS:
        data = by_variant[v]
        if not data:
            continue
        for entry in data["packages"]:
            badge = status.cell_badge(entry)
            if badge["status"] in ("pass", "missing"):
                continue
            out.append({
                "name": entry["name"],
                "variant": v,
                "status": badge["status"],
                "label": badge["label"],
                "install_error": entry.get("install_error", ""),
                "test_output": entry.get("test_output", ""),
                "anchor": _anchor_id(entry["name"], v),
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

    by_variant = {
        v: (json.loads((results_dir / f"{v}.json").read_text())
            if (results_dir / f"{v}.json").exists() else None)
        for v in VARIANTS
    }
    names = _ordered_packages(by_variant)
    rows = _make_rows(by_variant, names)
    variants_meta = [_variant_meta(v, by_variant[v]) for v in VARIANTS]
    failures = _failures(by_variant)

    (output_dir / "index.html").write_text(
        env.get_template("index.html").render(
            variants=variants_meta,
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
