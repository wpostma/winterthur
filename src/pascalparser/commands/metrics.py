"""``pascalparser metrics`` — per-function structural metrics.

Output schema conforms to the contract in
``~/.claude/skills/codereview/metrics-tool-spec.md``. This is the
intended consumer: the codereview skill calls this command and
deserialises the JSON.

This first cut populates the fields obtainable from the existing
:class:`ASTParser` output — name, qualified name, kind, line range,
loc_total, loc_effective. The deeper AST-walking metrics (nesting
depth, decision points, exit/break counts, anon-proc nesting,
parameter and local-var counts) are still ``null`` and will land in a
follow-up commit. Marking them ``null`` keeps the schema valid and
lets the codereview skill's scorecard distinguish "not measured" from
zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..io_helpers import file_info_from_path
from ..parser import ASTParser

# Fields from the metrics-tool-spec.md schema that we don't yet compute.
# Centralised so the deferred work is visible at a glance.
_DEFERRED_NUMERIC_FIELDS = (
    "max_nesting_depth",
    "param_count",
    "local_var_count",
    "anon_proc_count",
    "max_anon_proc_depth",
    "exit_count",
    "break_count",
    "continue_count",
    "raise_count",
    "result_assign_count",
    "try_count",
    "except_count",
    "finally_count",
    "if_count",
    "case_count",
    "case_arms",
    "loop_count",
    "boolean_op_count",
    "logger_call_count",
)

# Symbol kinds we treat as "function-shaped" for metrics output.
_FUNCTION_KINDS = frozenset(
    {"function", "method", "procedure", "constructor", "destructor", "operator"}
)


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "metrics",
        help="Per-function structural metrics (JSON for the codereview skill)",
    )
    sub.add_argument("files", nargs="+", help="Source files")
    sub.add_argument(
        "--json",
        action="store_true",
        default=True,
        help="Emit JSON (default; kept for parity with other subcommands)",
    )
    sub.add_argument(
        "--text",
        action="store_true",
        help="Emit a human-readable summary instead of JSON",
    )
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
    files_out: list[dict] = []
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
        total_lines = source.count(b"\n") + (0 if source.endswith(b"\n") else 1)

        functions = [
            _function_record(sym, source)
            for sym in parsed.symbols
            if sym.kind in _FUNCTION_KINDS
        ]

        files_out.append(
            {
                "file": info.path,
                "language": info.language,
                "total_lines": total_lines,
                "functions": functions,
            }
        )

    output = {"files": files_out}

    if args.text:
        _print_text(output)
    else:
        json.dump(output, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")

    return 1 if failures else 0


def _function_record(sym, source: bytes) -> dict:
    """Build a metrics record for one function-shaped symbol.

    Fields with simple line-arithmetic answers are filled in; the rest
    are ``null`` until the AST-walking metrics module lands.

    ``decision_points`` is populated from the parser's existing
    ``complexity_estimate`` (cyclomatic complexity) — that's an
    approximation of the codereview-skill ``decision_points`` field.
    """
    line_start = sym.start_line
    line_end = sym.end_line
    loc_total = max(0, line_end - line_start + 1)

    record: dict = {
        "name": sym.name,
        "qualified_name": sym.qualified_name,
        "kind": sym.kind,
        "line_start": line_start,
        "line_end": line_end,
        "loc_total": loc_total,
        "loc_effective": _effective_loc(source, line_start, line_end),
        "decision_points": int(getattr(sym, "complexity_estimate", 1) or 1),
        "params": [],  # populated when param-walking metrics land
    }
    for field in _DEFERRED_NUMERIC_FIELDS:
        record[field] = None
    return record


def _effective_loc(source: bytes, line_start: int, line_end: int) -> int:
    """LOC excluding blank lines and comment-only lines.

    Cheap line-by-line scan — fine for single-unit-of-compilation use.
    """
    if line_start <= 0 or line_end < line_start:
        return 0
    try:
        text = source.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return loc_total_fallback(line_start, line_end)
    lines = text.splitlines()
    span = lines[line_start - 1:line_end]
    count = 0
    for raw in span:
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith(("//", "#", "--", "{", "(*", "/*", "*")):
            # Pascal: '{' and '(*' open block comments; '//' is line comment.
            # C-family: '//' line, '/*' block, '*' inside continuation.
            # Python: '#' line.
            # SQL/Lua: '--' line.
            continue
        count += 1
    return count


def loc_total_fallback(line_start: int, line_end: int) -> int:
    return max(0, line_end - line_start + 1)


def _print_text(output: dict) -> None:
    for file_record in output["files"]:
        print(f"\n{file_record['file']} ({file_record['language']})")
        print(f"  total_lines: {file_record['total_lines']}")
        funcs = file_record["functions"]
        if not funcs:
            print("  (no functions)")
            continue
        print(f"  functions ({len(funcs)}):")
        for f in funcs:
            print(
                f"    {f['line_start']:>5}-{f['line_end']:<5} "
                f"{f['kind']:<12} {f['qualified_name']}  "
                f"loc={f['loc_total']} eff={f['loc_effective']}"
            )
