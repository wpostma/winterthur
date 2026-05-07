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

## Developer workflow — use `uv`, do not activate the venv

This repo is **uv-native**. `uv.lock` is committed. `[tool.uv.sources]`
binds `tree-sitter-pascal` to the local fork submodule (the PyPI
package is not the right version — it's missing identifier-`$`
continuation, bare `raise`, kSafecall/kSealed regex fixes, modernised
bindings). `pip install tree-sitter-pascal` will silently give you the
wrong grammar; **don't**.

### The commands you actually run

```powershell
# Once after a fresh clone (and after pulling pyproject.toml / uv.lock changes)
git submodule update --init --recursive
uv sync --extra dev                                    # NOTE: --extra dev — see footgun below

# Daily
.\test.ps1                                             # ← canonical test entry point
uv run pascalparser doctor                             # smoke-check all 9 grammars load
uv run pascalparser symbols path\to\Unit.pas
uv run pascalparser parse   path\to\Unit.pas --depth 3 # folded source view (see below)
uv run pascalparser metrics path\to\Unit.pas --json    # codereview-skill contract
```

### Subcommand: `parse` (folded source view)

`pascalparser parse FILE --depth N` renders the source code with the
bodies of nodes deeper than N folded into language-comment elisions
like `// ... (lines 29-50 elided)`. Use it to get an LLM-friendly
outline of a file without loading the whole thing.

| Depth | What you get | When to use |
|---|---|---|
| `--depth 1` | Top-level shape only: unit/module decl, `interface`/`uses`, public type signatures, function signatures with bodies folded. Roughly one screen for any file. | First look at an unfamiliar file; deciding which units to read in full. |
| `--depth 2` | Adds one level of nesting — the contents of `type` blocks, the top-level statements inside each function (but their nested control-flow bodies are still folded). | Skimming a unit's public API plus a hint of how its big procedures are structured. |
| `--depth 3` | The sweet spot for a god-method audit on a real Delphi unit. Procedure bodies show their top-level `if` / `case` / `try` shape; only the deepest blocks are folded. | The depth Warren reaches for most often when reviewing legacy Pascal. |

Real example on a ~190-line Delphi unit at `--depth 3`:

```pascal
unit ManagerMenu;

interface

uses
  // ... (lines 11-13 elided)

type
// ... (lines 17-62 elided)

implementation

uses
  // ... (lines 68-80 elided)

{$R *.DFM}

procedure TManagerForm.ShowMenu(const Drawer, EmployeeUniqueID: Integer);
// ... (lines 85-114 elided)

procedure TManagerForm.PullButtonClick(Sender: TObject);
// ... (lines 118-139 elided)

procedure TManagerForm.ReportButtonClick(Sender: TObject);
// ... (lines 142-145 elided)

procedure TManagerForm.CashOutClick(Sender: TObject);
// ... (lines 154-171 elided)
```

The signature of every procedure is preserved; bodies are folded with
their exact line ranges so you can ask the user (or yourself) "show me
lines 85-114" when one looks worth reading in full. **For Pascal,
`--depth 3` is usually the right starting point.** For Python files
`--depth 1` is often enough; `--depth 2` reveals nested function
bodies.

Other parse modes:

```powershell
# Errors flagged inline with a language-comment annotation
uv run pascalparser parse path\to\malformed.pas --depth 1
# Output ends up with lines like:
#   unit Foo;  // ! ERROR ! tree-sitter could not parse this region

# Raw AST dump (was the old default; now opt-in for grammar-coverage debugging)
uv run pascalparser parse path\to\Unit.pas --depth 4 --debug

# Just the parse-error nodes (implies --debug)
uv run pascalparser parse path\to\Unit.pas --errors-only
```

### Subcommand: `symbols` (terse symbol & import dump)

The most token-efficient view of a unit. **For LLM consumption this is
often the right starting point** — it gives the full table of
classes/methods/functions with line numbers in roughly one line per
symbol, no body content, no metrics noise.

```powershell
uv run pascalparser symbols path\to\Unit.pas
uv run pascalparser symbols path\to\Unit.pas --json              # machine-readable
uv run pascalparser symbols path\to\Unit.pas --regex "^TOrder\." # filter to TOrder methods
uv run pascalparser symbols path\to\Unit.pas --regex "refund"    # case-insensitive substring
```

`--regex PATTERN` filters by Python regex match (`re.search` semantics)
against **both** symbol display names (`<Class>.<Method>` or bare
`<Name>`) **and** import module paths (Pascal `uses ideal.bo.types`).
Case-insensitive by default — Pascal is — pass `--case-sensitive` if
you want the literal pattern. Each section's header becomes
`symbols (N of M matching /pattern/i)` and `imports (N of M matching /pattern/i)`,
so you see both the match count and the haystack size; an empty match
prints `(no symbol names matched)` / `(no import paths matched)` rather
than silently emitting nothing.

**Side benefit**: regex filtering surfaces Pascal's duplicate
forward-decl + body symbols. A method declared in `interface` and
defined in `implementation` shows up as two records (one with
`kind=method, parent=TOrder, name=Foo`, one with
`kind=function, parent=None, name=TOrder.Foo`), both rendering to the
same display string, so a regex that targets the display string finds
the pair in one go.

Sample output for a real Delphi unit (text mode):

