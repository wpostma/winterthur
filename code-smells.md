# code-smells.md — winterthur's catalogue of smells (fixed, kept, and pending)

This file is the project's running ledger of code smells we've identified
in the winterthur sources. Three categories:

- **Fixed** — smells we found and removed. Logged here as durable
  reference so the same shape isn't reintroduced and so the rationale
  for the fix is searchable.
- **Kept (deliberate)** — smells we identified, considered, and chose
  to leave in place. Each has a stated **trigger to revisit**; without
  one, an entry doesn't belong in this section.
- **Pending** — smells we identified but haven't fixed yet. Each entry
  notes why the fix has been deferred and what would change to promote
  it to "fix now."

The point is to make smells visible. A smell with no comment looks like
sloppiness; a smell with a name and a stated reason reads as a
considered decision. Anyone running `/codereview` against this repo or
skimming for cleanup work should consult this file before "fixing"
anything called out here — and should add an entry when they find
something new.

## Catalogue conventions

Each entry has:

- **Smell category** — a short name that captures the *shape* of the
  smell (so we can describe future occurrences with one word).
- **Severity if accidental** — what severity the smell *would* warrant
  if the author hadn't documented it.
- **Severity given context** — what we actually grade it as, here.
- **Sites** — file:line references to where the smell lives (or
  lived, for fixed entries).
- For *Kept*: a **Trigger to revisit** — concrete change that would
  obsolete the current shape and prompt a refactor.
- For *Pending*: a **Why deferred** note and a **Promote when…**
  trigger.

Rule for new entries: don't invent a category to defend a single site
— wait for the second occurrence and name the category then.

---

## ✅ Fixed

### 1. Silent try/except (no log, no record, no observation)

**Synonyms:** swallowed exception, mute fallback, pass-on-fail.

**Shape.** A `try: … except SomeException: <recover>` block that
returns a fallback value without logging, raising, or recording the
caught exception anywhere. The caller can't tell if recovery happened.

```python
try:
    from tree_sitter import QueryCursor
    cursor = QueryCursor(query)
    for match in cursor.matches(root_node):
        ...
except Exception:                # ← silent
    try:
        for item in query.matches(root_node):
            ...
    except Exception as exc:
        log.warning("query.matches() failed", error=str(exc))
return results
```

**Why it's a smell.** A silently-caught exception hides defects in two
directions:

1. The maintainer never sees that recovery happened, so the
   API-version-drift path being taken on every parse is invisible.
2. Real defects unrelated to API drift (a malformed query, a Unicode
   surrogate issue) get swallowed by the same blanket and look like a
   normal fallback.

A `# noqa: BLE001` on the line is *not* sufficient — it silences the
linter but preserves the silence at runtime.

**Severity if accidental:** 🔴 red. Two-layer silent excepts are the
classic mechanism behind "the bug only shows up in production" stories.

**Project rule:** silent try/except is **never acceptable** in this
codebase. Every recovery path must:

1. Log via structlog at minimum at `debug` level (preferably `warning`
   when the recovery indicates an environment/install problem).
2. Record on the responsible state object — for parser-internal sites,
   that's `ASTParser.caught_exceptions: list[CaughtException]`. The
   recording lets tests and downstream tools introspect "did anything
   go wrong silently?" without grepping logs.

**How fixed (2026-05-06).** Added `CaughtException` dataclass plus
`ASTParser.caught_exceptions` list and `_record_exception()` helper in
`src/winterthur/parser.py`. Refactored:

| Site (was) | Disposition |
|---|---|
| `parser.py:_run_query` outer except | Now `ASTParser._run_query` method, splits into `_run_query_modern` / `_run_query_legacy` free functions; both fallback arms call `_record_exception` |
| `parser.py:_get_query` except | Keeps explicit `log.warning` (broken `.scm` is actionable for maintainers) AND now also calls `_record_exception` |
| `commands/metrics.py:_walker_results_for` tree_sitter import except | Adds `log.warning` — install-integrity guard, would only fire if winterthur itself is broken (parser.py imports tree_sitter unconditionally) |
| `commands/smells.py:_scan_file` tree_sitter import except | Same treatment — `log.warning` |
| `commands/symbols.py:_combined_errors` tree_sitter import except | Same treatment — `log.warning` |
| `commands/metrics.py:_effective_loc` decode except | **Removed** — `bytes.decode("utf-8", errors="replace")` is a total operation, the try/except was dead code masquerading as defence |

**Tests.** `tests/unit/test_parser.py::TestCaughtExceptions` covers the
contract: a fresh parser has zero recorded exceptions, a clean parse
records none, a synthetic recovery appends with full context
(where/exc_type/message/file_path/language). Future silent-recovery
regressions would fail one of these tests.

