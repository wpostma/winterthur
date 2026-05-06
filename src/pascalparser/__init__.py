"""pascalparser — Pascal/Delphi parser plus lint and metrics tooling.

The underlying ``ASTParser`` is multi-language (carried over from
repowise); this project is named for its primary use case.

Public API:

- :func:`parse_file` — parse a single source file; returns ``ParsedFile``
- :class:`ASTParser` — the underlying parser
- :class:`ParsedFile`, :class:`Symbol`, :class:`Import`, :class:`FileInfo`
"""

from __future__ import annotations

from .models import FileInfo, Import, ParsedFile, Symbol
from .parser import ASTParser, LANGUAGE_CONFIGS

__version__ = "0.1.0"
__all__ = [
    "ASTParser",
    "FileInfo",
    "Import",
    "LANGUAGE_CONFIGS",
    "ParsedFile",
    "Symbol",
]
