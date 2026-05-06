"""``pascalparser smells`` — pattern-based smell detection.

Stub: not yet implemented. Will host the pattern detectors documented
in ``~/.claude/skills/codereview/smells.md`` (silent exits, R2/R3/R4
violations, swallowed exceptions, SQL string interpolation, recursion
without obvious base case, etc.).

Until this module is real, the codereview skill should run with
metric-only output.
"""

from __future__ import annotations

import argparse
import sys


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "smells",
        help="Pattern-based smell findings (NOT YET IMPLEMENTED)",
    )
    sub.add_argument("files", nargs="+", help="Source files")
    sub.add_argument("--json", action="store_true", help="Emit JSON output")
    sub.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    print(
        "pascalparser smells: not yet implemented.\n"
        "  See ~/.claude/skills/codereview/smells.md for the planned rule set.\n"
        "  Track progress in the project README.",
        file=sys.stderr,
    )
    return 2