**Sites in this repo (now):** Every site that *could* silently fall
through carries either `ASTParser._record_exception()` or an
explicit `structlog.warning(...)`. Search for `# noqa: BLE001` to find
the survivors — every remaining instance has a sibling log call and a
comment justifying the suppression.

---

## 🟡 Kept (deliberate)

### 2. Vacuous-but-deliberate conditional (placeholder branch)

**Synonyms:** future-divergence stub, decoy branch, parking-lot
conditional, "ifs in waiting."

**Shape.** An `if X: return EXPR else: return EXPR` (or equivalent
match/case, ternary, etc.) where both branches evaluate to **exactly
the same expression today**, but the *intent* is that one day they
should differ. The condition encodes a forecast, not a current
behaviour.

```python
if config.export_node_types:
    # Languages with explicit exports (TS, JS) — public top-level symbols
    return [s.name for s in symbols if s.visibility == "public" and s.parent_name is None]
# Languages where all top-level public symbols are exported (Python, Go, …)
return [s.name for s in symbols if s.visibility == "public" and s.parent_name is None]
```

**Why this looks like a bug.** A reader skimming the function thinks
"the branches do the same thing — either the condition is wrong, or
one branch is dead, or this is copy-paste rot." Tooling agrees: ruff
and pylint both flag identical-branch returns. `/codereview` calls it
a 🟡 yellow.

**Why it isn't a bug — when used deliberately.** The branch is a
**named seam for a planned future behaviour change.** TypeScript and
JavaScript have explicit `export` statements; the long-term plan is
that for those languages we should narrow the export list to symbols
named in actual `export …` AST nodes, rather than "every public
top-level symbol." That's not implemented yet — but the conditional
already names the seam and partitions the call sites that will diverge.
When the divergence lands, only one branch changes; no callers move,
no signature shifts, and the JSON/contract surface is stable.

**The price you pay.** The smell is visible to every reviewer until
the divergence is implemented. That's the whole reason this file
exists: so the smell is *named* rather than *invisible*.

**Severity if accidental:** 🟡 yellow.

**Severity given context:** 🟢 green-with-disclaimer.

**Trigger to revisit:** when we implement export-statement-anchored
filtering for TS/JS — i.e. when the if-branch should compute something
different from the else-branch.

**Test coverage today:** the else-branch is covered by
`test_exports_list` in `tests/unit/test_parser.py:175` (Python).
The if-branch (TS/JS) is not distinguished by any test, because the
two branches still produce the same output. **When the divergence is
implemented, a TS-specific export test must be added** that would
fail if the if-branch silently fell back to the else-branch behaviour.

**Sites in this repo:**

- `src/winterthur/parser.py:_derive_exports` — the canonical example.

---

## 🟠 Pending (identified, not yet fixed)

### 3. Misleading-comment dead line

**Shape.** A line of code that does nothing, accompanied by a comment
defending it ("tracked for clarity," "kept for symmetry," etc.). The
comment makes the line look intentional, which is worse than just
deleting it.

```python
for c in node.children:
    _walk(c, kinds, m, depth, anon_depth, language)

if bumped:
    depth -= 1  # noqa: F841  (tracked for clarity; recursion already restored)
```

`depth` is a parameter local to this stack frame; decrementing it
after the recursive `for` loop affects nothing. The comment claims
"recursion already restored" which would be true if `depth` were a
mutable container, but it isn't. The `# noqa: F841` exists because
ruff correctly flagged the unused write.

**Why deferred:** small isolated change, rolled into a separate
"micro-cleanup" PR rather than mixed into the silent-except work.

**Promote when…** anyone next touches `metrics_walker._walk` for any
reason. At that point: delete lines 310–311, add a one-line comment
near the parameter list noting that `depth` flows through stack
frames.

**Severity:** 🟡 yellow.

**Sites in this repo:**

- `src/winterthur/metrics_walker.py:311`.

### 4. Duplicated parse-error merge policy

**Shape.** The same multi-line "merge structural-validator errors with
parser-collected errors, dropping generic 'Parse error at line N'
entries when the validator already flagged a parse error" logic
appears in two files, with the duplication acknowledged by a comment
("Same logic the metrics command uses…") rather than refactored.

**Sites in this repo:**

- `src/winterthur/commands/metrics.py:140-158` (inline in `run`).
- `src/winterthur/commands/symbols.py:155-180` (`_combined_errors`).

