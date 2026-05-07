"""Per-language metrics walkers.

The :mod:`metrics_walker` dispatcher in the parent package is
language-agnostic; the strategy classes here do the per-language work.
Add a new language by writing one ``<lang>.py`` module that subclasses
:class:`LanguageWalker` and registering it in :mod:`walkers.registry`.
"""

from __future__ import annotations

from .base import (
    FunctionMetrics,
    LanguageWalker,
    WalkContext,
    walk_function,
)
from .registry import get_walker

__all__ = [
    "FunctionMetrics",
    "LanguageWalker",
    "WalkContext",
    "get_walker",
    "walk_function",
]
