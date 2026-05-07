"""``winterthur metrics`` — per-function structural metrics.

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

from ..io_helpers import file_info_from_path, language_for
from ..metrics_walker import (
    FunctionMetrics,
    collect_function_metrics,
    validate_structure,
)
from ..parser import ASTParser, _get_language

DEFAULT_FILE_LIMIT = 30

# Symbol kinds we treat as "function-shaped" for metrics output.
_FUNCTION_KINDS = frozenset(
    {"function", "method", "procedure", "constructor", "destructor", "operator"}
)


def register(subparsers: argparse._SubParsersAction) -> None:
    # See declaration.register() for the alias-hiding rationale.
    sub = subparsers.add_parser(
        "metrics",
        help="Per-function structural metrics (JSON for the codereview skill)",
    )
    _add_args(sub)
    sub.set_defaults(func=run)

    alias = subparsers.add_parser("metric")
    _add_args(alias)
    alias.set_defaults(func=run)
    from .declaration import _hide_from_help
    _hide_from_help(subparsers, "metric")


def _add_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "files",
        nargs="+",
        metavar="FILE_OR_GLOB",
        help=(
            "One or more file names or glob patterns (e.g. 'AdjustDrawer.pas', "
            "'*.pas', 'Order*.pas'). Resolved relative to --dir if given, "
            "otherwise the current directory."
        ),
    )
    sub.add_argument(
        "--dir",
        metavar="PATH",
        help=(
            "Base directory used as the prefix for the FILE_OR_GLOB args. "
            "Convenience so you don't repeat a long path. Does NOT scan the "
            "directory on its own — you still must pass a file or glob."
        ),
    )
    sub.add_argument(
        "--recurse",
        action="store_true",
        help=(
            "Expand globs recursively (rglob) instead of shallow (glob). "
            "E.g. --recurse '*.pas' matches .pas anywhere under --dir/CWD."
        ),
    )
    sub.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_FILE_LIMIT,
        metavar="N",
        help=f"Cap total files processed (default {DEFAULT_FILE_LIMIT}).",
    )
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


def run(args: argparse.Namespace) -> int:
    try:
        target_paths = _collect_paths(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    parser = ASTParser(skip_implementation=args.skip_implementation)
    files_out: list[dict] = []
    failures = 0

    for path in target_paths:
        try:
            info, source = file_info_from_path(path)
        except FileNotFoundError:
            print(f"error: file not found: {path}", file=sys.stderr)
            failures += 1
            continue
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            failures += 1
            continue

        parsed = parser.parse_file(info, source)
        total_lines = source.count(b"\n") + (0 if source.endswith(b"\n") else 1)
        walker_metrics, structural_errors = _walker_results_for(info.language, source)

        functions = [
            _function_record(sym, source, walker_metrics)
            for sym in parsed.symbols
            if sym.kind in _FUNCTION_KINDS
        ]

        # Combine structural errors (begin/end mismatch, missing 'end.', etc.)
        # with any parser-reported errors. If the validator already reported a
        # parse error we drop the parser's generic "Parse error at line N"
        # entries — they're the same signal with worse wording.
        errors: list[str] = list(structural_errors)
        validator_already_flagged_parse = any(
            msg.startswith("parse error") for msg in structural_errors
        )
        for msg in parsed.parse_errors:
            if validator_already_flagged_parse and msg.startswith("Parse error"):
                continue
            if msg not in errors:
                errors.append(msg)
        if errors:
            failures += 1

        file_record: dict = {
            "file": info.path,
            "language": info.language,
            "total_lines": total_lines,
            "functions": functions,
        }
        if errors:
            file_record["errors"] = errors
        files_out.append(file_record)

    output = {"files": files_out}

    if args.text:
        _print_text(output)
    else:
        json.dump(output, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")

    return 1 if failures else 0


_GLOB_CHARS = frozenset("*?[")


def _split_anchor_and_pattern(raw: str, base: Path | None) -> tuple[Path, str]:
    """Split a glob input into a real directory anchor and a relative pattern.

    pathlib's :py:meth:`Path.glob` only accepts relative patterns. An input
    like ``C:\\foo\\bar\\Adjust*.pas`` must be split into anchor
    ``C:\\foo\\bar`` and pattern ``Adjust*.pas``. This helper handles three
    cases:

    1. Pattern has no separators: ``"*.pas"`` -> (base or CWD, ``"*.pas"``).
    2. Pattern is relative with separators: ``"sub/Foo*.pas"`` ->
       (base or CWD, the same pattern).
    3. Pattern is absolute: ``"C:/x/y/Foo*.pas"`` -> walk the parts from
       the root, take everything up to (but not including) the first part
       that contains a glob char as the anchor; the remainder joined with
       ``/`` is the relative pattern.

    --dir is honoured for case 1 and case 2; for case 3 the anchor in the
    pattern itself wins (an absolute path is unambiguous).
    """
    p = Path(raw)
    if not p.is_absolute():
        anchor = base if base is not None else Path(".")
        return anchor, raw

    # Absolute pattern: find the first part that has a glob char.
    parts = p.parts  # ("C:\\", "foo", "bar", "Adjust*.pas") on Windows
    # parts[0] is the drive/root anchor (always non-glob); start from index 1.
    split_at = len(parts)
    for i in range(1, len(parts)):
        if any(ch in parts[i] for ch in _GLOB_CHARS):
            split_at = i
            break

    anchor = Path(*parts[:split_at]) if split_at > 0 else Path(parts[0])
    pattern_parts = parts[split_at:]
    if not pattern_parts:
        # No glob chars found — caller should not have classified as glob.
        return anchor, ""
    return anchor, "/".join(pattern_parts)


def _collect_paths(args: argparse.Namespace) -> list[Path]:
    """Resolve positional file/glob args, with --dir as an optional prefix.

    Model:
      - Each positional is either a literal file name/path or a glob.
      - --dir, if given, is prepended (for both literals and globs).
      - --recurse switches glob() -> rglob() for glob expansion.
      - Literal paths are NOT searched recursively. Use a glob if you
        want a recursive search by name (e.g. --recurse '**/Foo.pas').

    Errors:
        ValueError: --dir does not exist / not a dir; --limit < 1;
        a glob matches nothing.
    """
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be >= 1")

    base: Path | None = None
    if args.dir:
        base = Path(args.dir)
        if not base.exists():
            raise ValueError(f"--dir path does not exist: {base}")
        if not base.is_dir():
            raise ValueError(f"--dir path is not a directory: {base}")

    collected: list[Path] = []
    glob_count = 0          # how many positionals were globs
    glob_match_count = 0    # how many globs produced at least one match
    for raw in args.files:
        is_glob = any(c in raw for c in _GLOB_CHARS)
        if is_glob:
            glob_count += 1
            anchor, pattern = _split_anchor_and_pattern(raw, base)
            iterator = anchor.rglob(pattern) if args.recurse else anchor.glob(pattern)
            matches = sorted(p for p in iterator if p.is_file())
            if not matches:
                # Per-glob miss is a warning, not an error — when scanning
                # heterogeneous codebases the user often passes several
                # extension globs (*.py *.rs *.ts) and not every directory
                # has every extension. Only error if EVERY glob misses
                # AND no literal paths were given.
                print(
                    f"warning: no files matched glob {pattern!r} under {anchor}",
                    file=sys.stderr,
                )
                continue
            glob_match_count += 1
            collected.extend(matches)
        else:
            p = Path(raw)
            if not p.is_absolute() and base is not None:
                p = base / raw
            if not p.exists():
                # Literal paths still fail fast — typo in a single file
                # name is a different kind of mistake than "this dir
                # doesn't happen to have any *.rs files".
                raise ValueError(f"file not found: {p}")
            collected.append(p)

    # Hard error only if every glob missed AND no literal paths were given.
    if glob_count > 0 and glob_match_count == 0 and not collected:
        raise ValueError(
            f"no files matched any of the {glob_count} glob "
            f"pattern{'s' if glob_count != 1 else ''} given"
        )

    seen: set[Path] = set()
    deduped: list[Path] = []
    for p in collected:
        try:
            key = p.resolve()
        except OSError:
            key = p
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)

    if len(deduped) > args.limit:
        print(
            f"warning: {len(deduped)} files matched, limiting to first "
            f"{args.limit} (use --limit N to raise)",
            file=sys.stderr,
        )
        deduped = deduped[: args.limit]

    return deduped


def _walker_results_for(
    language: str, source: bytes
) -> tuple[dict[tuple[int, int], FunctionMetrics], list[str]]:
    """Re-parse *source* with tree-sitter; return (metrics, structural_errors).

    Both halves come from a single parse. ``metrics`` is keyed by
    ``(start_line, end_line)``. ``structural_errors`` carries Pascal-only
    findings (begin/end imbalance, missing ``end.``) and tree-sitter's
    has_error signal. Returns ``({}, [])`` when there is no walker support.
    """
    ts_language = _get_language(language)
    if ts_language is None:
        return {}, []
    try:
        from tree_sitter import Parser as _Parser
    except Exception:  # noqa: BLE001
        return {}, []
    ts_parser = _Parser(ts_language)
    tree = ts_parser.parse(source)
    metrics = collect_function_metrics(tree.root_node, source, language)
    errors = validate_structure(tree.root_node, source, language)
    return metrics, errors


# Counter fields written by the walker. Order here is the order they
# appear in the JSON when present, so LLM consumers see related metrics
# adjacent (control flow, then loops, then exception handling, then
# expression-level, then structural).
_WALKER_FIELDS: tuple[str, ...] = (
    "if_count",
    "case_count",
    "case_arms",
    "loop_count",
    "exit_count",
    "break_count",
    "continue_count",
    "try_count",
    "except_count",
    "finally_count",
    "raise_count",
    "boolean_op_count",
    "result_assign_count",
    "anon_proc_count",
    "max_anon_proc_depth",
    "max_nesting_depth",
    "param_count",
    "local_var_count",
    "logger_call_count",
)


def _function_record(
    sym, source: bytes, walker_metrics: dict[tuple[int, int], FunctionMetrics]
) -> dict:
    """Build a metrics record for one function-shaped symbol.

    Counter fields are emitted only when they have a non-null, non-zero
    value (and ``params`` only when non-empty). This keeps JSON output terse
    for LLM consumers — a function with no try/except simply has no
    try/except keys, instead of three zero-valued fields wasting tokens.
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
        "decision_points": 1,
    }

    fm = walker_metrics.get((line_start, line_end))
    if fm is None:
        # Walker had no entry (forward decl, non-pascal language, or no
        # walker mapping). Leave decision_points at 1 and emit nothing else.
        return record

    record["decision_points"] = fm.decision_points
    if fm.params:
        record["params"] = list(fm.params)
    for field_name in _WALKER_FIELDS:
        v = getattr(fm, field_name)
        if v:  # drops both None and 0
            record[field_name] = v
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


PARSE_ERROR_DISCLAIMER = (
    "  WARNING: Parse errors DO NOT mean that the code is bad, it only "
    "means the parser is probably broken.\n"
    "           Use compilers to check syntax, not this tool."
)


def _print_text(output: dict) -> None:
    for file_record in output["files"]:
        print(f"\n{file_record['file']} ({file_record['language']})")
        print(f"  total_lines: {file_record['total_lines']}")
        file_errors = file_record.get("errors", [])
        for msg in file_errors:
            print(f"  MALFORMED: {msg}")
        if file_errors:
            print(PARSE_ERROR_DISCLAIMER)
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
