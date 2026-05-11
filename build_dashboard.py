#!/usr/bin/env python
"""Build the static integration-matrix dashboard.

Reads the per-variant JSONs from results/ (stable.json, latest.json,
dev.json - any missing variant just shows as missing cells) and emits:

  site/index.html              Nx3 matrix
  site/cells/<pkg>__<v>.html   per-(package, variant) detail page
  site/variants/<v>.html       per-variant detail with full freeze

Publishing to gh-pages is done from CI via a published action; this
script only builds locally.
"""

import argparse
import json
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

import status


VARIANTS = status.VARIANTS


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
                            "detail_filename": "", "resolved_version": ""}
            else:
                cells[v] = {
                    **status.cell_badge(entry),
                    "detail_filename": f"{name}__{v}.html",
                    "resolved_version": entry.get("resolved_version", ""),
                }
        rows.append({"name": name, "cells": cells})
    return rows


def _issues(by_variant):
    """List non-passing cells with a one-line error excerpt where available."""
    out = []
    for v in VARIANTS:
        data = by_variant[v]
        if not data:
            continue
        for entry in data["packages"]:
            badge = status.cell_badge(entry)
            if badge["status"] in ("pass", "missing"):
                continue
            excerpt = ""
            for line in (entry.get("install_error") or "").splitlines():
                line = line.strip()
                if line:
                    excerpt = line[:200]
                    break
            out.append({
                "name": entry["name"],
                "variant": v,
                "status": badge["status"],
                "label": badge["label"],
                "error_excerpt": excerpt,
            })
    return out


def build(results_dir, output_dir, templates_dir, single_page=False):
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

    if single_page:
        single_tpl = env.get_template("single_page.html")
        (output_dir / "dashboard.html").write_text(
            single_tpl.render(
                variants=variants_meta,
                rows=rows,
                issues=_issues(by_variant),
            )
        )
        print(f"Wrote dashboard.html to {output_dir}/")
        return

    (output_dir / "cells").mkdir()
    (output_dir / "variants").mkdir()
    index_tpl = env.get_template("index.html")
    cell_tpl = env.get_template("cell.html")
    variant_tpl = env.get_template("variant.html")

    (output_dir / "index.html").write_text(
        index_tpl.render(variants=variants_meta, rows=rows)
    )
    n_pages = 1

    for v in VARIANTS:
        data = by_variant[v]
        if not data:
            continue
        for entry in data["packages"]:
            (output_dir / "cells" / f"{entry['name']}__{v}.html").write_text(
                cell_tpl.render(
                    entry=entry,
                    variant=v,
                    status=status.cell_badge(entry),
                    astropy_version=data["astropy"]["version"],
                )
            )
            n_pages += 1
        (output_dir / "variants" / f"{v}.html").write_text(
            variant_tpl.render(
                variant=v,
                data=data,
                install_badge=status.INSTALL_BADGE,
                test_badge=status.TEST_BADGE,
            )
        )
        n_pages += 1

    print(f"Wrote {n_pages} pages to {output_dir}/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--output", default="site")
    ap.add_argument("--templates-dir", default="templates")
    ap.add_argument("--single-page", action="store_true",
                    help="Render a single self-contained dashboard.html (no cell or "
                         "variant pages). Suitable for serving as a non-zipped GH "
                         "Actions artifact in PR previews.")
    args = ap.parse_args()
    build(args.results_dir, args.output, args.templates_dir,
          single_page=args.single_page)


if __name__ == "__main__":
    main()
