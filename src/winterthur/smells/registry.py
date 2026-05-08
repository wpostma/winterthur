"""Per-language smell-finder registry.

Same pattern as :mod:`walkers.registry` — explicit table keyed by
language tag. A new language is one module + one entry.
"""

from __future__ import annotations

from .base import SmellFinder
from .pascal import PascalSmellFinder
from .python import PythonSmellFinder
from .typescript import TypeScriptSmellFinder


_FINDERS: dict[str, SmellFinder] = {
    "pascal": PascalSmellFinder(),
    "python": PythonSmellFinder(),
    "typescript": TypeScriptSmellFinder(),
}


def get_finder(language: str) -> SmellFinder | None:
    """Return the finder for *language*, or ``None`` if unsupported."""
    return _FINDERS.get(language)
