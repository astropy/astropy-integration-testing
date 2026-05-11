#!/usr/bin/env python
"""Run one variant of the astropy ecosystem integration matrix.

Creates a single shared venv, installs astropy first then each package
from packages.yaml in order, recording per-package install outcome
(installed / skipped / install-fail / no-spec). Then runs
`pytest --pyargs <module>` for each successfully-installed package.
Writes results/<variant>.json with the full venv freeze and per-package
data.

Usage:
    python run_integration.py --variant stable
    python run_integration.py --variant latest
    python run_integration.py --variant dev
    python run_integration.py --variant stable --packages reproject,sunpy
    python run_integration.py --variant stable --tiers coordinated
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


try:
    import yaml
    from packaging.version import Version, InvalidVersion
except ImportError:
    sys.exit("This script requires 'pyyaml' and 'packaging'. "
             "Install with: pip install pyyaml packaging")

import status


PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
ASTROPY_NIGHTLY_INDEX = "https://pypi.anaconda.org/astropy/simple"
PYTHON_VERSION = "3.12"


def _http_json(url, timeout=20):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _http_text(url, timeout=20):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


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


def latest_astropy_nightly():
    """Return the latest astropy version from the astropy/simple channel."""
    html = _http_text(f"{ASTROPY_NIGHTLY_INDEX}/astropy/")
    versions = set()
    for href in re.findall(r'href="([^"]+\.whl)"', html):
        fname = href.rsplit("/", 1)[-1]
        parts = fname[: -len(".whl")].split("-")
        if len(parts) >= 2 and parts[0].lower() == "astropy":
            versions.add(parts[1])
    if not versions:
        sys.exit("No astropy nightly wheels found on the astropy/simple channel.")
    return sorted(versions, key=_version_key)[-1]


def _extras_suffix(pkg):
    extras = pkg.get("install_extras") or []
    return "[" + ",".join(extras) + "]" if extras else ""


def resolve_specs(packages, variant):
    """Resolve the astropy spec and per-package install specs for the variant."""
    if variant == "dev":
        astropy_ver = latest_astropy_nightly()
        astropy = {
            "install": f"astropy=={astropy_ver}",
            "version": astropy_ver,
            "extra_index_urls": [ASTROPY_NIGHTLY_INDEX],
            "prerelease_strategy": "if-necessary-or-explicit",
        }

        def pkg_spec(pkg):
            repo = pkg.get("repo_url")
            if not repo:
                return None, None
            return f"{pkg['pypi_name']}{_extras_suffix(pkg)} @ git+{repo}", None

    else:
        include_pre = (variant == "latest")
        astropy_ver = latest_pypi("astropy", include_prereleases=include_pre)
        astropy = {
            "install": f"astropy=={astropy_ver}",
            "version": astropy_ver,
            "extra_index_urls": [],
            "prerelease_strategy": "allow" if include_pre else "if-necessary-or-explicit",
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
    proc = subprocess.run(["uv", "python", "find", version],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode == 0:
        return proc.stdout.strip()
    inst = subprocess.run(["uv", "python", "install", version],
                          capture_output=True, text=True, timeout=600)
    if inst.returncode != 0:
        sys.exit(f"uv python install {version}: {inst.stderr.strip()}")
    proc = subprocess.run(["uv", "python", "find", version],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        sys.exit(f"uv python find {version}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _venv_python_version(python):
    proc = subprocess.run(
        [python, "-c", "import sys; print('.'.join(str(x) for x in sys.version_info[:3]))"],
        capture_output=True, text=True, timeout=30,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _pkg_version(python, name):
    proc = subprocess.run(
        [python, "-c",
         "import importlib.metadata as md, sys; print(md.version(sys.argv[1]))",
         name],
        capture_output=True, text=True, timeout=30,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _freeze(python):
    proc = subprocess.run(["uv", "pip", "freeze", "--python", python],
                          capture_output=True, text=True, timeout=60)
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


def _load_packages(path):
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return list(raw.get("packages", []))


# Ordering of tiers when installing/displaying. Unknown tiers sort last.
TIER_RANK = {"coordinated": 0, "affiliated": 1, "other": 2}


def _install_order(packages):
    """Coordinated before affiliated before other, alphabetical within each tier."""
    return sorted(packages, key=lambda p: (
        TIER_RANK.get(p.get("tier", "coordinated"), 9),
        p["pypi_name"].lower(),
    ))


def _run_install(install_cmd, timeout):
    """Wrap subprocess.run with TimeoutExpired catch. Returns (rc, stderr_or_msg)."""
    try:
        proc = subprocess.run(install_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout during install"
    return proc.returncode, (proc.stderr or proc.stdout)


def run_variant(variant, packages, repo_root, results_dir, timeouts):
    astropy, pkg_specs = resolve_specs(packages, variant)
    print(f"\n=== Variant: {variant} ===")
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
        "python_version": "",
        "fatal_error": "",
        "installed_deps": {},
        "packages": [],
    }
    out_path = results_dir / f"{variant}.json"

    Path(repo_root, ".tmp").mkdir(exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix=f"int-{variant}-",
                              dir=str(Path(repo_root, ".tmp").resolve()))

    try:
        py_path = ensure_python(PYTHON_VERSION)
        venv = os.path.join(tmpdir, "venv")
        venv_proc = subprocess.run(["uv", "venv", venv, "-p", py_path, "-q"],
                                   capture_output=True, text=True, timeout=120)
        if venv_proc.returncode != 0:
            result["fatal_error"] = f"uv venv: {venv_proc.stderr or venv_proc.stdout}"
            return result, out_path
        python = os.path.join(venv, "bin", "python")
        result["python_version"] = _venv_python_version(python)

        common = ["uv", "pip", "install", "--python", python, "-q"]
        for url in astropy["extra_index_urls"]:
            common += ["--extra-index-url", url]
        common += [f"--prerelease={astropy['prerelease_strategy']}"]

        print("\nInstalling astropy + pytest...")
        rc, err = _run_install(common + [astropy["install"], "pytest", "pytest-timeout"],
                               timeouts["install"])
        if rc != 0:
            print("  FATAL: astropy install failed")
            result["fatal_error"] = err
            return result, out_path
        result["astropy"]["version"] = _pkg_version(python, "astropy") or result["astropy"]["version"]

        installed_pkgs = []
        for pkg, install_spec, target_version in pkg_specs:
            entry = {
                "name": pkg["pypi_name"],
                "tier": pkg.get("tier", "coordinated"),
                "module": pkg.get("module", pkg["pypi_name"]),
                "install_spec": install_spec,
                "target_version": target_version,
                "resolved_version": "",
                "install_status": "",       # installed | skipped | install-fail | no-spec
                "install_error": "",
                "test_status": "",          # pass | fail | no-tests | timeout | not-run
                "tests_passed": None,
                "test_output": "",
                "duration": 0,
            }
            if install_spec is None:
                entry["install_status"] = status.NO_SPEC
                entry["install_error"] = "no install spec (missing repo_url, or no PyPI release)"
                result["packages"].append(entry)
                continue

            print(f"\nInstalling {pkg['pypi_name']}...")
            install_cmd = common + [install_spec] + (pkg.get("extra_deps") or [])
            rc, err = _run_install(install_cmd, timeouts["install"])
            if rc == 0:
                entry["install_status"] = status.INSTALLED
                entry["resolved_version"] = _pkg_version(python, pkg["pypi_name"])
                print(f"  installed at {entry['resolved_version']}")
                installed_pkgs.append((pkg, entry))
            else:
                entry["install_error"] = err
                if _resolver_conflict(err):
                    entry["install_status"] = status.SKIPPED
                    print("  skipped (resolver conflict)")
                else:
                    entry["install_status"] = status.INSTALL_FAIL
                    print("  install failed")
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
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=timeouts["test"], cwd=repo_root, env=env)
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

            no_module = (
                proc.returncode == 4
                and "module or package not found" in (proc.stdout + proc.stderr)
            )
            if proc.returncode == 5 or no_module:
                entry["test_status"] = status.NO_TESTS
            elif proc.returncode == 0:
                entry["test_status"] = status.PASS
                entry["tests_passed"] = True
            else:
                entry["test_status"] = status.FAIL
                entry["tests_passed"] = False
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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="packages.yaml")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--variant", choices=status.VARIANTS, required=True)
    ap.add_argument("--packages", help="Comma-separated subset of package names to run")
    ap.add_argument("--tiers",
                    help="Comma-separated subset of tiers to run (e.g. 'coordinated,other'); "
                         "default: all tiers")
    ap.add_argument("--timeout-install", type=int, default=900)
    ap.add_argument("--timeout-test", type=int, default=1800)
    args = ap.parse_args()

    packages = _load_packages(args.config)
    if args.tiers:
        wanted_tiers = {t.strip() for t in args.tiers.split(",") if t.strip()}
        packages = [p for p in packages if p.get("tier", "coordinated") in wanted_tiers]
    if args.packages:
        wanted = {n.strip() for n in args.packages.split(",") if n.strip()}
        packages = [p for p in packages if p["pypi_name"] in wanted]
    packages = _install_order(packages)

    repo_root = Path.cwd()
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    timeouts = {"install": args.timeout_install, "test": args.timeout_test}
    result, out_path = run_variant(args.variant, packages, repo_root, results_dir, timeouts)
    print(f"\nDone: {_counts(result)}")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
