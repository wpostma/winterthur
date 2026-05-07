"""AST-pattern smell detectors that complement :mod:`metrics_walker`.

Some smells are countable from the metrics walker's totals (god-method
size, deep nesting, too many params, multiple exits). Others need
positional information about *where* in the AST a construct appears —
e.g. an ``exit;`` is only the R2 violation when it sits inside a loop
body, not when it's a top-of-function guard. This module hosts the
positional walks.

Each detector returns a list of :class:`SmellHit` records keyed by line
number. The smells command merges these with metrics-derived smells and
prints / serialises them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .metrics_walker import (
    _iter_descendants,
    NODE_KINDS_BY_LANGUAGE,
)


@dataclass
class SmellHit:
    """One AST-pattern smell occurrence.

    ``function_key`` is the ``(start_line, end_line)`` range of the enclosing
    function, matching :func:`metrics_walker.collect_function_metrics`'s
    return-dict key. Top-level (non-function) hits use ``None``.
    """

    rule: str
    line: int
    function_key: tuple[int, int] | None = None
    detail: str = ""


# Pascal: any `with` statement is a smell — it shadows scope and breaks
# greppability. The grammar emits one `with` node per `with X, Y, Z do …`,
# even when several entities are listed.
_WITH_NODE_TYPES_PASCAL = frozenset({"with"})

# We deliberately do NOT have an "exit-in-loop" detector here. The original
# A1 rule from smells.md flagged any `Exit;` inside a `while`/`for`/`repeat`
# body as an R2 violation, but in practice that's the idiomatic
# linear-search early-return pattern (every Find/Get/TryGet function uses
# it). The actually-useful signal is COUNT, not LOCATION: a single exit is
# fine wherever it sits, two or more exits in one function is a sure sign
# the function should be decomposed. That's A4 (multiple-exits), driven
# from metrics_walker's exit_count — no positional walk needed.


def find_pascal_smells(
    root_node, source: bytes, language: str
) -> list[SmellHit]:
    """Run all Pascal AST-pattern detectors and return their hits.

    Returns ``[]`` for non-Pascal languages — extending to Python/Rust/etc.
    means adding language-specific node-type tables and walkers.
    """
    if language != "pascal":
        return []

    kinds = NODE_KINDS_BY_LANGUAGE.get(language)
    if kinds is None:
        return []

    fn_kinds = kinds["function_def"]
    function_ranges: list[tuple[int, int, object]] = []
    for fn_node in _iter_descendants(root_node):
        if fn_node.type in fn_kinds:
            function_ranges.append(
                (fn_node.start_point[0] + 1, fn_node.end_point[0] + 1, fn_node)
            )

    hits: list[SmellHit] = []
    for start, end, fn_node in function_ranges:
        key = (start, end)
        hits.extend(_with_smells_in_function(fn_node, key))
    return hits


def _with_smells_in_function(
    fn_node, key: tuple[int, int]
) -> Iterable[SmellHit]:
    for node in _iter_descendants(fn_node):
        if node.type in _WITH_NODE_TYPES_PASCAL:
            yield SmellHit(
                rule="W1",
                line=node.start_point[0] + 1,
                function_key=key,
                detail="with-statement (scope shadowing)",
            )
