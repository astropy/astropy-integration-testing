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

The harness itself is not astropy-specific: `core_package` in
`packages.yaml` defines the ecosystem's core package, so the same code
can drive integration testing for another ecosystem (e.g. sunpy) just
by pointing the config at a different core package and package list.

How it works
------------

The `variant` job runs on a schedule (and on `workflow_dispatch`) as a
matrix over the *columns* configured in `packages.yaml`. Each column is
an independent (Python version, variant) pair — the two axes are not a
cross-product, so you can test e.g. `3.12 + stable`, `3.13 + pre` and
`3.14 + dev` if you want. The three variants are:

| Variant  | Core package                                          | Each package                           |
|----------|-------------------------------------------------------|----------------------------------------|
| `stable` | Latest non-pre-release on PyPI                        | Latest non-pre-release on PyPI         |
| `pre`    | Latest including pre-releases (`--prerelease=allow`)  | Latest including pre-releases          |
| `dev`    | Latest dev wheel from `core_package.dev_index_urls` (or `git+repo_url` if unset) | `git+<repo_url>` (HEAD of main branch) |

Within each matrix job, a single shared venv is built: the core package
is installed first, then each package one at a time in a deterministic
order (coordinated first, alphabetical within each tier). If a package
can't be installed alongside the existing venv (e.g., it pins
`astropy<7` but we already installed astropy 8), it's skipped and
recorded; the rest of the venv is untouched. After installs, `pytest
--pyargs <module>` runs for each package that installed successfully.

A small `setup` job parses `columns:` from `packages.yaml` and emits it
as the workflow matrix, so the column list has a single source of
truth. The `dashboard` job then downloads the per-matrix-job result
JSONs and publishes the dashboard to `gh-pages`.

What's in the repo
------------------

| File                                  | Purpose                                              |
|---------------------------------------|------------------------------------------------------|
| `packages.yaml`                       | The config: `core_package`, the `columns` to test, and the `packages` list. |
| `astropy_integration/config.py`       | Loads and validates `packages.yaml` (shared by `run` and `dashboard`). |
| `astropy_integration/run.py`          | Runs one or more columns: resolve specs, install, test, write `results/<variant>__<python>.json`. |
| `astropy_integration/dashboard.py`    | Reads `results/*.json`, renders `site/index.html` (single self-contained page). |
| `astropy_integration/cli.py`          | Console entry point that dispatches the `run` and `dashboard` subcommands. |
| `astropy_integration/status.py`       | Shared status vocabulary (used by both `run` and `dashboard`). |
| `astropy_integration/templates/`      | HTML/CSS for the dashboard (loaded as package data). |
| `pyproject.toml`                      | Package metadata; declares the `astropy-integration` console script. |
| `conftest.py`                         | Repo-root pytest plugin that caps each package to the first `PYTEST_LIMIT_N` tests for PR previews. |
| `.github/workflows/integration.yml`   | The matrix workflow (`setup` builds the matrix, `variant` runs each column, `dashboard` publishes). |
| `.github/workflows/preview-link.yml`  | Companion that posts the "View dashboard preview" status check on PRs. |
| `sunpy_pytest.ini`                    | Custom pytest config referenced by sunpy's `pytest_args` (sunpy's own config requires plugins we don't install). |

Running locally
---------------

```bash
pip install .
# uv is required; see https://docs.astral.sh/uv/

# Run one variant. Each variant takes 30-90 min depending on package count.
astropy-integration run --variant stable

# Or a single package, to iterate faster:
astropy-integration run --variant stable --packages reproject

# Or a tier subset (default: all tiers run):
astropy-integration run --variant stable --tiers coordinated,other

# Build the dashboard from whatever results/*.json files exist:
astropy-integration dashboard

# Preview locally:
python -m http.server -d site 8000
```

Results land in `results/<variant>__<python>.json`; the dashboard in
`site/`. Both directories are gitignored.

Core package
------------

`packages.yaml` has a top-level `core_package` block — the package
installed into the shared venv before everything else:

```yaml
core_package:
  pypi_name: astropy
  module: astropy
  repo_url: https://github.com/astropy/astropy.git
  # dev variant: install nightly wheels from these indexes.
  # omit to install the dev version from git+repo_url instead.
  dev_index_urls:
    - https://pypi.anaconda.org/astropy/simple
    - https://pypi.anaconda.org/liberfa/simple
```

To retarget the harness at a different ecosystem, point `core_package`
at that ecosystem's core package and replace the `packages` list.
`dashboard_title` (also top-level) sets the dashboard's heading.

Columns
-------

`packages.yaml` has a top-level `columns` list. Each column is one
(Python version, variant) pair and becomes one dashboard column;
Python version and variant are independent, so list whatever
combinations you want (uv notation for Python, so `"3.14t"` is the
free-threaded 3.14 build):

```yaml
columns:
  - {python: "3.12", variant: stable}
  - {python: "3.13", variant: pre}
  - {python: "3.14t", variant: dev}
```

The runner tests every column; `--variant` / `--python` narrow that to
a subset. The dashboard groups consecutive columns that share a Python
version under a spanning header, so a classic `python x variant`
layout still renders as grouped columns. The CI matrix is generated
from this list by the `setup` job, so there's nothing to keep in sync.

Adding or disabling a package
-----------------------------

Edit `packages.yaml`. Each entry takes:

- `pypi_name` (the package's name on PyPI; also used as the row label)
- `tier` (label used for ordering and the `--tiers` filter; conventional
  values are `coordinated`, `affiliated`, `pyopensci`, `other`)
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
3. The `setup` job reads `columns:` from `packages.yaml` and the
   matrix expands to one parallel `variant` job per column; the
   `dashboard` job waits for them and publishes to `gh-pages`.

PR previews
-----------

`integration-matrix` also runs on pull requests. Same column matrix
as the scheduled run, just with a different final step: the
`dashboard` job uploads `site/index.html` as a non-zipped artifact
(`actions/upload-artifact@v7` with `archive: false`) instead of
publishing to gh-pages. The companion `preview-link` workflow
attaches a "View dashboard preview" status check to the commit
whose "Details" link opens the rendered page directly in the
browser.

This means the PR preview reflects *this PR's actual matrix run*,
not last main's data. To keep PR feedback fast, each package is
capped at the first 10 collected tests (via `PYTEST_LIMIT_N=10`,
applied by the repo-level `conftest.py`); the preview is a smoke
check of layout, install resolution, and the workflow itself, not
a full regression signal. Concurrency cancels in-progress PR runs
when a new push lands, so only the latest push consumes CI time.

`preview-link.yml` lives at `.github/workflows/preview-link.yml`
and must be on the default branch for its `workflow_run` trigger
to fire. The first PR after merging the workflow won't get the
status check.
