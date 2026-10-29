"""`python -m tools <tool> <run> ...`: one subcommand per tool, each from its package's command module.

A command module defines NAME, HELP, add_arguments(parser) and run(args) -> int (0 ok, 1 failure, 2 usage or refused).
"""
from __future__ import annotations

import argparse

from tools.contagion import contagion_command as contagion
from tools.docent import docent_command as docent
from tools.failures import failures_command as failures
from tools.graphs import graphs_command as graphs
from tools.hawk import hawk_command as hawk
from tools.scout import scout_command as scout
from tools.transect import transect_command as transect

COMMANDS = (scout, docent, graphs, transect, contagion, failures, hawk)


def build_parser() -> argparse.ArgumentParser:
    """The parser with one subcommand per tool."""
    parser = argparse.ArgumentParser(prog="python -m tools", description="Analysis tools over the exported runs.")
    subparsers = parser.add_subparsers(dest="tool", required=True, metavar="tool")
    for module in COMMANDS:
        subparser = subparsers.add_parser(module.NAME, help=module.HELP, description=module.HELP)
        module.add_arguments(subparser)
        subparser.set_defaults(func=module.run)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse the arguments and run the chosen tool; returns its exit code."""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 2
    return args.func(args)
