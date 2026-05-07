"""Per-language smell-finder registry.

Same pattern as :mod:`walkers.registry` — explicit table keyed by
language tag. A new language is one module + one entry.
"""

from __future__ import annotations

from .base import SmellFinder
from .pascal import PascalSmellFinder


_FINDERS: dict[str, SmellFinder] = {
    "pascal": PascalSmellFinder(),
}


def get_finder(language: str) -> SmellFinder | None:
    """Return the finder for *language*, or ``None`` if unsupported."""
    return _FINDERS.get(language)
