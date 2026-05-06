"""``pascalparser doctor`` — verify the install is functional.

Loads each tree-sitter language registered in :mod:`pascalparser.parser`,
parses a one-line snippet per language as a smoke test, and reports
versions. Exit code 0 if everything works; 1 if any language fails.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import sys

from ..parser import LANGUAGE_CONFIGS, _get_language

# Tiny snippets that should parse cleanly per language.
_SMOKE_SNIPPETS: dict[str, bytes] = {
    "python": b"x = 1\n",
    "typescript": b"const x: number = 1;\n",
    "javascript": b"const x = 1;\n",
    "go": b"package main\n",
    "rust": b"fn main() {}\n",
    "java": b"class A {}\n",
    "c": b"int main(void) { return 0; }\n",
    "cpp": b"int main() { return 0; }\n",
    "pascal": b"program A; begin end.\n",
}


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "doctor",
        help="Verify tree-sitter grammars load and parse a smoke snippet",
    )
    sub.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    from tree_sitter import Parser

    print("pascalparser doctor")
    try:
        version = importlib.metadata.version("pascalparser")
    except importlib.metadata.PackageNotFoundError:
        version = "(dev, not installed)"
    print(f"  pascalparser version: {version}")

    failures = 0
    for tag in sorted(LANGUAGE_CONFIGS):
        lang = _get_language(tag)
        if lang is None:
            print(f"  [FAIL] {tag}: language module not loaded")
            failures += 1
            continue
        snippet = _SMOKE_SNIPPETS.get(tag)
        if snippet is None:
            print(f"  [SKIP] {tag}: no smoke snippet defined")
            continue
        parser = Parser()
        parser.language = lang
        tree = parser.parse(snippet)
        if tree.root_node.has_error:
            print(f"  [WARN] {tag}: snippet parsed with errors")
        else:
            print(f"  [ ok ] {tag}")

    if failures:
        print(f"\n{failures} language(s) failed to load.", file=sys.stderr)
        return 1
    return 0
