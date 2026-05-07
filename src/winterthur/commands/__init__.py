"""CLI subcommand modules.

Each module exposes:

- ``register(subparsers)`` — adds the subcommand's argparse subparser.
- ``run(args) -> int`` — does the work, returns an exit code.

The dispatcher in :mod:`winterthur.cli` enumerates this package's
modules to assemble the top-level parser.
"""

from __future__ import annotations

from . import consts, declaration, doctor, metrics, parse, smells, symbols

ALL_COMMANDS = (consts, declaration, doctor, metrics, parse, smells, symbols)

__all__ = [
    "ALL_COMMANDS",
    "consts",
    "declaration",
    "doctor",
    "metrics",
    "parse",
    "smells",
    "symbols",
]
