"""CLI subcommand modules.

Each module exposes:

- ``register(subparsers)`` — adds the subcommand's argparse subparser.
- ``run(args) -> int`` — does the work, returns an exit code.

The dispatcher in :mod:`pascalparser.cli` enumerates this package's
modules to assemble the top-level parser.
"""

from __future__ import annotations

from . import declaration, doctor, metrics, parse, smells, symbols

ALL_COMMANDS = (doctor, declaration, metrics, parse, smells, symbols)

__all__ = [
    "ALL_COMMANDS",
    "declaration",
    "doctor",
    "metrics",
    "parse",
    "smells",
    "symbols",
]
