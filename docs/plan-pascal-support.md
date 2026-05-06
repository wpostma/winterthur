# Pascal Support Plan for Repowise

Produced after hands-on evaluation of repowise v0.2.2 against a Delphi/Pascal monorepo
(`flux_persist`). The evaluation session diagnosed `get_context(file)` returning
"Target not found" for every Pascal file. This plan explains exactly why and exactly
what to fix.

---

## What Already Works (Do Not Break)

Before listing gaps, record what the codebase already has correct for Pascal:

| Component | File | Status |
|---|---|---|
| Extension -> language mapping | `packages/core/src/repowise/core/ingestion/models.py` | `.pas`, `.dpr`, `.dpk`, `.pp` -> `"pascal"` ok |
| `.dfm` form files | `models.py` | `.dfm` -> `"pascal-form"` ok |
| Entry-point detection | `traverser.py` | `.dpr` and `.dpk` flagged as entry points ok |
| `LanguageConfig` entry | `parser.py` | `"pascal"` entry exists with node type mappings ok |
| tree-sitter loader | `parser.py` | `_try_load("pascal", ...)` attempted ok |

The foundation is there. The tool is not ignoring Pascal files. It is discovering them,
tagging them, and then silently producing empty results because two things are missing.

---

## Root Cause of "Target Not Found"

The failure chain has exactly two links:

**Link 1 -- `tree_sitter_pascal` not installed in the venv.**
`parser.py` calls `_try_load("pascal", ...)` which catches all exceptions and logs at
DEBUG level. If `tree_sitter_pascal` is absent from the venv, the pascal entry is simply
absent from `_LANGUAGE_REGISTRY`. The file is traversed and tagged correctly, but
`parse_file()` receives `language_obj = None` and returns an empty `ParsedFile`.

Confirmed: `tree_sitter_pascal` v0.9.1 is installed in the system Python
(`C:\Users\warre\AppData\Local\Python\pythoncore-3.14-64`) but NOT in
`C:\vsdev\repowise\.venv`.

**Link 2 -- No `pascal.scm` query file.**
`packages/core/queries/` contains `.scm` files for c, cpp, go, java, javascript,
kotlin, python, ruby, rust, typescript -- but not pascal. Even if the language object
loads successfully, `ASTParser` looks for `queries/pascal.scm` and finds nothing, so
it extracts zero symbols and zero imports.

Both links must be fixed. Either one alone produces an empty `ParsedFile`, which
results in a page with no content, which the MCP layer returns as "Target not found".

---

## Fix 1 -- Install tree-sitter-pascal in the venv

```bash
cd C:\vsdev\repowise
.venv\Scripts\pip install tree-sitter-pascal
```

Verify:
```bash
.venv\Scripts\python -c "import tree_sitter_pascal; print(tree_sitter_pascal.__version__)"
```

Also add it to the project's dependency manifest so it survives a `uv sync` or
`pip install -e .`:

- `packages/core/pyproject.toml` -- add `tree-sitter-pascal` to `[project.dependencies]`
  alongside the other `tree-sitter-*` packages.

### Packaging note

The local `thirdparty/tree-sitter-pascal` checkout should be treated as a working
directory only, not as the long-term integration model.

The desired end state is that Pascal support works through the regular install
pipeline:

- `pip install repowise`
- `uv sync`
- `pip install -e .`

In other words, `tree-sitter-pascal` should eventually be pulled in as a normal
Python dependency, not maintained as an ad hoc vendored git checkout inside the
repo. The `thirdparty/` copy is useful for development and investigation, but it
should not be the source of truth for installation.

---

## Fix 2 -- Write `packages/core/queries/pascal.scm`

This is the primary work item. The query file must follow the same capture-name
conventions as every other `.scm` file:

| Capture name | Meaning |
|---|---|
| `@symbol.def` | Full definition node (determines line range) |
| `@symbol.name` | Name identifier node |
| `@symbol.params` | Parameter list node (optional) |
| `@symbol.modifiers` | Visibility keyword nodes (optional) |
| `@import.statement` | Full `uses` clause node |
| `@import.module` | Individual unit name in the `uses` clause |

The `LanguageConfig` for `"pascal"` already lists the tree-sitter-pascal node type
names to use:

```python
symbol_node_types={
    "declType": "class",    # type declaration (class, record, interface)
    "declProc": "function", # interface-section procedure/function declaration
    "defProc": "function",  # implementation-section procedure/function definition
},
import_node_types=["declUses"],   # uses clause
```

A starting skeleton for `pascal.scm`:

```scheme
; =============================================================================
; repowise -- Pascal / Delphi symbol and import queries
; tree-sitter-pascal >= 0.9
; =============================================================================

; ---------------------------------------------------------------------------
; Type declarations (class, record, interface, enum)
; ---------------------------------------------------------------------------

(declType
  name: (ident) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Procedure / function declarations (interface section)
; ---------------------------------------------------------------------------

(declProc
  name: (ident) @symbol.name
  params: (formalParms)? @symbol.params
) @symbol.def

; ---------------------------------------------------------------------------
; Procedure / function definitions (implementation section)
; ---------------------------------------------------------------------------

(defProc
  name: (ident) @symbol.name
  params: (formalParms)? @symbol.params
) @symbol.def

; ---------------------------------------------------------------------------
; Uses clauses (imports)
; ---------------------------------------------------------------------------

(declUses
  (usedUnits
    (usedUnit
      (ident) @import.module
    )
  )
) @import.statement
```

**WARNING:** The node type names above are best guesses derived from the existing
`LanguageConfig`. They must be verified against the actual tree-sitter-pascal grammar
before committing. The correct way to verify:

