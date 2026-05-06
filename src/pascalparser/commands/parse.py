"""``pascalparser parse`` — debug aid.

Dumps the raw tree-sitter AST (or just the high-level shape) for a
single source file, primarily for grammar coverage debugging. Not
intended as machine-readable output — use :mod:`commands.symbols` or
:mod:`commands.metrics` for that.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..io_helpers import file_info_from_path
from ..parser import _get_language


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "parse",
        help="Dump the tree-sitter AST for a single file (debug aid)",
    )
    sub.add_argument("file", help="Source file to parse")
    sub.add_argument(
        "--depth",
        type=int,
        default=4,
        help="Maximum tree depth to print (default: 4)",
    )
    sub.add_argument(
        "--errors-only",
        action="store_true",
        help="Print only nodes flagged as errors by the grammar",
    )
    sub.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from tree_sitter import Parser

    file_info, source = file_info_from_path(Path(args.file))
    lang = _get_language(file_info.language)
    if lang is None:
        print(
            f"error: no tree-sitter grammar loaded for {file_info.language!r}",
            file=sys.stderr,
        )
        return 2

    parser = Parser()
    parser.language = lang
    tree = parser.parse(source)
    root = tree.root_node

    if args.errors_only:
        errors = list(_iter_error_nodes(root))
        if not errors:
            print("(no error nodes)")
            return 0
        for node in errors:
            _print_node(node, source, depth=0)
        return 0 if not root.has_error else 1

    _print_node(root, source, depth=0, max_depth=args.depth)
    return 0 if not root.has_error else 1


def _print_node(node, source: bytes, *, depth: int, max_depth: int = 4) -> None:
    indent = "  " * depth
    text = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
    snippet = text.splitlines()[0][:60] if text else ""
    line = (
        f"{indent}{node.type} "
        f"[{node.start_point[0] + 1}:{node.start_point[1]}-"
        f"{node.end_point[0] + 1}:{node.end_point[1]}]"
    )
    if snippet:
        line += f"  '{snippet}'"
    if node.has_error:
        line += "  <ERROR>"
    print(line)
    if depth >= max_depth:
        if node.child_count:
            print(f"{indent}  … ({node.child_count} children, depth limit)")
        return
    for child in node.children:
        _print_node(child, source, depth=depth + 1, max_depth=max_depth)


def _iter_error_nodes(node):
    if node.is_error or node.is_missing:
        yield node
    for child in node.children:
        yield from _iter_error_nodes(child)
