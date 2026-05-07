"""Per-language AST-pattern smell finders.

Mirrors :mod:`winterthur.walkers`: each language gets a
:class:`SmellFinder` subclass in its own module, registered in
:mod:`smells.registry`. The :mod:`commands.smells` command looks up
the finder by language tag and delegates.

Adding a new language: write ``smells/<lang>.py`` with a
``SmellFinder`` subclass, then add one entry to ``smells/registry.py``.
"""

from __future__ import annotations

from .base import SmellFinder, SmellHit
from .registry import get_finder

__all__ = ["SmellFinder", "SmellHit", "get_finder"]
