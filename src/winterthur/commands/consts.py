"""``winterthur consts`` — dump constant declarations from a file.

Const-only Pascal units (``unit foo.consts.bar; const … end.``) yield no
records from ``symbols`` or ``metrics`` — those tools target callable
shapes. This command fills the gap: list every ``declConst`` in a file
with its line, name, and full declaration text, optionally filtered by
a glob or regex.

Pattern syntax matches :mod:`declaration`: glob by default
(``bt_*``, ``MAX_*``), pass ``--regex`` for full Python regex with
``re.fullmatch`` semantics. Case-insensitive unless ``--case-sensitive``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ..io_helpers import file_info_from_path
from ..parser import _get_language
from .declaration import _glob_to_regex


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "consts",
        help="Dump constant declarations from a file",
    )
    sub.add_argument("file", help="Source file")
    sub.add_argument(
        "pattern",
        nargs="?",
        default=None,
        help=(
            "Optional name pattern (glob by default — '*' / '?' wildcards). "
            "If omitted, every constant is dumped (up to --limit). "
            "Pass --regex to switch to Python regex with re.fullmatch."
        ),
    )
    sub.add_argument(
        "--regex",
        action="store_true",
        help="Treat the pattern as a Python regex instead of a glob.",
    )
    sub.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Make the pattern case-sensitive (default: case-insensitive).",
    )
    sub.add_argument(
        "--limit",
        type=int,
        default=200,
        metavar="N",
        help="Cap how many constants to print (default 200).",
    )
    sub.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        file_info, source = file_info_from_path(Path(args.file))
    except FileNotFoundError:
        print(f"error: file not found: {args.file}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    pattern: re.Pattern | None = None
    pattern_label = "all"
    if args.pattern is not None:
        flags = 0 if args.case_sensitive else re.IGNORECASE
        regex_source = (
            args.pattern if args.regex else _glob_to_regex(args.pattern)
        )
        try:
            pattern = re.compile(regex_source, flags)
        except re.error as exc:
            kind = "regex" if args.regex else "glob"
            print(f"error: invalid {kind} {args.pattern!r}: {exc}", file=sys.stderr)
            return 2
        pattern_label = (
            f"/{args.pattern}/" if args.regex else f"glob '{args.pattern}'"
        )

    ts_language = _get_language(file_info.language)
    if ts_language is None:
        print(
            f"error: no tree-sitter grammar loaded for {file_info.language!r}",
            file=sys.stderr,
        )
        return 2

    from tree_sitter import Parser as _Parser
    ts_parser = _Parser(ts_language)
    tree = ts_parser.parse(source)

    all_consts = list(_iter_consts(tree.root_node, source, file_info.language))

    if pattern is not None:
        matched = [c for c in all_consts if pattern.fullmatch(c["name"])]
    else:
        matched = list(all_consts)

    if not matched:
        flag = "" if args.case_sensitive else " (case-insensitive)"
        if pattern is None:
            print(f"no constants found in {file_info.path}", file=sys.stderr)
        else:
            print(
                f"no constants matched {pattern_label}{flag} in {file_info.path}",
                file=sys.stderr,
            )
        return 1

    capped = matched[: args.limit]

    # Header tells the consumer the haystack size — handy when you're
    # asking "is this a 17-const file or a 1700-const file?" and shaping
    # follow-up queries.
    if pattern is not None:
        print(
            f"# {file_info.path}: {len(capped)} of {len(all_consts)} "
            f"constants matching {pattern_label}"
            + ("" if args.case_sensitive else " (case-insensitive)")
        )
    else:
        print(f"# {file_info.path}: {len(capped)} of {len(all_consts)} constants")

    for c in capped:
        print(f"  {c['line']:>5}  {c['text']}")

    if len(matched) > args.limit:
        print(
            f"  ... ({len(matched) - args.limit} more matches; raise --limit to see)"
        )

    if tree.root_node.has_error:
        from .symbols import PARSE_ERROR_DISCLAIMER
        print(PARSE_ERROR_DISCLAIMER, file=sys.stderr)

    return 0


def _iter_consts(root, source: bytes, language: str):
    """Yield {name, line, text} dicts for every constant declaration.

    Pascal: ``declConst`` nodes — one per ``Foo = expr;`` entry within a
    ``const`` block. Other languages can be added when their grammars
    expose an analogous node (Python ``assignment`` at module level
    with an UPPER_CASE LHS would be the obvious heuristic; we don't
    do that yet — Pascal is the primary target for now).
    """
    if language != "pascal":
        return  # other-language consts not implemented
    for n in _walk(root):
        if n.type != "declConst":
            continue
        name = _first_identifier_text(n, source)
        if name is None:
            continue
        text = (
            source[n.start_byte:n.end_byte]
            .decode("utf-8", errors="replace")
            .replace("\r\n", "\n")
            .strip()
        )
        yield {"name": name, "line": n.start_point[0] + 1, "text": text}


def _walk(node):
    yield node
    for c in node.children:
        yield from _walk(c)


def _first_identifier_text(node, source: bytes) -> str | None:
    for c in node.children:
        if c.type == "identifier":
            return source[c.start_byte:c.end_byte].decode("utf-8", errors="replace")
    return None
