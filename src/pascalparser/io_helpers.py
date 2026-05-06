"""Small filesystem helpers for the CLI.

Single-unit-of-compilation use case: the user gives us a path on disk,
we need a populated :class:`FileInfo` plus the source bytes. None of
the repowise-pipeline-only fields (git_hash, is_test classification,
etc.) are meaningful here — they get sensible stubs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import EXTENSION_TO_LANGUAGE, FileInfo, LanguageTag


def language_for(path: Path) -> LanguageTag | None:
    """Map a file extension to a language tag, or ``None`` if unknown."""
    return EXTENSION_TO_LANGUAGE.get(path.suffix.lower())


def file_info_from_path(path: Path) -> tuple[FileInfo, bytes]:
    """Build a :class:`FileInfo` and read source bytes for a single file.

    Raises:
        FileNotFoundError: path does not exist.
        ValueError: extension does not map to a supported language.
    """
    abs_path = path.resolve()
    if not abs_path.exists():
        raise FileNotFoundError(abs_path)

    language = language_for(abs_path)
    if language is None:
        raise ValueError(
            f"unsupported file extension {abs_path.suffix!r} for {abs_path}"
        )

    source = abs_path.read_bytes()
    stat = abs_path.stat()
    info = FileInfo(
        path=abs_path.as_posix(),
        abs_path=str(abs_path),
        language=language,
        size_bytes=stat.st_size,
        git_hash="",
        last_modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return info, source
