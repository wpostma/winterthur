"""Per-function metrics walker — language-agnostic dispatcher.

Per-language behaviour lives in :mod:`winterthur.walkers`. This module
keeps the public API stable: :func:`collect_function_metrics`,
:func:`validate_structure`, and :class:`FunctionMetrics` all still
import from here. The strategy classes in
:mod:`winterthur.walkers.pascal` (and future ``walkers.python`` etc.)
do the per-language work.

Adding a new language: write a ``LanguageWalker`` subclass in
``walkers/<lang>.py`` and register it in ``walkers/registry.py``. No
edits to this file required.
"""

from __future__ import annotations

from .walkers.base import (
    FunctionMetrics,
    LanguageWalker,
    WalkContext,
    _bump,
    _iter_descendants,
    walk_function,
)
from .walkers.registry import get_walker

__all__ = [
    "FunctionMetrics",
    "LanguageWalker",
    "WalkContext",
    "collect_function_metrics",
    "get_walker",
    "validate_structure",
    # Re-exports kept for the smells command and walker, which used to
    # reach into this module's privates.
    "_bump",
    "_iter_descendants",
]


def collect_function_metrics(
    root_node, source: bytes, language: str
) -> dict[tuple[int, int], FunctionMetrics]:
    """Walk *root_node* and return metrics keyed by ``(start_line, end_line)``.

    Lines are 1-indexed (matching :class:`Symbol`). Returns an empty
    dict for languages without a registered walker.
    """
    walker = get_walker(language)
    if walker is None:
        return {}

    out: dict[tuple[int, int], FunctionMetrics] = {}
    for fn_node in _iter_descendants(root_node):
        if fn_node.type not in walker.function_node_types:
            continue
        metrics = walk_function(fn_node, source, walker)
        key = (fn_node.start_point[0] + 1, fn_node.end_point[0] + 1)
        out[key] = metrics
    return out


def validate_structure(
    root_node, source: bytes, language: str
) -> list[str]:
    """Return human-readable structural errors for *root_node*.

    Empty list when the language has no validator (or no walker
    registered). Pascal currently surfaces tree-sitter parse errors,
    begin/end imbalance, and a missing ``end.`` unit terminator.
    """
    walker = get_walker(language)
    if walker is None:
        return []
    return walker.validate_structure(root_node, source)