```
path\to\OrderDM.pas (pascal)
  errors: 112
  symbols (386):
      100  class        TOrderSubType
      125  class        TPriceScheduleInfo
      127  method       TPriceScheduleInfo.GenerateCacheKey
      143  class        TItemCard
      206  method       TItemCards.AddItem
      221  method       TItemCards.ProcessScanCards
      226  class        TOrder
      ...
  imports (38):
      Windows
      Messages
      ...
```

**Display rule**: methods are shown as `<Class>.<Method>`, top-level
functions as bare `<Name>`. The full
path-prefixed `qualified_name` (e.g. `path.to.Unit.TOrder.Method`)
exists in the JSON output and on the underlying `Symbol` dataclass for
future cross-file resolver use, but the text dump strips it because
the file is named on the line directly above the symbol list — the
prefix would be 386× redundant noise.

When to reach for symbols vs metrics vs parse:

| You want… | Use |
|---|---|
| "What's defined in this file? Just the names and lines." | `symbols` |
| "What does this file's structure look like, with signatures and folded bodies?" | `parse --depth 3` |
| "How complex is each function — if/case/loop/exit counts, nesting depth?" | `metrics` |
| "Does this unit have a parse error, missing `end.`, begin/end imbalance?" | `metrics` (validates) or `parse --errors-only` |

### Subcommand: `metrics` (per-function structural metrics)

```powershell
# JSON for codereview-skill consumption — contract in metrics-tool-spec.md
uv run pascalparser metrics path\to\Unit.pas

# Human-readable summary instead
uv run pascalparser metrics path\to\Unit.pas --text

# --dir is a path PREFIX (not a "scan dir" flag). Positional args are the
# real selection — literal names or globs, resolved relative to --dir.
uv run pascalparser metrics --dir C:\path\to\Source AdjustDrawer.pas

# Glob matched relative to --dir (shallow)
uv run pascalparser metrics --dir C:\path\to\Source "Order*.pas"

# Recursive multi-extension scan, single call
uv run pascalparser metrics --dir C:\path\to\repo --recurse "*.py" "*.ts" "*.pas"

# Default cap is 30 files; raise with --limit
uv run pascalparser metrics --dir C:\path\to\Source --recurse "*.pas" --limit 200
```

Counter fields (`if_count`, `try_count`, `raise_count`, …) are **omitted
when zero** to keep JSON terse for LLM consumers — a function with no
try/except simply has no try/except keys instead of three zero-valued
fields wasting tokens. Always-present fields:
`name, qualified_name, kind, line_start, line_end, loc_total,
loc_effective, decision_points`. `params` only appears when non-empty.

Per-file structural validation runs on Pascal: malformed units (begin/end
imbalance, missing `end.`, tree-sitter parse error) produce an `errors`
array on the file record and a non-zero exit code. Healthy files have
no `errors` key at all.

**Use `.\test.ps1`, not bare `uv run pytest`.** The script is two lines —
`uv sync --extra dev` then `uv run pytest` — and that ordering is
load-bearing. It exists to communicate intent: re-sync the dev extras
every time, then run the tests through the project venv. See the
footgun below for why this matters.

**Do not** `.venv\Scripts\activate` — `uv run` invokes the right
interpreter from `.venv\` directly. Activating works, but it's extra
ceremony with no payoff and it confuses future-Claude into running
bare `pytest` / `pascalparser` and getting the wrong env.

### Footgun: `uv sync` without `--extra dev` silently breaks pytest

`pytest` lives in `[project.optional-dependencies] dev` in
`pyproject.toml`. **Plain `uv sync` does NOT install optional
extras**, so `.venv\Scripts\` ends up with no `pytest.exe`. When you
then run `uv run pytest`, uv can't find pytest in the venv and falls
through to whatever Python on PATH has it — typically a system Python
that has no idea about this project's `src/` layout. You get:

```
ModuleNotFoundError: No module named 'pascalparser'
```

…on a Python version that isn't even 3.11. This is what `test.ps1`
defends against: it always re-syncs `--extra dev` first, so the venv
has pytest, and `uv run pytest` resolves correctly inside the venv.

If you ever see that ModuleNotFoundError, run `.\test.ps1` (or
`uv sync --extra dev` manually). Don't go chasing import-path or
`PYTHONPATH` red herrings.

### When to reach for something other than `uv run`

| Situation | Use |
|---|---|
| Ad-hoc REPL or script in this repo | `uv run python …` |
| Re-pin lockfile after editing `pyproject.toml` | `uv lock` then commit `uv.lock` |
| Add a runtime dep | `uv add <pkg>` (edits pyproject + lock) |
| Add a dev dep | `uv add --dev <pkg>` |
| Wire `pascalparser.exe` into the codereview skill | point its `metrics_tool` at `C:\vsdev\pascalparser\.venv\Scripts\pascalparser.exe` (the shim `uv sync` already wrote) |
| Install globally on PATH outside this repo | `uvx --from . pascalparser …` or `pipx install .` — **only** for downstream use, never for dev work in this tree |

### Plain-`pip` fallback (only if `uv` isn't available)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pip install -e thirdparty\tree-sitter-pascal     # pip won't honor [tool.uv.sources]
```

Note the second line: with raw pip you have to install the submodule
editable yourself, because `[tool.uv.sources]` is a uv-only feature.
This is why `uv` is the supported path — it's one command instead of
two-and-a-footgun.

### Submodule init

A clone done without `--recurse-submodules` will leave
`thirdparty/tree-sitter-pascal/` empty and `uv sync` will fail with a
build error pointing at that path. Fix:

```powershell
git submodule update --init --recursive
uv sync
```

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
