"""Base types for per-language smell finders.

Some smells are countable from the metrics walker's totals (god-method
size, deep nesting, too many params, multiple exits). Others need
positional information about *where* in the AST a construct appears —
those live in concrete :class:`SmellFinder` subclasses.

A finder paired with a language tag uses :func:`walkers.get_walker` to
discover that language's ``function_node_types``, so we don't repeat
that frozenset across the metrics and smells layers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from ..walkers import get_walker
from ..walkers.base import _iter_descendants


@dataclass
class SmellHit:
    """One AST-pattern smell occurrence.

    ``function_key`` is the ``(start_line, end_line)`` range of the
    enclosing function, matching
    :func:`metrics_walker.collect_function_metrics`'s return-dict key.
    Top-level (non-function) hits use ``None``.
    """

    rule: str
    line: int
    function_key: tuple[int, int] | None = None
    detail: str = ""


class SmellFinder(ABC):
    """Strategy class for per-language smell detection.

    Subclasses set :attr:`language` and implement :meth:`find`. The
    helper :meth:`_function_ranges` yields ``(start, end, fn_node)``
    triples for each function-shaped node — sharing the iteration
    pattern across detectors.
    """

    language: ClassVar[str]

    @abstractmethod
    def find(self, root_node, source: bytes) -> list[SmellHit]:
        """Run all detectors for this language and return their hits."""

    # ------------------------------------------------------------------
    # Shared helpers — used by concrete finders, not part of the public API.
    # ------------------------------------------------------------------

    def _function_ranges(
        self, root_node
    ) -> list[tuple[int, int, object]]:
        """Enumerate ``(start_line, end_line, fn_node)`` for each function."""
        walker = get_walker(self.language)
        if walker is None:
            return []
        fn_kinds = walker.function_node_types
        out: list[tuple[int, int, object]] = []
        for fn_node in _iter_descendants(root_node):
            if fn_node.type in fn_kinds:
                out.append(
                    (
                        fn_node.start_point[0] + 1,
                        fn_node.end_point[0] + 1,
                        fn_node,
                    )
                )
        return out
