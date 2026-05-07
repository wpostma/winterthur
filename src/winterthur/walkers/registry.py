"""Per-language walker registry.

Adding a new language: write ``walkers/<lang>.py`` exposing a
:class:`LanguageWalker` subclass, then register it here. Keeping the
registry as an explicit table (rather than auto-discovery) means a
typo in a module name shows up as a missing key, not as silent
fall-through to the language-not-supported path.
"""

from __future__ import annotations

from .base import LanguageWalker
from .pascal import PascalWalker
from .python import PythonWalker
from .typescript import TypeScriptWalker


_WALKERS: dict[str, LanguageWalker] = {
    "pascal": PascalWalker(),
    "python": PythonWalker(),
    "typescript": TypeScriptWalker(),
}


def get_walker(language: str) -> LanguageWalker | None:
    """Return the walker for *language*, or ``None`` if unsupported."""
    return _WALKERS.get(language)