**Why deferred:** the duplication is small, well-commented, and the
two call sites have slightly different return shapes (metrics builds a
file record, symbols returns just a list). A clean factor-out needs a
shared `merge_parse_errors(structural, generic) -> list[str]` helper
in something like `winterthur/_validate_merge.py`.

**Promote when…** a third call site appears, OR the merge policy
needs to change (then we'd be patching two places, which is the
canonical "drift hazard" moment).

**Severity:** 🟡 yellow.

### 5. God-method drift in `_extract_symbols`

**Shape.** A 99-LOC function (`ASTParser._extract_symbols`) holding
seven distinct concerns: dedup, kind refinement, decorator collection,
visibility evaluation, parent-class detection, function→method
upgrade, signature construction, docstring extraction, async detection,
qualified-name building, Symbol construction. Each step is clear; the
aggregate is dense.

**Sites in this repo:**

- `src/winterthur/parser.py:_extract_symbols`.

**Why deferred:** the function doesn't have a bug today — it's a
readability concern, not a correctness one. Refactor wants an
`_assemble_symbol(capture_dict, …) -> Symbol | None` helper, after
which the outer loop becomes ~10 lines of dedup + collection.

**Promote when…** the next per-language tweak lands here (e.g. C# or
Kotlin). At that point the cost of weaving language logic into the
existing dense block exceeds the cost of refactoring first.

**Severity:** 🟡 yellow.

### 6. Module-level mutable singleton (`_DEFAULT_PARSER`)

**Shape.** A module-level `Optional[ASTParser]` that the convenience
function `parse_file` lazily initialises with `global` mutation. Safe
under CLI single-threaded use; unsafe the day this is imported into a
worker pool, LSP, or HTTP server.

```python
_DEFAULT_PARSER: ASTParser | None = None

def parse_file(file_info, source) -> ParsedFile:
    global _DEFAULT_PARSER
    if _DEFAULT_PARSER is None:
        _DEFAULT_PARSER = ASTParser()
    return _DEFAULT_PARSER.parse_file(file_info, source)
```

**Sites in this repo:**

- `src/winterthur/parser.py:_DEFAULT_PARSER` and the
  module-level `parse_file()` convenience function.

**Why deferred:** winterthur is a single-threaded CLI today. The
convenience function is real ergonomics for embedding in scripts, and
swapping it for "construct your own ASTParser" loses that ergonomics
for the 99% of callers who run single-threaded.

**Promote when…** anyone imports winterthur from a server, LSP, or
worker-pool context. Either (a) make `parse_file` always construct a
fresh `ASTParser()` (constructor is cheap; the per-language query
compile is the only expensive bit and it's already dict-keyed at the
class level) or (b) use a `threading.local()`-backed singleton.

**Severity:** 🟡 yellow (latent).

### 7. Repeated `try: from tree_sitter import Parser` boilerplate

**Shape.** Three sites in `commands/` each open with the same
"defensive import + fall-back" block guarding `tree_sitter.Parser`.
Now logged loudly (no longer silent — see entry #1), but still
copy-paste shaped: a `winterthur/_ts_helpers.py:build_parser(language)`
helper would collapse three sites into one.

**Sites in this repo:**

- `src/winterthur/commands/metrics.py:_walker_results_for`.
- `src/winterthur/commands/smells.py:_scan_file`.
- `src/winterthur/commands/symbols.py:_combined_errors`.

**Why deferred:** the silent-except fix (entry #1) was the urgent
shape concern. Centralising the import is a follow-up cleanup that
doesn't affect behaviour, only repetition.

**Promote when…** a fourth call site appears, OR the install-integrity
warning text needs to change in one place rather than three.

**Severity:** 🟡 yellow.

---

## What this file is NOT

- **Not a TODO list** for features. Items here are about *shape*, not
  about new functionality.
- **Not a bug tracker.** If a smell is also a bug, it goes in the
  fixed section after the bug fix lands. Use git history / commit
  messages for the bug-tracking dimension.
- **Not a comprehensive code-smell taxonomy.** For the general
  Pascal/Delphi smell catalogue used by `/codereview`, see
  `~/.claude/skills/codereview/smells.md`. This file is a
  *project-local* ledger.

## Adding a new entry

Before adding an entry, ask:

1. Which section does it go in? Fixed (you actually fixed it) /
   Kept (you decided to leave it) / Pending (you decided to defer)?
2. Is the *shape* of the smell something we'd recognise again? If
   yes, name a category and write under that heading.
3. For Kept and Pending: does the entry name the **trigger** that
   would cause the smell to be removed? An entry without a trigger
   is a permanent excuse and shouldn't go here.

Keep entries short. The point is to make smells visible, not to
re-explain the codebase.
