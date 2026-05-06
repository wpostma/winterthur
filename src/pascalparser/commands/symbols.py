"""``pascalparser symbols`` — dump symbols and imports.

For a list of source files, run :class:`pascalparser.parser.ASTParser`
and emit each file's symbols + imports either as text (default) or as
JSON. Useful as a sanity check that the parser+queries combination is
extracting what you expect.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from ..io_helpers import file_info_from_path
from ..parser import ASTParser


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "symbols",
        help="Dump symbols and imports for one or more files",
    )
    sub.add_argument("files", nargs="+", help="Source files")
    sub.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    sub.add_argument(
        "--skip-implementation",
        action="store_true",
        help=(
            "Pascal only: strip everything after the 'implementation' keyword "
            "before parsing (fast-symbol-graph mode; default off)."
        ),
    )
    sub.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    parser = ASTParser(skip_implementation=args.skip_implementation)
    output: list[dict] = []
    failures = 0

    for raw_path in args.files:
        path = Path(raw_path)
        try:
            info, source = file_info_from_path(path)
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            failures += 1
            continue

        parsed = parser.parse_file(info, source)
        record = {
            "file": info.path,
            "language": info.language,
            "symbols": [_symbol_to_dict(s) for s in parsed.symbols],
            "imports": [_import_to_dict(i) for i in parsed.imports],
            "errors": list(parsed.parse_errors),
        }

        if args.json:
            output.append(record)
        else:
            _print_text(record)

    if args.json:
        json.dump({"files": output}, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")

    return 1 if failures else 0


def _symbol_to_dict(sym) -> dict:
    d = asdict(sym)
    # Drop fields that aren't useful in the symbol dump.
    for k in ("docstring",):
        d.pop(k, None)
    return d


def _import_to_dict(imp) -> dict:
    return asdict(imp)


def _print_text(record: dict) -> None:
    print(f"\n{record['file']} ({record['language']})")
    if record["errors"]:
        print(f"  errors: {len(record['errors'])}")
    syms = record["symbols"]
    if syms:
        print(f"  symbols ({len(syms)}):")
        for s in syms:
            kind = s.get("kind", "?")
            qname = s.get("qualified_name") or s.get("name", "?")
            line = s.get("start_line", "?")
            print(f"    {line:>5}  {kind:<12} {qname}")
    imps = record["imports"]
    if imps:
        print(f"  imports ({len(imps)}):")
        for i in imps:
            print(f"    {i.get('module_path', '?')}")
