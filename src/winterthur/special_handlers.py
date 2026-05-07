"""Special handlers for non-tree-sitter file formats.

These parsers use plain text/regex/YAML parsing rather than tree-sitter because
the formats are simple enough (Dockerfile, Makefile) or require domain-specific
libraries (OpenAPI via PyYAML).

Each handler produces a fully-populated ParsedFile — the same output model as
the tree-sitter parsers — so the rest of the pipeline treats them identically.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import structlog

from .models import FileInfo, Import, ParsedFile, Symbol

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def parse_special(file_info: FileInfo, source: bytes, lang: str) -> ParsedFile:
    """Route to the correct special handler based on language tag."""
    handler: Callable[[FileInfo, bytes], ParsedFile] = {
        "openapi": _parse_openapi,
        "dockerfile": _parse_dockerfile,
        "makefile": _parse_makefile,
        "pascal-form": _parse_dfm,
    }.get(lang, _parse_unknown)
    try:
        return handler(file_info, source)
    except Exception as exc:
        log.warning("Special handler failed", path=file_info.path, error=str(exc))
        return _empty(file_info, parse_errors=[str(exc)])


# ---------------------------------------------------------------------------
# OpenAPI handler
# ---------------------------------------------------------------------------


def _parse_openapi(file_info: FileInfo, source: bytes) -> ParsedFile:
    """Parse OpenAPI 2 / 3 YAML or JSON specs."""
    try:
        import yaml  # pyyaml, already in dependencies
    except ImportError:
        return _empty(file_info, parse_errors=["pyyaml not installed"])

    try:
        data = yaml.safe_load(source.decode("utf-8", errors="replace"))
    except Exception as exc:
        return _empty(file_info, parse_errors=[f"YAML parse error: {exc}"])

    if not isinstance(data, dict):
        return _empty(file_info, parse_errors=["Not a YAML mapping"])

    # Confirm it's an OpenAPI/Swagger spec
    if "openapi" not in data and "swagger" not in data:
        return _empty(file_info, parse_errors=["Not an OpenAPI/Swagger spec"])

    symbols: list[Symbol] = []
    _title = (data.get("info") or {}).get("title", file_info.path)

    paths = data.get("paths") or {}
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, spec in methods.items():
            if method.lower() in ("get", "post", "put", "patch", "delete", "head", "options"):
                op_id = (spec or {}).get("operationId", f"{method.upper()} {path}")
                summary = (spec or {}).get("summary")
                symbols.append(
                    Symbol(
                        id=f"{file_info.path}::{op_id}",
                        name=op_id,
                        qualified_name=op_id,
                        kind="function",
                        signature=f"{method.upper()} {path}",
                        start_line=1,
                        end_line=1,
                        docstring=summary,
                        visibility="public",
                        language="openapi",
                    )
                )

    # Components / schemas as type symbols
    components = (data.get("components") or {}).get("schemas") or (data.get("definitions") or {})
    for schema_name in components:
        symbols.append(
            Symbol(
                id=f"{file_info.path}::{schema_name}",
                name=schema_name,
                qualified_name=schema_name,
                kind="type_alias",
                signature=f"schema {schema_name}",
                start_line=1,
                end_line=1,
                docstring=None,
                visibility="public",
                language="openapi",
            )
        )

    return ParsedFile(
        file_info=file_info,
        symbols=symbols,
        imports=[],
        exports=[s.name for s in symbols],
        docstring=str(data.get("info", {}).get("description", "")) or None,
        parse_errors=[],
    )


# ---------------------------------------------------------------------------
# Dockerfile handler
# ---------------------------------------------------------------------------

_FROM_RE = re.compile(r"^\s*FROM\s+([^\s]+)", re.IGNORECASE)
_COPY_RE = re.compile(r"^\s*COPY\s+", re.IGNORECASE)
_RUN_RE = re.compile(r"^\s*RUN\s+", re.IGNORECASE)
_ENTRYPOINT_RE = re.compile(r"^\s*(?:ENTRYPOINT|CMD)\s+(.+)", re.IGNORECASE)
_EXPOSE_RE = re.compile(r"^\s*EXPOSE\s+(\d+)", re.IGNORECASE)
_ENV_RE = re.compile(r"^\s*ENV\s+(\w+)", re.IGNORECASE)
_ARG_RE = re.compile(r"^\s*ARG\s+(\w+)", re.IGNORECASE)


def _parse_dockerfile(file_info: FileInfo, source: bytes) -> ParsedFile:
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()
    imports: list[Import] = []
    symbols: list[Symbol] = []

    for lineno, line in enumerate(lines, start=1):
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue

        # FROM → import
        m = _FROM_RE.match(line)
        if m:
            image = m.group(1)
            imports.append(
                Import(
                    raw_statement=line.strip(),
                    module_path=image,
                    imported_names=[image],
                    is_relative=False,
                    resolved_file=None,
                )
            )
            continue

        # ENTRYPOINT / CMD → entry-point symbol
        m = _ENTRYPOINT_RE.match(line)
        if m:
            name = "entrypoint" if "ENTRYPOINT" in line.upper() else "cmd"
            symbols.append(
                Symbol(
                    id=f"{file_info.path}::{name}",
                    name=name,
                    qualified_name=name,
                    kind="function",
                    signature=line.strip(),
                    start_line=lineno,
                    end_line=lineno,
                    docstring=None,
                    visibility="public",
                    language="dockerfile",
                )
            )
            continue

        # EXPOSE → constant
        m = _EXPOSE_RE.match(line)
        if m:
            port = m.group(1)
            symbols.append(
                Symbol(
                    id=f"{file_info.path}::EXPOSE_{port}",
                    name=f"EXPOSE_{port}",
                    qualified_name=f"port_{port}",
                    kind="constant",
                    signature=line.strip(),
                    start_line=lineno,
                    end_line=lineno,
                    docstring=None,
                    visibility="public",
                    language="dockerfile",
                )
            )

    return ParsedFile(
        file_info=file_info,
        symbols=symbols,
        imports=imports,
        exports=[],
        docstring=None,
        parse_errors=[],
    )


# ---------------------------------------------------------------------------
# Makefile handler
# ---------------------------------------------------------------------------

# Matches: target_name: [prerequisites...]
_TARGET_RE = re.compile(r"^([a-zA-Z0-9_][a-zA-Z0-9_\-./]*):[^=]")
_INCLUDE_RE = re.compile(r"^include\s+(.+)", re.IGNORECASE)
_PHONY_RE = re.compile(r"^\.PHONY\s*:\s*(.+)")


def _parse_makefile(file_info: FileInfo, source: bytes) -> ParsedFile:
    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()
    symbols: list[Symbol] = []
    imports: list[Import] = []
    phony_targets: set[str] = set()

    # First pass: collect .PHONY targets
    for line in lines:
        m = _PHONY_RE.match(line)
        if m:
            phony_targets.update(m.group(1).split())

    # Second pass: extract targets
    for lineno, line in enumerate(lines, start=1):
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue

        m = _TARGET_RE.match(line)
        if m:
            target = m.group(1)
            if not target.startswith("."):  # skip .PHONY, .SUFFIXES, etc.
                symbols.append(
                    Symbol(
                        id=f"{file_info.path}::{target}",
                        name=target,
                        qualified_name=target,
                        kind="function",
                        signature=f"{target}:",
                        start_line=lineno,
                        end_line=lineno,
                        docstring=None,
                        visibility="public",
                        language="makefile",
                    )
                )
            continue

        m = _INCLUDE_RE.match(line)
        if m:
            include_path = m.group(1).strip()
            imports.append(
                Import(
                    raw_statement=line.strip(),
                    module_path=include_path,
                    imported_names=[],
                    is_relative=True,
                    resolved_file=None,
                )
            )

    return ParsedFile(
        file_info=file_info,
        symbols=symbols,
        imports=imports,
        exports=[s.name for s in symbols],
        docstring=None,
        parse_errors=[],
    )


# ---------------------------------------------------------------------------
# Delphi Form (.dfm) handler
# ---------------------------------------------------------------------------

# object/inherited keyword followed by InstanceName: ClassName
_DFM_OBJECT_RE = re.compile(
    r"^\s*(?:object|inherited)\s+(\w+)\s*:\s*(\w+)", re.IGNORECASE
)
# Caption property — single-quoted string ('' is Delphi's escaped single quote)
_DFM_CAPTION_RE = re.compile(
    r"^\s*Caption\s*=\s*'((?:[^']|'')*)'", re.IGNORECASE
)
_DFM_END_RE = re.compile(r"^\s*end\s*$", re.IGNORECASE)
_DFM_BINARY_MAGIC = b"TPF0"


def _parse_dfm(file_info: FileInfo, source: bytes) -> ParsedFile:
    """Parse a Delphi text-format form file (.dfm).

    Establishes the three-way association that makes DFM files meaningful:

        Caption  ('Sales Report Options')
            ↕
        Form class  (TSalesReportOptionsDialog)    ← used in Pascal type decls
            ↕
        Unit name  (Sales.Reporting.Options.Dialog) ← always == file stem

    Root symbol uses the *class* name (e.g. ``TSalesReportOptionsDialog``),
    not the instance name, because the class name is what appears in ``.pas``
    type declarations and cross-file references.

    Direct child components (depth 1 inside root) are emitted as ``variable``
    symbols — the published fields of the form class.  Deeper nesting is
    omitted to keep the symbol list concise.

    A companion ``.pas`` import with ``resolved_file`` set links this DFM node
    to its Pascal unit in the dependency graph so traversal from either file
    reaches the other without any extra lookup.
    """
    if source[:4] == _DFM_BINARY_MAGIC:
        return _empty(file_info, parse_errors=["Binary .dfm — text parsing skipped"])

    text = source.decode("utf-8", errors="replace")
    lines = text.splitlines()

    # Unit name == filename stem (Delphi enforces this convention)
    unit_name = Path(file_info.path).stem  # "enquiryU" or "foo.bar.Dialog"

    symbols: list[Symbol] = []
    depth = 0
    root_instance: str | None = None
    root_class: str | None = None
    root_start: int = 1
    root_end: int = len(lines)
    caption: str | None = None

    # Stack entries: (instance_name, class_name, start_line)
    stack: list[tuple[str, str, int]] = []

    for lineno, line in enumerate(lines, start=1):
        m = _DFM_OBJECT_RE.match(line)
        if m:
            depth += 1
            instance_name, class_name = m.group(1), m.group(2)
            stack.append((instance_name, class_name, lineno))
            if depth == 1:
                root_instance = instance_name
                root_class = class_name
                root_start = lineno
            continue

        if _DFM_END_RE.match(line):
            if stack:
                inst, cls, start = stack.pop()
                if depth == 2:
                    # Direct child of root — published field of the form class
                    symbols.append(
                        Symbol(
                            id=f"{file_info.path}::{root_class}::{inst}",
                            name=inst,
                            qualified_name=f"{unit_name}.{root_class}.{inst}",
                            kind="variable",
                            signature=f"{inst}: {cls}",
                            start_line=start,
                            end_line=lineno,
                            docstring=None,
                            visibility="public",
                            language="pascal-form",
                            parent_name=root_class,
                        )
                    )
                elif depth == 1:
                    root_end = lineno
            depth = max(depth - 1, 0)
            continue

        # Caption of the root form — only at depth 1 (root's own properties)
        if depth == 1 and caption is None:
            cm = _DFM_CAPTION_RE.match(line)
            if cm:
                caption = cm.group(1).replace("''", "'")

    if root_class is None:
        return _empty(file_info, parse_errors=["No root object found in .dfm"])

    caption_display = f"'{caption}'" if caption else "(no Caption)"
    n_components = sum(1 for s in symbols if s.kind == "variable")
    docstring = (
        f"Form: {root_class} | Caption: {caption_display} | Unit: {unit_name}"
        + (f" | {n_components} component(s)" if n_components else "")
    )

    # Root form symbol — class name as identifier, instance name in signature
    sig_caption = f" (Caption: {caption_display})" if caption else ""
    root_sym = Symbol(
        id=f"{file_info.path}::{root_class}",
        name=root_class,
        qualified_name=f"{unit_name}.{root_class}",
        kind="class",
        signature=f"{root_instance}: {root_class}{sig_caption}",
        start_line=root_start,
        end_line=root_end,
        docstring=docstring,
        visibility="public",
        language="pascal-form",
    )
    symbols.insert(0, root_sym)

    # Companion .pas import — resolved_file lets the graph resolver find the
    # Pascal unit without any name-to-path lookup.
    companion_path = str(Path(file_info.path).with_suffix(".pas"))
    imports = [
        Import(
            raw_statement=f"{{companion unit: {unit_name}}}",
            module_path=unit_name,
            imported_names=[root_class],
            is_relative=True,
            resolved_file=companion_path,
        )
    ]

    return ParsedFile(
        file_info=file_info,
        symbols=symbols,
        imports=imports,
        exports=[root_class],
        docstring=docstring,
        parse_errors=[],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_unknown(file_info: FileInfo, source: bytes) -> ParsedFile:
    return _empty(file_info, parse_errors=[f"No special handler for {file_info.language}"])


def _empty(file_info: FileInfo, parse_errors: list[str] | None = None) -> ParsedFile:
    return ParsedFile(
        file_info=file_info,
        symbols=[],
        imports=[],
        exports=[],
        docstring=None,
        parse_errors=parse_errors or [],
    )
