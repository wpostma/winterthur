"""Top-level CLI entry point.

Designed to be invoked via ``uvx winterthur …`` or
``pipx install winterthur`` followed by ``winterthur …`` on PATH.
The CLI is the contract; the importable Python API is a side benefit.

Subcommands live in :mod:`winterthur.commands`. Each registers its
own argparse subparser and a ``run(args) -> int`` handler. To add a
new subcommand:

1. Drop a module into ``commands/``.
2. Implement ``register(subparsers)`` and ``run(args) -> int``.
3. Append it to ``commands.ALL_COMMANDS``.

No edits to this file required.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__
from .commands import ALL_COMMANDS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="winterthur",
        description=(
            "Pascal/Delphi parser plus lint and metrics tooling. "
            "Single-unit-of-compilation scanning via tree-sitter — "
            "no .dproj, no compile, no .dcu resolution."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"winterthur {__version__}",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        metavar="COMMAND",
        required=True,
    )
    for module in ALL_COMMANDS:
        module.register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
