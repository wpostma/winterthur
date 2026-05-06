"""``pascalparser parse`` — depth-limited file view.

Default output: the source code itself, with bodies of nodes deeper than
``--depth`` folded into language-comment elisions like
``# ... (lines 29-50 elided)``. Notable nodes (tree-sitter ERROR /
missing-token recovery) are tagged with a ``! ERROR !`` annotation in the
same comment style.

``--debug`` switches to the raw AST dump (every node, its line range,
its first 60 chars of source) for grammar coverage debugging — that's
the original behaviour, kept around because the source view doesn't
expose node-type names and you sometimes need them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..io_helpers import file_info_from_path
from ..parser import _get_language

# Per-language line-comment prefix used for elision and error markers.
# Pascal also supports {…} and (*…*) block comments, but the // form has
# been universal since Delphi 2 and is the least surprising in folded code.
_COMMENT_STYLES: dict[str, str] = {
    "python": "# ",
    "go": "// ",
    "rust": "// ",
    "typescript": "// ",
    "javascript": "// ",
    "java": "// ",
    "c": "// ",
    "cpp": "// ",
    "pascal": "// ",
}

# Tree-sitter node types that carry a "body" (and thus are elidable).
# We use the body child's line range, not the parent's, so the signature
# line(s) — `def foo(...):` — stay visible.
_BODY_CHILD_TYPES = frozenset({
    "block",                 # python, pascal
    "suite",                 # python (older grammars)
    "compound_statement",    # c-family
    "function_body",         # rust, go (variant)
    "class_body",            # java, ts
    "implementation",        # pascal
    "interface",             # pascal
})


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "parse",
        help="Show a folded source view of a single file (debug aid)",
    )
    sub.add_argument("file", help="Source file to parse")
    sub.add_argument(
        "--depth",
        type=int,
        default=1,
        help=(
            "Fold node bodies deeper than this. Default 1: keep top-level "
            "signatures, elide their bodies."
        ),
    )
    sub.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Dump the raw tree-sitter AST instead of the folded source view "
            "(node types, line ranges, snippets). Useful for grammar coverage "
            "debugging."
        ),
    )
    sub.add_argument(
        "--errors-only",
        action="store_true",
        help=(
            "Only print nodes flagged as errors by the grammar. Implies "
            "--debug; the source view doesn't have a coherent error-only mode."
        ),
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
        return _run_errors_only(root, source)

    if args.debug:
        _print_node(root, source, depth=0, max_depth=args.depth)
        return 0 if not root.has_error else 1

    print(_render_source_view(root, source, file_info.language, args.depth))
    return 0 if not root.has_error else 1


# --------------------------------------------------------------------------- #
# Default mode: folded source view
# --------------------------------------------------------------------------- #


def _render_source_view(root, source: bytes, language: str, max_depth: int) -> str:
    """Return the source with depth-limited bodies folded into comment elisions."""
    lines = source.decode("utf-8", errors="replace").splitlines()
    comment = _COMMENT_STYLES.get(language, "// ")

    elisions: list[tuple[int, int]] = []   # 1-indexed inclusive ranges
    error_lines: dict[int, str] = {}        # 1-indexed line -> message

    def walk(node, depth: int) -> None:
        if node.type == "ERROR":
            error_lines.setdefault(
                node.start_point[0] + 1, "tree-sitter could not parse this region"
            )
        if node.is_missing:
            error_lines.setdefault(
                node.start_point[0] + 1, f"missing {node.type!r}"
            )

        if depth >= max_depth:
            elide = _elide_range_for(node)
            if elide is not None:
                elisions.append(elide)
            return  # don't descend past max_depth

        for child in node.children:
            walk(child, depth + 1)

    walk(root, 0)

    elisions = _merge_overlapping(sorted(elisions))

    out: list[str] = []
    line_no = 1
    elide_iter = iter(elisions)
    next_elide = next(elide_iter, None)
    n_lines = len(lines)

    while line_no <= n_lines:
        if next_elide is not None and line_no == next_elide[0]:
            first, last = next_elide
            indent = _leading_indent(lines[first - 1])
            span = (
                f"line {first} elided"
                if first == last
                else f"lines {first}-{last} elided"
            )
            out.append(f"{indent}{comment}... ({span})")
            line_no = last + 1
            next_elide = next(elide_iter, None)
            continue

        text = lines[line_no - 1]
        if line_no in error_lines:
            text = f"{text}  {comment}! ERROR ! {error_lines[line_no]}"
        out.append(text)
        line_no += 1

    return "\n".join(out)


def _elide_range_for(node) -> tuple[int, int] | None:
    """Return (first_line, last_line) of *node*'s body, or None.

    Skips single-line nodes (nothing to elide). Prefers the largest 'body'
    child so the signature line(s) stay visible. Falls back to "everything
    after the first line" when no body child is recognisable.
    """
    if node.end_point[0] <= node.start_point[0]:
        return None  # single-line construct

    body_child = None
    for c in node.children:
        if c.type in _BODY_CHILD_TYPES:
            # Pick the largest body child if there are several (Pascal: a
            # `unit` has both `interface` and `implementation`).
            if body_child is None or (
                c.end_point[0] - c.start_point[0]
                > body_child.end_point[0] - body_child.start_point[0]
            ):
                body_child = c

    if body_child is not None:
        first = body_child.start_point[0] + 1
        last = body_child.end_point[0] + 1
        if last < first:
            return None
        return first, last

    # No body child — elide everything after the first source line.
    return node.start_point[0] + 2, node.end_point[0] + 1


def _merge_overlapping(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent (start, end) ranges (1-indexed inclusive)."""
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _leading_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


# --------------------------------------------------------------------------- #
# --debug mode: AST dump (was previously the default)
# --------------------------------------------------------------------------- #


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
            # ASCII '...' so this renders on Windows CP-1252 terminals; the
            # original used U+2026 which mojibakes there.
            print(f"{indent}  ... ({node.child_count} children, depth limit)")
        return
    for child in node.children:
        _print_node(child, source, depth=depth + 1, max_depth=max_depth)


def _run_errors_only(root, source: bytes) -> int:
    errors = list(_iter_error_nodes(root))
    if not errors:
        print("(no error nodes)")
        return 0
    for node in errors:
        _print_node(node, source, depth=0)
    return 0 if not root.has_error else 1


def _iter_error_nodes(node):
    if node.is_error or node.is_missing:
        yield node
    for child in node.children:
        yield from _iter_error_nodes(child)
