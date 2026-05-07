"""``winterthur smells`` — pattern-based smell detection.

Composes two sources:

1. **Metric thresholds** — reuses :func:`metrics_walker.collect_function_metrics`
   output. A function with ``loc_total > 150`` is god-method-y, ``decision_points
   > 25`` is hard to reason about, ``max_nesting_depth > 5`` is deep. No new
   tree walk required — the metrics walker already produced the numbers.

2. **AST-pattern smells** — :mod:`winterthur.smells_walker`. ``with``
   statements (W1) need positional AST scrutiny that doesn't reduce to a
   per-function counter — every occurrence is reported with its line.

The codereview skill's ``smells.md`` has a longer rule catalogue (R3
fall-through, swallowed exceptions, SQL string interpolation, …); this
first cut covers the rules that benefit most from a parser. Pure regex
rules belong in the skill's own grep step, not here.

Pascal is the only first-class language for now — the metrics walker's
node-kind table is currently Pascal-only, and the AST walker's
``with`` and ``exit-in-loop`` rules are Pascal-specific. Other
languages will report ``no smells found`` until their walker entries
are filled in.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

from ..io_helpers import file_info_from_path
from ..metrics_walker import (
    FunctionMetrics,
    collect_function_metrics,
    validate_structure,
)
from ..parser import _get_language
from ..smells_walker import SmellHit, find_pascal_smells
from ..walkers import get_walker
from ..walkers.base import _iter_descendants


# Severity bands match smells.md's red/yellow/green convention. Red is a
# likely bug or hard rule; yellow is a smell worth checking; green is
# noted but acceptable. We don't currently emit green.
SEVERITY_RED = "red"
SEVERITY_YELLOW = "yellow"

# Threshold table. Tunable — these match what the codereview skill's
# scorecard.md considers "loud" for Delphi units. Thresholds are
# *inclusive*: ``value >= threshold`` triggers (so "8 is many params"
# reads naturally instead of "you need 9 to be flagged").
#
# L3 nesting numbers look high because the Pascal grammar counts every
# `if` AND its `block` as separate nesting layers — six logical levels of
# indented `if begin … end` show up as ~12. Calibrated for that.
THRESHOLDS: dict[str, dict[str, int]] = {
    "L1": {"yellow": 150, "red": 400},   # loc_total
    "L2": {"yellow": 15, "red": 25},     # decision_points
    "L3": {"yellow": 8, "red": 12},      # max_nesting_depth
    "P1": {"yellow": 8, "red": 13},      # param_count
    "A4": {"yellow": 2, "red": 4},       # exit_count
}

RULE_NAMES = {
    "L1": "god-method",
    "L2": "god-decisions",
    "L3": "deep-nesting",
    "P1": "many-params",
    "A4": "multiple-exits",
    "W1": "with-statement",
}

# Severity ordering for sorted output (red first).
_SEVERITY_RANK = {SEVERITY_RED: 0, SEVERITY_YELLOW: 1}


@dataclass
class Finding:
    """One smell finding, regardless of which detector produced it."""

    rule: str          # short code, e.g. "L1"
    name: str          # human label, e.g. "god-method"
    severity: str      # "red" | "yellow"
    line: int
    function: str | None  # qualified name when scoped to a function
    metric: int | None    # the offending count (loc, decisions, …)
    threshold: int | None # the threshold it crossed
    detail: str        # one-line human description


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "smells",
        help=(
            "Pattern-based smell findings (god-method, deep nesting, "
            "with-statements, multiple-exits, ...)"
        ),
    )
    sub.add_argument(
        "files",
        nargs="+",
        metavar="FILE",
        help="One or more source files (Pascal first-class).",
    )
    sub.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of the default human-readable text view.",
    )
    sub.add_argument(
        "--rules",
        metavar="CODES",
        help=(
            "Comma-separated rule codes to include. Default: all. "
            "Example: --rules L1,L2,W1"
        ),
    )
    sub.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    rule_filter: set[str] | None = None
    if args.rules:
        rule_filter = {r.strip() for r in args.rules.split(",") if r.strip()}
        unknown = rule_filter - set(RULE_NAMES.keys())
        if unknown:
            print(
                f"error: unknown rule code(s): {', '.join(sorted(unknown))}. "
                f"Known: {', '.join(sorted(RULE_NAMES.keys()))}",
                file=sys.stderr,
            )
            return 2

    file_records: list[dict] = []
    failures = 0

    for raw in args.files:
        path = Path(raw)
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

        record = _scan_file(info, source, rule_filter)
        file_records.append(record)

    output = {"files": file_records}

    if args.json:
        json.dump(output, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _print_text(output)

    # Exit code: 0 if no findings; 1 if findings; 2 reserved for hard errors
    # (already returned above). Hard parse errors on a file count toward 1
    # too — the codereview skill cares whether anything fired.
    if failures:
        return 1
    has_findings = any(rec.get("findings") for rec in file_records)
    return 1 if has_findings else 0


def _scan_file(info, source: bytes, rule_filter: set[str] | None) -> dict:
    """Produce the {file, language, findings, errors?} record for one file."""
    ts_language = _get_language(info.language)
    if ts_language is None:
        return {
            "file": info.path,
            "language": info.language,
            "findings": [],
            "errors": [f"no tree-sitter grammar loaded for {info.language!r}"],
        }

    try:
        from tree_sitter import Parser as _Parser
    except Exception as exc:  # noqa: BLE001 — install-integrity guard, logged
        # tree_sitter import failing here means the winterthur install is
        # broken (parser.py also imports it unconditionally). Surface both
        # via the structured log AND inline in the file record so the user
        # sees something actionable.
        import structlog
        structlog.get_logger(__name__).warning(
            "tree_sitter import failed in smells command",
            error=str(exc),
            file_path=info.path,
            language=info.language,
        )
        return {
            "file": info.path,
            "language": info.language,
            "findings": [],
            "errors": [f"tree-sitter unavailable: {exc}"],
        }

    ts_parser = _Parser(ts_language)
    tree = ts_parser.parse(source)

    metrics = collect_function_metrics(tree.root_node, source, info.language)
    structural_errors = validate_structure(tree.root_node, source, info.language)
    ast_hits = find_pascal_smells(tree.root_node, source, info.language)

    function_names = _function_qualified_names(tree.root_node, source, info.language)

    findings: list[Finding] = []
    findings.extend(_metric_findings(metrics, function_names))
    findings.extend(_ast_findings(ast_hits, function_names))

    if rule_filter is not None:
        findings = [f for f in findings if f.rule in rule_filter]

    findings.sort(
        key=lambda f: (_SEVERITY_RANK.get(f.severity, 99), f.line, f.rule)
    )

    record: dict = {
        "file": info.path,
        "language": info.language,
        "findings": [asdict(f) for f in findings],
    }
    if structural_errors:
        record["errors"] = list(structural_errors)
    return record


def _metric_findings(
    metrics: dict[tuple[int, int], FunctionMetrics],
    function_names: dict[tuple[int, int], tuple[int, str]],
) -> list[Finding]:
    """Convert per-function counters into threshold findings."""
    out: list[Finding] = []
    for key, fm in metrics.items():
        # Resolve display name; fall back to "?" if we couldn't extract one.
        line, qual = function_names.get(key, (key[0], "?"))

        loc_total = max(0, key[1] - key[0] + 1)
        out.extend(_threshold_findings_for(
            "L1", loc_total, line, qual, "loc_total"
        ))
        out.extend(_threshold_findings_for(
            "L2", fm.decision_points, line, qual, "decision_points"
        ))
        out.extend(_threshold_findings_for(
            "L3", fm.max_nesting_depth, line, qual, "max_nesting_depth"
        ))
        out.extend(_threshold_findings_for(
            "P1", fm.param_count, line, qual, "param_count"
        ))
        out.extend(_threshold_findings_for(
            "A4", fm.exit_count, line, qual, "exit_count"
        ))
    return out


def _threshold_findings_for(
    rule: str, value: int | None, line: int, qual: str, label: str
) -> list[Finding]:
    """Emit at most one finding for *value* against *rule*'s thresholds.

    Picks the highest band crossed (red beats yellow). Returns ``[]`` when
    value is below the yellow threshold or when value is ``None`` (the
    walker had no observation, e.g. no exits at all).
    """
    if value is None:
        return []
    bands = THRESHOLDS[rule]
    for severity in (SEVERITY_RED, SEVERITY_YELLOW):
        thresh = bands[severity]
        if value >= thresh:
            return [Finding(
                rule=rule,
                name=RULE_NAMES[rule],
                severity=severity,
                line=line,
                function=qual,
                metric=value,
                threshold=thresh,
                detail=f"{label}={value} (>={thresh})",
            )]
    return []


def _ast_findings(
    hits: list[SmellHit],
    function_names: dict[tuple[int, int], tuple[int, str]],
) -> list[Finding]:
    """Convert SmellHit objects (W1, A1, ...) into Finding objects."""
    out: list[Finding] = []
    # Severity policy for AST-pattern smells. Keep the table even with one
    # entry — adding rules later (swallowed exceptions, SQL string interp,
    # etc.) needs the same shape.
    severity_for_rule = {"W1": SEVERITY_YELLOW}

    for h in hits:
        qual: str | None = None
        if h.function_key is not None:
            entry = function_names.get(h.function_key)
            if entry is not None:
                qual = entry[1]
        out.append(Finding(
            rule=h.rule,
            name=RULE_NAMES.get(h.rule, h.rule),
            severity=severity_for_rule.get(h.rule, SEVERITY_YELLOW),
            line=h.line,
            function=qual,
            metric=None,
            threshold=None,
            detail=h.detail,
        ))
    return out


def _function_qualified_names(
    root_node, source: bytes, language: str
) -> dict[tuple[int, int], tuple[int, str]]:
    """Map ``(start_line, end_line)`` -> ``(start_line, qualified_name)``.

    Iteration mirrors :func:`collect_function_metrics` so the keys line
    up exactly. Per-language name extraction is delegated to the
    walker's :meth:`LanguageWalker.qualified_name` method; the fallback
    for an unsupported language or unrecognised shape is ``"?"``.
    """
    walker = get_walker(language)
    if walker is None:
        return {}
    out: dict[tuple[int, int], tuple[int, str]] = {}
    fn_kinds = walker.function_node_types
    for node in _iter_descendants(root_node):
        if node.type not in fn_kinds:
            continue
        start = node.start_point[0] + 1
        end = node.end_point[0] + 1
        qual = walker.qualified_name(node, source) or "?"
        out[(start, end)] = (start, qual)
    return out


_PARSE_ERROR_DISCLAIMER = (
    "  WARNING: Parse errors DO NOT mean that the code is bad, it only "
    "means the parser is probably broken.\n"
    "           Use compilers to check syntax, not this tool."
)


def _print_text(output: dict) -> None:
    for rec in output["files"]:
        print(f"\n{rec['file']} ({rec['language']})")
        errors = rec.get("errors", [])
        for msg in errors:
            print(f"  MALFORMED: {msg}")
        if errors:
            print(_PARSE_ERROR_DISCLAIMER)
        findings = rec["findings"]
        if not findings:
            print("  (no smells found)")
            continue
        print(f"  smells ({len(findings)}):")
        # Pre-compute column widths so output reads as a table.
        rule_w = max(len(f["rule"]) for f in findings)
        sev_w = max(len(f["severity"]) for f in findings)
        fn_w = max(
            len(f["function"] or "-") for f in findings
        )
        fn_w = min(fn_w, 50)  # cap for very long names
        for f in findings:
            qual = f["function"] or "-"
            if len(qual) > fn_w:
                qual = qual[: fn_w - 1] + "…"
            print(
                f"    {f['rule']:<{rule_w}} {f['severity']:<{sev_w}}  "
                f"{qual:<{fn_w}}  line {f['line']:<5}  {f['detail']}"
            )
