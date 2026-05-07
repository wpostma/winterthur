"""``winterthur declaration`` — dump the full declaration of one or more symbols.

Given a file and a display-name regex, find every matching symbol and
print its full declaration text — signature, parameter list,
return type — by slicing the source from the function-def node's start
up to (but not including) its body. Useful for "how do I call this?"
without opening the whole unit.

Differs from ``symbols``:

- ``symbols`` lists names + line numbers (one line per match).
- ``declaration`` prints the whole signature block (potentially several
  lines for multi-line param lists), so you see the actual call site
  shape.

Differs from ``parse --depth N``:

- ``parse`` is global and depth-driven; you pick a depth and see folded
  source for the whole file.
- ``declaration`` is symbol-targeted and unconcerned with depth — show
  me the declaration for these specific names.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ..io_helpers import file_info_from_path
from ..metrics_walker import validate_structure
from ..parser import ASTParser, _get_language
from .parse import _PARSE_ERROR_DISCLAIMER, _BODY_CHILD_TYPES


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "declaration",
        aliases=["declarations"],
        help="Dump the full declaration (signature + params) of matching symbols",
    )
    sub.add_argument("file", help="Source file")
    sub.add_argument(
        "symbol",
        help=(
            "Display-name pattern (glob by default; '*' and '?' wildcards). "
            "Pascal/C++/etc. overloads matching the same pattern are ALL "
            "printed — glob is the natural way to fetch a family. "
            "Examples: 'TOrder.ReAllocatePackages' (one), "
            "'TOrder.Calculate*' (every TOrder.Calculate*), "
            "'*Refund*' (anything containing 'Refund'). "
            "Pass --regex to switch to full Python regex (re.fullmatch). "
            "Case-insensitive unless --case-sensitive."
        ),
    )
    sub.add_argument(
        "--regex",
        action="store_true",
        help=(
            "Treat the symbol pattern as a Python regex (re.fullmatch) "
            "instead of glob. Useful when you need character classes, "
            "alternation, or anchoring beyond what glob offers."
        ),
    )
    sub.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Make the pattern case-sensitive (default: case-insensitive).",
    )
    sub.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help=(
            "Cap how many declarations to print (default 10). Pascal's "
            "forward-decl/body duplication can produce 2x matches for one "
            "method; raise this if you intentionally want broad matches."
        ),
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

    flags = 0 if args.case_sensitive else re.IGNORECASE
    if args.regex:
        regex_source = args.symbol
        pattern_label = f"/{args.symbol}/"
    else:
        regex_source = _glob_to_regex(args.symbol)
        pattern_label = f"glob '{args.symbol}'"
    try:
        pattern = re.compile(regex_source, flags)
    except re.error as exc:
        print(
            f"error: invalid {'regex' if args.regex else 'glob'} {args.symbol!r}: {exc}",
            file=sys.stderr,
        )
        return 2

    parser = ASTParser()
    parsed = parser.parse_file(file_info, source)

    # fullmatch (anchored) for both glob and --regex modes — declaration
    # is "show me THIS symbol", not "find anything containing X". The
    # symbols subcommand is for the substring-search case.
    matches = [
        s for s in parsed.symbols
        if pattern.fullmatch(_display_name(s))
        and s.kind in _CALLABLE_KINDS
    ]
    if not matches:
        flag = "" if args.case_sensitive else " (case-insensitive)"
        print(
            f"no callable symbols matched {pattern_label}{flag} in {file_info.path}",
            file=sys.stderr,
        )
        return 1

    ts_language = _get_language(file_info.language)
    if ts_language is None:
        # No grammar loaded — fall back to printing what Symbol carries.
        for sym in matches[: args.limit]:
            print(f"\n{file_info.path}:{sym.start_line}")
            print(_display_name(sym))
        if len(matches) > args.limit:
            print(f"\n... ({len(matches) - args.limit} more matches; raise --limit to see)")
        return 0

    from tree_sitter import Parser as _Parser
    ts_parser = _Parser(ts_language)
    tree = ts_parser.parse(source)

    # Index defProc / function-definition nodes by start_line so we can
    # match Symbol.start_line to a tree-sitter node in O(1).
    nodes_by_start_line: dict[int, object] = {}
    for n in _iter_function_def_nodes(tree.root_node):
        nodes_by_start_line.setdefault(n.start_point[0] + 1, n)

    # Pre-collect comment nodes so we can find leading-comment blocks
    # above each declaration in O(log n) walks.
    comments = _collect_comment_nodes(tree.root_node, source)

    shown = 0
    for sym in matches:
        if shown >= args.limit:
            break
        node = nodes_by_start_line.get(sym.start_line)
        decl_text = _extract_declaration_text(node, source) if node else None
        if not decl_text:
            decl_text = _display_name(sym)  # fallback when no AST node found

        leading_comments = _leading_comments_for(comments, sym.start_line)

        section = _section_label(node, file_info.language, sym)
        header = f"{file_info.path}:{sym.start_line}  ({section})"
        print(f"\n{header}")
        print("-" * len(header))
        for c in leading_comments:
            print(c)
        print(decl_text)
        shown += 1

    if len(matches) > args.limit:
        print(
            f"\n... ({len(matches) - args.limit} more matches; raise --limit to see)"
        )

    if tree.root_node.has_error:
        # Surface the validator's specific line-numbered messages above the
        # disclaimer — same pattern symbols and metrics use. "WARNING ...
        # parser is probably broken" without saying WHERE leaves the user
        # nothing to act on.
        for msg in validate_structure(tree.root_node, source, file_info.language):
            print(f"NOTE: {msg}", file=sys.stderr)
        print(_PARSE_ERROR_DISCLAIMER, file=sys.stderr)

    return 0


# Symbol kinds that have a declaration worth dumping. Excludes class
# declarations (those would print the entire class body).
_CALLABLE_KINDS = frozenset(
    {"function", "method", "procedure", "constructor", "destructor", "operator"}
)

# Tree-sitter function-definition node types per language. Includes Pascal's
# `declProc` (interface forward decl, no body) AND `defProc` (implementation
# with a body) — Symbol.start_line can point at either, and we want to find
# the matching node in both cases.
_FUNCTION_DEF_TYPES = frozenset({
    "declProc",            # pascal: interface forward decl
    "defProc",             # pascal: implementation body
    "function_definition", # python
    "method_definition",   # ts/js
    "function_declaration", # go, rust
})


# Maximum lines of empty space between a comment block and a declaration
# for the comment to still count as "this comment describes that declaration".
# 1 means "one blank line of gap is allowed", which matches the common
# style of: `{ Doc here. }<blank>procedure Foo;`.
_COMMENT_GAP_TOLERANCE = 1


def _collect_comment_nodes(root, source: bytes) -> list[tuple[int, int, str]]:
    """Walk the tree once and return (start_line, end_line, text) for each comment.

    Sorted by start_line so callers can binary-search / walk-backward
    cheaply. Pascal's tree-sitter grammar emits a single ``comment``
    node type covering ``// …``, ``{ … }``, and ``(* … *)`` variants —
    so this is grammar-tolerant within tree-sitter's coverage. For
    languages whose grammar uses a different name, callers fall back
    to the line-based heuristic via the empty-list path.
    """
    out: list[tuple[int, int, str]] = []

    def walk(n) -> None:
        if n.type in ("comment", "line_comment", "block_comment"):
            text = source[n.start_byte:n.end_byte].decode(
                "utf-8", errors="replace"
            ).replace("\r\n", "\n").rstrip()
            out.append((n.start_point[0] + 1, n.end_point[0] + 1, text))
        for c in n.children:
            walk(c)

    walk(root)
    out.sort(key=lambda t: t[0])
    return out


def _leading_comments_for(
    comments: list[tuple[int, int, str]], decl_start_line: int
) -> list[str]:
    """Return the contiguous comment block immediately above *decl_start_line*.

    "Contiguous" means each successive comment's end_line is within
    ``_COMMENT_GAP_TOLERANCE`` of the next one's start_line, walking
    backwards. The closest comment must also be within the same gap of
    the declaration itself (so unrelated comments far above don't get
    pulled in).
    """
    # Filter to comments that end strictly before the declaration.
    candidates = [c for c in comments if c[1] < decl_start_line]
    if not candidates:
        return []

    block: list[str] = []
    next_expected_end = decl_start_line - 1
    for cs, ce, text in reversed(candidates):
        if ce < next_expected_end - _COMMENT_GAP_TOLERANCE:
            break
        block.insert(0, text)
        next_expected_end = cs - 1
    return block


def _section_label(node, language: str, sym) -> str:
    """Pick a section tag for the per-match header line.

    For Pascal the duplicate forward-decl + body pair is the rule, not
    the exception, and (method) / (function) tells the reader nothing
    about WHICH of the two they're looking at. The tree-sitter node
    type IS the answer:

      - declProc  → ``interface`` (forward declaration in the type's
        ``interface`` section, no body)
      - defProc   → ``implementation`` (the actual ``begin … end``
        body in the unit's ``implementation`` section)

    For other languages we keep the kind ('function', 'method', etc.)
    since they don't have Pascal's split-section convention.
    """
    if language == "pascal" and node is not None:
        if node.type == "declProc":
            return "interface"
        if node.type == "defProc":
            return "implementation"
    return sym.kind


def _glob_to_regex(glob: str) -> str:
    """Translate a shell-style glob to a regex for re.fullmatch.

    `*` -> `.*`, `?` -> `.`, every other character escaped. We don't
    use :func:`fnmatch.translate` because it adds its own anchors and
    flag wrappers that interfere with our flag handling.
    """
    out: list[str] = []
    for ch in glob:
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _display_name(sym) -> str:
    return f"{sym.parent_name}.{sym.name}" if sym.parent_name else sym.name


def _iter_function_def_nodes(node):
    """Yield every function-definition-shaped node under *node*."""
    if node.type in _FUNCTION_DEF_TYPES:
        yield node
    for c in node.children:
        yield from _iter_function_def_nodes(c)


def _extract_declaration_text(node, source: bytes) -> str | None:
    """Return the declaration text for *node* (signature only, no body).

    Three cases:

    1. Pascal ``declProc`` (interface forward decl) — the entire node IS
       the declaration; no body anywhere.
    2. Pascal ``defProc`` (implementation) — has a ``declProc`` child
       carrying the qualified signature (``Function TOrder.Method(...): X``)
       and a separate ``block`` body. Use the declProc child.
    3. Python / generic — slice from node start to body child start.

    Returns None only if no useful slice can be made (and the caller
    falls back to the bare symbol name).
    """
    if node.type == "declProc":
        return _decode_clean(source[node.start_byte:node.end_byte])

    if node.type == "defProc":
        for c in node.children:
            if c.type == "declProc":
                return _decode_clean(source[c.start_byte:c.end_byte])
        # Fall through to body-slice if the grammar shape is unexpected.

    body = None
    for c in node.children:
        if c.type in _BODY_CHILD_TYPES:
            body = c
            break
    if body is None:
        return None
    raw = source[node.start_byte:body.start_byte]
    return _decode_clean(raw).rstrip(":").rstrip()


def _decode_clean(raw: bytes) -> str:
    """Decode utf-8 and normalise CRLF to LF so print() doesn't double-space."""
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").rstrip()
