"""``pascalparser symbols`` — dump symbols and imports.

For a list of source files, run :class:`pascalparser.parser.ASTParser`
and emit each file's symbols + imports either as text (default) or as
JSON. Useful as a sanity check that the parser+queries combination is
extracting what you expect.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from ..io_helpers import file_info_from_path
from ..metrics_walker import validate_structure
from ..parser import ASTParser, _get_language


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "symbols",
        help="Dump symbols and imports for one or more files",
    )
    sub.add_argument("files", nargs="+", help="Source files")
    sub.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    sub.add_argument(
        "--regex",
        metavar="PATTERN",
        help=(
            "Filter symbols whose display name matches this regex. Matched "
            "against '<Class>.<Method>' for methods, bare '<Name>' for "
            "top-level. Case-insensitive (Pascal is case-insensitive). Use "
            "re.search semantics — anchor with ^ / $ if you want a full match."
        ),
    )
    sub.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Make --regex case-sensitive (default: case-insensitive).",
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
    pattern: re.Pattern | None = None
    if args.regex:
        try:
            flags = 0 if args.case_sensitive else re.IGNORECASE
            pattern = re.compile(args.regex, flags)
        except re.error as exc:
            print(f"error: invalid --regex {args.regex!r}: {exc}", file=sys.stderr)
            return 2

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
        all_symbol_dicts = [_symbol_to_dict(s) for s in parsed.symbols]
        all_import_dicts = [_import_to_dict(i) for i in parsed.imports]

        if pattern is not None:
            filtered_symbols = [
                s for s in all_symbol_dicts if pattern.search(_symbol_match_text(s))
            ]
            filtered_imports = [
                i for i in all_import_dicts if pattern.search(_import_match_text(i))
            ]
        else:
            filtered_symbols = all_symbol_dicts
            filtered_imports = all_import_dicts

        record = {
            "file": info.path,
            "language": info.language,
            "symbols": filtered_symbols,
            "symbols_total": len(all_symbol_dicts),
            "imports": filtered_imports,
            "imports_total": len(all_import_dicts),
            "errors": _combined_errors(info.language, source, parsed.parse_errors),
        }
        if pattern is not None:
            record["regex"] = args.regex
            record["regex_case_sensitive"] = bool(args.case_sensitive)

        if args.json:
            # Drop the helper field from JSON unless it's informative.
            json_record = dict(record)
            if pattern is None:
                json_record.pop("symbols_total", None)
            output.append(json_record)
        else:
            _print_text(record)

    if args.json:
        json.dump({"files": output}, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")

    return 1 if failures else 0


def _display_name(s: dict) -> str:
    """Return the display form used by the text printer."""
    name = s.get("name", "")
    parent = s.get("parent_name")
    return f"{parent}.{name}" if parent else name


def _symbol_match_text(s: dict) -> str:
    """Return the haystack --regex is searched against for a symbol.

    Includes the display name AND the full signature (parameter list +
    return type) so a query like ``--regex "TAgeRange"`` finds methods
    that take a TAgeRange parameter, not just methods named after the
    type. Joining with newlines means a regex like ``"foo.*bar"`` won't
    accidentally bridge from the name to the signature.
    """
    return "\n".join(
        x for x in (_display_name(s), s.get("signature", "")) if x
    )


def _import_match_text(i: dict) -> str:
    """Return the import string --regex is matched against.

    Use ``module_path`` only — it's the normalised one-import-per-record
    field. ``raw_statement`` carries the WHOLE `uses` clause for a Pascal
    file, so matching against it would make every import in the same
    clause match if any one of them did.
    """
    return str(i.get("module_path", "") or "")


def _combined_errors(
    language: str, source: bytes, parser_errors: list[str]
) -> list[str]:
    """Run structural validation and merge with parser-collected errors.

    Same logic the metrics command uses: prefer the validator's specific
    wording ("parse error starting at line 1558"), drop the parser's
    generic per-ERROR-node "Parse error at line N" entries when the
    validator already flagged a parse error. Without this, the symbols
    command shows "errors: 112" without saying where — useless.
    """
    ts_language = _get_language(language)
    if ts_language is None:
        return list(parser_errors)
    try:
        from tree_sitter import Parser as _Parser
    except Exception:  # noqa: BLE001
        return list(parser_errors)

    ts_parser = _Parser(ts_language)
    tree = ts_parser.parse(source)
    structural = validate_structure(tree.root_node, source, language)

    merged: list[str] = list(structural)
    parse_already_flagged = any(m.startswith("parse error") for m in structural)
    for msg in parser_errors:
        if parse_already_flagged and msg.startswith("Parse error"):
            continue
        if msg not in merged:
            merged.append(msg)
    return merged


def _symbol_to_dict(sym) -> dict:
    d = asdict(sym)
    # Drop fields that aren't useful in the symbol dump.
    for k in ("docstring",):
        d.pop(k, None)
    return d


def _import_to_dict(imp) -> dict:
    return asdict(imp)


_ERROR_DISPLAY_CAP = 5  # show first N errors verbatim, summarise the rest

PARSE_ERROR_DISCLAIMER = (
    "  WARNING: Parse errors DO NOT mean that the code is bad, it only "
    "means the parser is probably broken.\n"
    "           Use compilers to check syntax, not this tool."
)


def _print_text(record: dict) -> None:
    print(f"\n{record['file']} ({record['language']})")
    errors = record["errors"]
    if errors:
        # Show the first few verbatim (so the user sees specific line numbers
        # and validator messages like "begin/end mismatch") then summarise.
        print(f"  errors ({len(errors)}):")
        for msg in errors[:_ERROR_DISPLAY_CAP]:
            print(f"    {msg}")
        if len(errors) > _ERROR_DISPLAY_CAP:
            print(f"    ... ({len(errors) - _ERROR_DISPLAY_CAP} more)")
        print(PARSE_ERROR_DISCLAIMER)
    syms = record["symbols"]
    total = record.get("symbols_total", len(syms))
    regex = record.get("regex")

    flag = "" if record.get("regex_case_sensitive") else "i"

    if regex is not None:
        # "12 of 386 matching /^TOrder\./i". Worth surfacing even when the
        # match count is 0 — that tells the user "your pattern is wrong"
        # instead of "this file has no symbols".
        print(f"  symbols ({len(syms)} of {total} matching /{regex}/{flag}):")
        if not syms:
            print(f"    (no symbol names matched)")
    elif syms:
        print(f"  symbols ({len(syms)}):")
    elif total == 0:
        print(f"  (no symbols)")

    for s in syms:
        kind = s.get("kind", "?")
        line = s.get("start_line", "?")
        # Display name: <Class>.<Method> for methods, just <Name>
        # otherwise. The fully path-prefixed qualified_name carries
        # uniqueness info needed by a future cross-file resolver, but
        # it's pure noise in the per-file dump — the file is named
        # right above this list.
        print(f"    {line:>5}  {kind:<12} {_display_name(s)}")

    imps = record["imports"]
    imps_total = record.get("imports_total", len(imps))
    if regex is not None:
        # When filtering, always show the imports section header so the
        # user sees both haystack size and match count — `--regex` matches
        # imports too (Pascal `uses ideal.bo.types`).
        print(f"  imports ({len(imps)} of {imps_total} matching /{regex}/{flag}):")
        if not imps:
            print(f"    (no import paths matched)")
    elif imps:
        print(f"  imports ({len(imps)}):")
    for i in imps:
        print(f"    {i.get('module_path', '?')}")
