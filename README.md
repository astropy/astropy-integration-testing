Integration testing for the Astropy ecosystem
=============================================

[![Integration matrix](https://github.com/astropy/astropy-integration-testing/actions/workflows/integration.yml/badge.svg)](https://github.com/astropy/astropy-integration-testing/actions/workflows/integration.yml)

Cross-ecosystem integration tests for the Astropy core and coordinated
packages. Individual packages should still test against dev/pre-release
astropy in their own CI; the goal here is to catch issues that only
appear when many packages are installed together.

The dashboard is published to
[astropy.github.io/astropy-integration-testing](https://astropy.github.io/astropy-integration-testing/)
after each scheduled run.

How it works
------------

Three CI jobs run on a schedule (and on `workflow_dispatch`), one per
astropy variant:

| Variant  | Astropy                                             | Each package                           |
|----------|-----------------------------------------------------|----------------------------------------|
| `stable` | Latest non-pre-release on PyPI                      | Latest non-pre-release on PyPI         |
| `pre`    | Latest including pre-releases (`--prerelease=allow`)| Latest including pre-releases          |
| `dev`    | Latest dev wheel from the astropy/simple channel    | `git+<repo_url>` (HEAD of main branch) |

Within each job, a single shared venv is built and packages are
installed one at a time in a deterministic order (coordinated first,
alphabetical within each tier). If a package can't be installed
alongside the existing venv (e.g. it pins `astropy<7` but we already
installed astropy 8), it's skipped and recorded; the rest of the venv
is untouched. After installs, `pytest --pyargs <module>` runs for each
package that installed successfully.

A fourth job downloads the three result JSONs and publishes the
dashboard to `gh-pages`.

What's in the repo
------------------

| File                                | Purpose                                              |
|-------------------------------------|------------------------------------------------------|
| `packages.yaml`                     | The list of packages tested + `python_versions` to test against. |
| `run_integration.py`                | Runs one or more (variant, python) combos: resolve specs, install, test, write `results/<variant>__<python>.json`. |
| `build_dashboard.py`                | Reads `results/*.json`, renders `site/index.html` (single self-contained page). |
| `status.py`                         | Shared status vocabulary (used by both scripts).     |
| `templates/`                        | HTML/CSS for the dashboard.                          |
| `.github/workflows/integration.yml` | The matrix workflow (variant x python + dashboard).  |
| `.github/workflows/preview-link.yml`| Companion that posts the "View dashboard preview" status check on PRs. |
| `sunpy_pytest.ini`                  | Custom pytest config referenced by sunpy's `pytest_args` (sunpy's own config requires plugins we don't install). |

Running locally
---------------

```bash
pip install jinja2 packaging pyyaml
# uv is required; see https://docs.astral.sh/uv/

# Run one variant. Each variant takes 30-90 min depending on package count.
python run_integration.py --variant stable

# Or a single package, to iterate faster:
python run_integration.py --variant stable --packages reproject

# Or a tier subset (default: all tiers run):
python run_integration.py --variant stable --tiers coordinated,other

# Build the dashboard from whatever results/<variant>.json files exist:
python build_dashboard.py

# Preview locally:
python -m http.server -d site 8000
```

Results land in `results/<variant>.json`; the dashboard in `site/`.
Both directories are gitignored.

Python versions
---------------

`packages.yaml` has a top-level `python_versions` list (uv notation,
so `"3.14t"` means the free-threaded 3.14 build):

```yaml
python_versions:
  - "3.12"
  - "3.14t"
```

The runner tests every (variant x python_version) combination. The
dashboard renders Python versions as grouped header columns above the
three variants. **Keep the `matrix.python` list in
`.github/workflows/integration.yml` in sync** with this — the CI uses
its own matrix because GitHub Actions can't read it from YAML directly.

Adding or disabling a package
-----------------------------

Edit `packages.yaml`. Each entry takes:

- `pypi_name` (the package's name on PyPI; also used as the row label)
- `tier` (label used for ordering and the `--tiers` filter; conventional
  values are `coordinated`, `affiliated`, `other`)
- `module` (the top-level Python module name, for `pytest --pyargs`)
- `repo_url` (for the `dev` variant install)
- `install_extras` (list, e.g. `[test, all]`)
- `extra_deps` (optional list of extra packages to add to the install)
- `pytest_args` (optional list passed through to pytest; use `-k "not foo"` to skip tests)

Every entry runs by default. Use `--tiers <subset>` on the runner to
restrict to a tier subset (e.g. `--tiers coordinated`).

Triggering a run from GitHub
----------------------------

1. Actions tab -> `integration-matrix` workflow.
2. "Run workflow" dropdown -> green button.
3. The matrix expands to `len(variants) x len(python_versions)`
   parallel jobs; the `dashboard` job waits for them and publishes
   to `gh-pages`.

PR previews
-----------

`integration-matrix` also runs on pull requests. Same three-variant
matrix as the scheduled run, just with a different final step: the
`dashboard` job uploads `site/index.html` as a non-zipped artifact
(`actions/upload-artifact@v7` with `archive: false`) instead of
publishing to gh-pages. The companion `preview-link` workflow
attaches a "View dashboard preview" status check to the commit
whose "Details" link opens the rendered page directly in the
browser.

This means the PR preview reflects *this PR's actual matrix run*,
not last main's data. It's also slower than a render-only preview
would be — expect the same wall-clock as a normal main run, up to
a few hours per push. Concurrency cancels in-progress PR runs when
a new push lands, so only the latest push consumes CI time.

`preview-link.yml` lives at `.github/workflows/preview-link.yml`
and must be on the default branch for its `workflow_run` trigger
to fire. The first PR after merging the workflow won't get the
status check.
