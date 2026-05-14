"""Console entry point: dispatches `run` and `dashboard` subcommands."""

import argparse

from . import dashboard, run


def main():
    parser = argparse.ArgumentParser(prog="astropy-integration")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser(
        "run",
        help="Run one or more (variant, python) cells of the matrix.",
        description=run.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run.add_arguments(run_p)

    dash_p = sub.add_parser(
        "dashboard",
        help="Build the dashboard HTML from results/*.json.",
        description=dashboard.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    dashboard.add_arguments(dash_p)

    args = parser.parse_args()
    if args.cmd == "run":
        run.run(args)
    elif args.cmd == "dashboard":
        dashboard.run(args)
