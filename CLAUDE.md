# pascalparser — Claude Code project notes

Project-local conventions on top of the global `~/.claude/CLAUDE.md`.
Read both before making changes.

## What this project is

A Pascal/Delphi parser plus lint and metrics tooling, built on
tree-sitter. The underlying parser is multi-language (carried over from
repowise). The project may be renamed `codeparser` when the
multi-language scope is more authoritative than the Pascal focus.

## Where things live

- **Parser source:** `src/pascalparser/parser.py` (multi-language;
  do NOT strip out other languages).
- **Models:** `src/pascalparser/models.py` (FileInfo, ParsedFile,
  Symbol, Import). Plain dataclasses for speed.
- **Tree-sitter queries:** `src/pascalparser/queries/<lang>.scm`.
  Capture-name conventions: `@symbol.def`, `@symbol.name`,
  `@symbol.params`, `@symbol.modifiers`, `@symbol.receiver`,
  `@import.statement`, `@import.module`. Same conventions as repowise.
- **Pascal grammar fork:** `thirdparty/tree-sitter-pascal/` (git
  submodule of `git@github.com:wpostma/tree-sitter-pascal.git`).
- **Tests:** `tests/unit/test_parser.py` (ported from
  `repowise/tests/unit/ingestion/test_parser.py`).
- **Docs:** `docs/plan-pascal-support.md` is the original diagnosis of
  the two-missing-links problem (no `tree_sitter_pascal` in venv, no
  `pascal.scm` query) — it's history but worth reading once.

## Things that already worked in repowise (don't break)

| Thing | Location | Status |
|---|---|---|
| Extension → language mapping | `models.py` | `.pas .dpr .dpk .pp` → `pascal`; `.dfm` → `pascal-form` |
| LanguageConfig for pascal | `parser.py` | exists in LANGUAGE_CONFIGS dict |
| tree-sitter loader | `parser.py` | `_try_load("pascal", lambda: Language(__import__("tree_sitter_pascal").language()))` |
| pascal.scm query | `src/pascalparser/queries/pascal.scm` | symbols + imports captures |

## Single unit of compilation rule

This tool **must** work on individual source files. No requirement that
a `.dproj` exist, that the unit's `uses` list resolve, or that `.dcu`
files be available. Treat each input file as a self-contained AST. The
parser's `Import` records track what the file *says* it depends on; we
do not follow those imports across files.

This is the architectural choice that distinguishes pascalparser from
Embarcadero's `AuditsCLI` (which requires a working `.dproj` build) and
from DelphiAST-based tooling (which often needs `uses` resolution to
produce useful output).

## Coding conventions

- **Python files:** LF line endings (per global CLAUDE.md). Verify with
  `file <path>` and run `dos2unix` if needed.
- **Type hints:** required on public functions. `from __future__ import
  annotations` is at the top of every module (carried over from
  repowise).
- **Dataclasses, not Pydantic.** The pipeline may scan tens of thousands
  of files; runtime validation overhead would add up.
- **No language-specific branches in `ASTParser`.** Per-language
  differences live in `LANGUAGE_CONFIGS` (in `parser.py`) and in the
  `.scm` query files. Adding a new language = one config entry + one
  query file. No new module, no new class.
- **Logging:** `structlog` only. Mirror the existing style from
  repowise.
- **Tests:** `pytest`. Snapshot/golden tests via `pytest-snapshot` are
  fine for parser output.

## Building / running

```powershell
# Initial setup (assumes Python 3.11+)
uv sync                              # creates .venv, installs deps

# Or with plain pip
python3 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# Test
pytest

# CLI
pascalparser path/to/file.pas
pascalparser path/to/file.pas --json > metrics.json
```

The submodule needs to be initialised (`git submodule update --init`)
on a fresh clone if you didn't `--recurse-submodules`.

## Integration with the codereview skill

The CLI's `--json` output conforms to
`~/.claude/skills/codereview/metrics-tool-spec.md`. To wire it as the
skill's metrics tool:

```json
// ~/.claude/skills/codereview/config.json
{
  "metrics_tool": "/c/vsdev/pascalparser/.venv/Scripts/pascalparser.exe"
}
```

(Path adjustment needed depending on platform / venv location.)

## Submodule conventions

- Pull the submodule explicitly: `git submodule update --remote
  thirdparty/tree-sitter-pascal` to advance to the fork's HEAD.
- Submodule is **pinned to a specific SHA** in the parent repo. Bumping
  the SHA is a deliberate commit, not an automatic action.

## Renaming to codeparser later

If/when we rename:
- Repo dir: `C:\vsdev\pascalparser` → `C:\vsdev\codeparser`
- Python package: `src/pascalparser/` → `src/codeparser/`
- Project name in `pyproject.toml`
- All `from pascalparser.X import Y` in tests
- This file (CLAUDE.md), the README, the codereview skill's
  `config.json`

Until that happens, "pascalparser" is the project identity.