```python
from tree_sitter import Parser, Language
import tree_sitter_pascal
lang = Language(tree_sitter_pascal.language())
parser = Parser(lang)
tree = parser.parse(b"unit foo; interface uses SysUtils; type TBar = class end; implementation end.")
print(tree.root_node.sexp())
```

Read the s-expression output to find the actual node type names, then update the `.scm`
accordingly. Do not guess -- wrong node names produce silently empty query results,
which is the same failure mode we are trying to escape.

---

## Fix 3 -- Monorepo Detection for Delphi

**File:** `packages/core/src/repowise/core/ingestion/traverser.py`

**Problem:** `_detect_monorepo()` only recognises `pyproject.toml`, `package.json`,
`Cargo.toml`, `go.mod` as package manifest files. A Delphi monorepo uses `.groupproj`
(project group) to declare its structure.

**Fix:** The detection loop in `_detect_monorepo()` currently does
`candidate.name not in _MANIFEST_FILES`. Extend it to also match on suffix:

```python
# existing exact-name match
if candidate.name not in _MANIFEST_FILES and candidate.suffix not in {".groupproj"}:
    continue
```

This is a two-line change in `_detect_monorepo()`. No model changes needed --
`PackageInfo.manifest_file` is already a plain string.

---

## Fix 4 -- Pascal-Specific Parsing Nuances

These are not blockers for initial support but are required for accurate results.

### 4a -- Unit name from declaration, not filename

In Pascal the authoritative module name is declared inside the file:

```pascal
unit cocinasync.flux.store;
```

The filename (`cocinasync.flux.store.pas`) happens to match here, but this is
convention, not a rule. `Import.module_path` should store the unit name as it
appears in the `uses` clause. Resolving that name to a file path requires a
post-processing pass in `graph.py` that walks the known file list, parses each
file's own `unit <name>;` declaration, and builds a name-to-path index.

### 4b -- Interface-section uses vs. implementation-section uses

Pascal `uses` clauses appear in two places with different semantics:

```pascal
interface
  uses A, B;        { public dependencies }
implementation
  uses C, D;        { private dependencies }
```

The tree-sitter-pascal grammar likely wraps these in different parent nodes
(`interfaceSection` vs `implementationSection`). Capturing both is correct.
Labelling them differently in the index would improve dependency graph accuracy
but is optional for v1.

### 4c -- `{$IFDEF}` conditional compilation

Conditional imports are common in cross-platform Delphi code:

```pascal
uses
  {$IFDEF TESTINSIGHT}
  TestInsight.DUnitX,
  {$ENDIF}
  cocinasync.global;
```

tree-sitter-pascal may treat `{$IFDEF}` blocks as opaque nodes. If so, conditional
imports will be invisible to the query. This is acceptable for v1 -- document it
and move on. Do not attempt to evaluate preprocessor conditions.

### 4d -- Visibility sections in Pascal classes

Pascal classes use named visibility sections rather than per-member keywords:

```pascal
type
  TBaseStore = class(TInterfacedObject)
  private
    FUpdateCount: UInt64;
  public
    property UpdateCount: UInt64 read FUpdateCount;
  end;
```

The current `visibility_fn` for pascal is `_public_by_default`. A better
implementation would track which section (`private`, `protected`, `public`,
`published`, `strict private`, `strict protected`) a symbol falls under.
For v1, exposing only `public` and `published` members to `get_context()` is
the right default -- those are what callers can see.

---

## Fix 5 -- `.dfm` Form Files

**Current state:** `.dfm` is tagged as `"pascal-form"` but there is no
`LanguageConfig` entry for `"pascal-form"` and no query file.
`parse_file()` will fail silently.

**Minimum viable fix (30 minutes):** Add `"pascal-form"` to `_PASSTHROUGH_LANGUAGES`
in `parser.py` so it is explicitly silenced rather than silently failing.

**Better fix (optional, ~4 hours):** `.dfm` files are plain-text Delphi form
definitions. A simple line-by-line parser (no tree-sitter needed) could extract:
- Root form/frame class name and its base class (`object CustomerDemoForm: TForm`)
- Top-level component names and types

This lets `get_context()` report "this is `TCustomerDemoForm`, a `TForm`, containing
a `TEdit`, `TButton`, `TLabel`" -- useful context when navigating a VCL/FMX codebase.

---

## Testing the Fix

After implementing Fixes 1 and 2:

```bash
cd C:\vsdev\repowise
# Re-index the flux_persist repo
python -m repowise reindex C:\delphidev\ideal\flux_persist

# Test get_context via CLI
python -m repowise context cocinasync/cocinasync.flux.store.pas
```

Expected: symbols list includes `TBaseStore` (class), `RegisterForUpdates` (method),
`WaitForUpdate` (method); imports list includes `cocinasync.flux.action`,
`cocinasync.async`, `System.SyncObjs`.

If symbols are still empty after Fixes 1+2, run the s-expression diagnostic (Fix 2)
to verify the actual node type names in the grammar.

---

## Summary: Effort vs. Impact

| Fix | Effort | Impact |
|---|---|---|
| Fix 1: install tree-sitter-pascal in venv | 5 min | Unblocks everything else |
| Fix 2: write `pascal.scm` | 2-4 hours | Core feature -- enables `get_context()` |
| Fix 3: monorepo detection for `.groupproj` | 1 hour | Correct structure in `get_overview()` |
| Fix 4a: unit name resolution | 2-3 hours | Accurate cross-file dependency graph |
| Fix 4b-4d: parsing nuances | 1-2 hours each | Higher accuracy, not blockers |
| Fix 5: `.dfm` passthrough / parser | 30 min / 4 hours | Cleanliness / optional enrichment |

**Fixes 1 and 2 together are the 80% solution.** Everything else is refinement.
