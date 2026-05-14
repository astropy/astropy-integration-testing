"""Run one variant of the astropy ecosystem integration matrix.

Creates a single shared venv, installs astropy first then each package
from packages.yaml in order, recording per-package install outcome
(installed / skipped / install-fail / no-spec). Each install is
constrained so it can never downgrade a package already in the venv;
a package needing an older version is reported as skipped. Then runs
`pytest --pyargs <module>` for each successfully-installed package.
Writes results/<variant>__<python>.json with the full venv freeze and
per-package data.

Usage:
    astropy-integration run                                    # full matrix (all variants x all Python versions from config)
    astropy-integration run --variant stable                   # one variant, all configured Pythons
    astropy-integration run --variant stable --python 3.12     # one combo
    astropy-integration run --python 3.14t                     # all variants on free-threaded 3.14
    astropy-integration run --variant stable --packages reproject,sunpy
    astropy-integration run --variant stable --tiers coordinated
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml
from packaging.version import InvalidVersion, Version

from . import status

PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
ASTROPY_NIGHTLY_INDEX = "https://pypi.anaconda.org/astropy/simple"
LIBERFA_NIGHTLY_INDEX = (
    "https://pypi.anaconda.org/liberfa/simple"  # for pyerfa dev wheels
)


def _http_json(url, timeout=20):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _version_key(v):
    try:
        return Version(v)
    except InvalidVersion:
        return Version("0")


def _is_prerelease(v):
    try:
        return Version(v).is_prerelease
    except InvalidVersion:
        return True


def _yanked(files):
    return all(f.get("yanked") for f in files) if files else True


def latest_pypi(name, include_prereleases):
    info = _http_json(PYPI_JSON_URL.format(name=name))
    versions = []
    for ver, files in info["releases"].items():
        if _yanked(files):
            continue
        if not include_prereleases and _is_prerelease(ver):
            continue
        versions.append(ver)
    if not versions and not include_prereleases:
        # Fallback: package has only pre-releases on PyPI
        return latest_pypi(name, include_prereleases=True)
    if not versions:
        return None
    return sorted(versions, key=_version_key)[-1]


def _extras_suffix(pkg):
    extras = pkg.get("install_extras") or []
    return "[" + ",".join(extras) + "]" if extras else ""


def resolve_specs(packages, variant):
    """Resolve the astropy spec and per-package install specs for the variant."""
    if variant == "dev":
        # Let uv resolve the latest dev version from the astropy/simple
        # channel; we read the installed version back after install. No
        # explicit pin avoids the PEP 440 local-version segment headaches
        # that astropy's nightly wheels have (e.g. 8.1.0.dev53+gabcdef).
        #
        # `--index-strategy unsafe-best-match` is required because uv's
        # default ("first-index") only considers a single index per
        # package; astropy/simple hosts astropy AND pyerfa (the channel's
        # latest wheels sometimes ship only musllinux). unsafe-best-match
        # lets uv fall back to PyPI when the channel's only wheels don't
        # match the runner platform.
        astropy = {
            "install": "astropy",
            "version": "",
            "extra_index_urls": [ASTROPY_NIGHTLY_INDEX, LIBERFA_NIGHTLY_INDEX],
            "prerelease_strategy": "allow",
            "index_strategy": "unsafe-best-match",
        }

        def pkg_spec(pkg):
            repo = pkg.get("repo_url")
            if not repo:
                return None, None
            return f"{pkg['pypi_name']}{_extras_suffix(pkg)} @ git+{repo}", None

    else:
        include_pre = variant == "pre"
        astropy_ver = latest_pypi("astropy", include_prereleases=include_pre)
        astropy = {
            "install": f"astropy=={astropy_ver}",
            "version": astropy_ver,
            "extra_index_urls": [],
            "prerelease_strategy": "allow"
            if include_pre
            else "if-necessary-or-explicit",
            "index_strategy": None,
        }

        def pkg_spec(pkg):
            ver = latest_pypi(pkg["pypi_name"], include_prereleases=include_pre)
            if not ver:
                return None, None
            return f"{pkg['pypi_name']}{_extras_suffix(pkg)}=={ver}", ver

    pkg_specs = []
    for pkg in packages:
        spec, target = pkg_spec(pkg)
        pkg_specs.append((pkg, spec, target))
    return astropy, pkg_specs


def _resolver_conflict(stderr):
    """True if the install stderr looks like a uv resolver conflict."""
    s = stderr.lower()
    keywords = (
        "no solution found",
        "incompatible",
        "conflict",
        "could not find a version",
        "no matching distribution",
        "no version of",
    )
    return any(k in s for k in keywords)


def ensure_python(version):
    proc = subprocess.run(
        ["uv", "python", "find", version], capture_output=True, text=True, timeout=60
    )
    if proc.returncode == 0:
        return proc.stdout.strip()
    inst = subprocess.run(
        ["uv", "python", "install", version],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if inst.returncode != 0:
        sys.exit(f"uv python install {version}: {inst.stderr.strip()}")
    proc = subprocess.run(
        ["uv", "python", "find", version], capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:
        sys.exit(f"uv python find {version}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _venv_python_version(python):
    proc = subprocess.run(
        [
            python,
            "-c",
            "import sys; print('.'.join(str(x) for x in sys.version_info[:3]))",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _pkg_version(python, name):
    proc = subprocess.run(
        [
            python,
            "-c",
            "import importlib.metadata as md, sys; print(md.version(sys.argv[1]))",
            name,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _freeze(python):
    proc = subprocess.run(
        ["uv", "pip", "freeze", "--python", python],
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = {}
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "==" in line:
                n, v = line.split("==", 1)
                out[n.lower()] = v
    return out


def _write_no_downgrade_constraints(python, path):
    """Pin every installed package to '>=' its current version.

    Passed as a uv `--constraint` file to later installs so a new package
    can pull its deps forward but never downgrade what the shared venv
    already has; a package that genuinely needs an older version then
    shows up as a resolver conflict (skipped) instead of silently
    poisoning the venv for everything tested afterwards.
    """
    frozen = _freeze(python)
    lines = [f"{name}>={ver}" for name, ver in sorted(frozen.items())]
    Path(path).write_text("\n".join(lines) + ("\n" if lines else ""))


def _load_packages(path):
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return list(raw.get("packages", []))


def _load_python_versions(path):
    raw = yaml.safe_load(Path(path).read_text()) or {}
    versions = raw.get("python_versions") or []
    if not versions:
        versions = ["3.12"]
    return [str(v) for v in versions]


# Ordering of tiers when installing/displaying. Unknown tiers sort last.
TIER_RANK = {"coordinated": 0, "affiliated": 1, "pyopensci": 2, "other": 3}


def _install_order(packages):
    """Coordinated, then affiliated, then pyopensci, then other; alphabetical within each tier."""
    return sorted(
        packages,
        key=lambda p: (
            TIER_RANK.get(p.get("tier", "coordinated"), 9),
            p["pypi_name"].lower(),
        ),
    )


def _run_install(install_cmd, timeout):
    """Wrap subprocess.run with TimeoutExpired catch. Returns (rc, stderr_or_msg)."""
    try:
        proc = subprocess.run(
            install_cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return None, "timeout during install"
    return proc.returncode, (proc.stderr or proc.stdout)


def run_variant(variant, python_version, packages, repo_root, results_dir, timeouts):
    astropy, pkg_specs = resolve_specs(packages, variant)
    print(f"\n=== Variant: {variant} (Python {python_version}) ===")
    print(f"  astropy: {astropy['install']}")
    for pkg, spec, target in pkg_specs:
        print(f"  {pkg['pypi_name']:<20} {spec or '(no install spec)'}")

    result = {
        "variant": variant,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "finished_at": "",
        "astropy": {
            "install_spec": astropy["install"],
            "version": astropy["version"],
            "extra_index_urls": astropy["extra_index_urls"],
            "prerelease_strategy": astropy["prerelease_strategy"],
        },
        "python_requested": python_version,
        "python_version": "",
        "timeout_test_seconds": timeouts["test"],
        "pytest_limit_n": (
            int(os.environ["PYTEST_LIMIT_N"])
            if (os.environ.get("PYTEST_LIMIT_N") or "").isdigit()
            else None
        ),
        "fatal_error": "",
        "installed_deps": {},
        "packages": [],
    }
    out_path = results_dir / f"{variant}__{python_version}.json"

    Path(repo_root, ".tmp").mkdir(exist_ok=True)
    tmpdir = tempfile.mkdtemp(
        prefix=f"int-{variant}-{python_version}-",
        dir=str(Path(repo_root, ".tmp").resolve()),
    )

    try:
        py_path = ensure_python(python_version)
        venv = os.path.join(tmpdir, "venv")
        venv_proc = subprocess.run(
            ["uv", "venv", venv, "-p", py_path, "-q"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if venv_proc.returncode != 0:
            result["fatal_error"] = f"uv venv: {venv_proc.stderr or venv_proc.stdout}"
            return result, out_path
        python = os.path.join(venv, "bin", "python")
        result["python_version"] = _venv_python_version(python)
        constraints_path = os.path.join(tmpdir, "no-downgrade-constraints.txt")

        common = ["uv", "pip", "install", "--python", python, "-q"]
        for url in astropy["extra_index_urls"]:
            common += ["--extra-index-url", url]
        common += [f"--prerelease={astropy['prerelease_strategy']}"]
        if astropy.get("index_strategy"):
            common += [f"--index-strategy={astropy['index_strategy']}"]

        print("\nInstalling astropy + pytest...")
        # pytest-remotedata registers the `remote_data` marker many
        # astropy ecosystem packages use; with the plugin installed but
        # `--remote-data` not passed, those tests are skipped automatically
        # instead of running and timing out on network calls.
        rc, err = _run_install(
            common
            + [astropy["install"], "pytest", "pytest-timeout", "pytest-remotedata"],
            timeouts["install"],
        )
        if rc != 0:
            print("  FATAL: astropy install failed")
            print(err)
            result["fatal_error"] = err
            return result, out_path
        result["astropy"]["version"] = (
            _pkg_version(python, "astropy") or result["astropy"]["version"]
        )
        _write_no_downgrade_constraints(python, constraints_path)

        installed_pkgs = []
        for pkg, install_spec, target_version in pkg_specs:
            entry = {
                "name": pkg["pypi_name"],
                "tier": pkg.get("tier", "coordinated"),
                "module": pkg.get("module", pkg["pypi_name"]),
                "install_spec": install_spec,
                "target_version": target_version,
                "resolved_version": "",
                "install_status": "",  # installed | skipped | install-fail | no-spec
                "install_error": "",
                "test_status": "",  # pass | fail | no-tests | timeout | not-run
                "test_output": "",
                "duration": 0,
            }
            if install_spec is None:
                entry["install_status"] = status.NO_SPEC
                entry["install_error"] = (
                    "no install spec (missing repo_url, or no PyPI release)"
                )
                result["packages"].append(entry)
                continue

            print(f"\nInstalling {pkg['pypi_name']}...")
            install_cmd = (
                common
                + ["--constraint", constraints_path, install_spec]
                + (pkg.get("extra_deps") or [])
            )
            rc, err = _run_install(install_cmd, timeouts["install"])
            if rc == 0:
                entry["install_status"] = status.INSTALLED
                entry["resolved_version"] = _pkg_version(python, pkg["pypi_name"])
                print(f"  installed at {entry['resolved_version']}")
                installed_pkgs.append((pkg, entry))
                _write_no_downgrade_constraints(python, constraints_path)
            else:
                entry["install_error"] = err
                if _resolver_conflict(err):
                    entry["install_status"] = status.SKIPPED
                    print("  skipped (resolver conflict)")
                else:
                    entry["install_status"] = status.INSTALL_FAIL
                    print("  install failed")
                print(err)
            result["packages"].append(entry)

        result["installed_deps"] = _freeze(python)

        for pkg, entry in installed_pkgs:
            print(f"\nTesting {pkg['pypi_name']}...")
            module = entry["module"]
            cmd = [python, "-m", "pytest", "--pyargs", module]
            cmd += pkg.get("pytest_args", [])
            cmd += ["--timeout=120", "-q", "--tb=line", "--no-header"]

            env = {**os.environ, "MPLBACKEND": "Agg", "DISPLAY": ""}
            start = time.time()
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeouts["test"],
                    cwd=repo_root,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                entry["test_status"] = status.TIMEOUT
                entry["test_output"] = "timeout"
                entry["duration"] = round(time.time() - start, 1)
                print(f"  timeout after {entry['duration']}s")
                continue
            entry["duration"] = round(time.time() - start, 1)
            out = proc.stdout
            if proc.stderr:
                out += "\n--- stderr ---\n" + proc.stderr
            entry["test_output"] = out

            no_module = proc.returncode == 4 and "module or package not found" in (
                proc.stdout + proc.stderr
            )
            if proc.returncode == 5 or no_module:
                entry["test_status"] = status.NO_TESTS
            elif proc.returncode == 0:
                entry["test_status"] = status.PASS
            else:
                entry["test_status"] = status.FAIL
            print(f"  {entry['test_status']} in {entry['duration']}s")

    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out_path.write_text(json.dumps(result, indent=2))
        shutil.rmtree(tmpdir, ignore_errors=True)

    return result, out_path


def _counts(result):
    install = Counter(e["install_status"] for e in result["packages"])
    test = Counter(e["test_status"] for e in result["packages"] if e["test_status"])
    return {"install": dict(install), "test": dict(test)}


def add_arguments(ap):
    ap.add_argument("--config", default="packages.yaml")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument(
        "--variant",
        choices=status.VARIANTS,
        help="Variant to run; if omitted, runs all variants in sequence.",
    )
    ap.add_argument(
        "--python",
        help="Python version to run against (e.g. '3.12', '3.14t'); "
        "if omitted, runs every version listed in the config.",
    )
    ap.add_argument("--packages", help="Comma-separated subset of package names to run")
    ap.add_argument(
        "--tiers",
        help="Comma-separated subset of tiers to run (e.g. 'coordinated,other'); "
        "default: all tiers",
    )
    ap.add_argument("--timeout-install", type=int, default=900)
    ap.add_argument("--timeout-test", type=int, default=1800)


def run(args):
    # Make every print flush immediately so CI streams progress live
    # instead of buffering until the script exits.
    sys.stdout.reconfigure(line_buffering=True)

    all_packages = _load_packages(args.config)
    packages = all_packages
    if args.tiers:
        wanted_tiers = {t.strip() for t in args.tiers.split(",") if t.strip()}
        packages = [p for p in packages if p.get("tier", "coordinated") in wanted_tiers]
    if args.packages:
        wanted = {n.strip() for n in args.packages.split(",") if n.strip()}
        known = {p["pypi_name"] for p in all_packages}
        unknown = wanted - known
        if unknown:
            sys.exit(
                f"Unknown package name(s): {', '.join(sorted(unknown))}. "
                f"Valid names are the pypi_name entries in {args.config}."
            )
        packages = [p for p in packages if p["pypi_name"] in wanted]
    packages = _install_order(packages)

    repo_root = Path.cwd()
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    timeouts = {"install": args.timeout_install, "test": args.timeout_test}
    variants_to_run = [args.variant] if args.variant else list(status.VARIANTS)
    pythons_to_run = (
        [args.python] if args.python else _load_python_versions(args.config)
    )

    fatal_combos = []
    for python_version in pythons_to_run:
        for variant in variants_to_run:
            result, out_path = run_variant(
                variant, python_version, packages, repo_root, results_dir, timeouts
            )
            print(f"\nDone {variant}/{python_version}: {_counts(result)}")
            print(f"Wrote {out_path}")
            if result.get("fatal_error"):
                fatal_combos.append(f"{variant}/{python_version}")

    if fatal_combos:
        sys.exit(f"\nAstropy install failed for: {', '.join(fatal_combos)}")
