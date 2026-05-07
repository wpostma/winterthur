# winterthur

<img src="docs/images/Niklaus_Wirth_large.jpg" alt="Niklaus Wirth" width="180" align="right" />

Multi-language source parser + lint and metrics tooling, built on
tree-sitter. First-class support for **Pascal/Delphi** (the original
motivation) plus Python, Rust, TypeScript, JavaScript, Java, Go, and
C/C++. C# coverage is a planned extension.

Named for **Winterthur**, the Swiss city where Niklaus Wirth — creator
of the Pascal language — was born in 1934.

## Goals

- **Single unit of compilation.** Take one or more `.pas` / `.dpr` /
  `.dpk` / `.inc` files (or single-file source in any supported
  language) and produce per-function metrics + smell findings.
  No `.dproj`, no compile, no `.dcu` resolution. Tree-sitter only.
- **Plug into the `codereview` Claude Code skill.** The CLI emits the
  JSON contract documented in
  `~/.claude/skills/codereview/metrics-tool-spec.md` so the skill can
  consume it without bespoke glue.
- **Lint and metrics in one tool.** LOC, max nesting depth,
  cyclomatic-ish complexity, parameter counts, anon-proc nesting,
  early-exit counts, plus pattern-based smell findings (silent exits,
  R2/R3/R4 violations, swallowed exceptions, SQL string interpolation,
  sale/refund symmetry candidates).

## Layout

```
winterthur/
  thirdparty/tree-sitter-pascal/    # git submodule, fork at wpostma/tree-sitter-pascal
  src/winterthur/
    parser.py                        # multi-language tree-sitter AST parser
    models.py                        # FileInfo, ParsedFile, Symbol, Import
    special_handlers.py              # Dockerfile/Makefile/OpenAPI parsers
    queries/
      pascal.scm                     # symbols + imports query (extends as needed)
    cli.py                           # CLI entry: winterthur <file...>
    metrics.py                       # JSON-emitting metrics for codereview
    smells.py                        # pattern detectors (silent exit, R2-R4, etc.)
  tests/
    unit/test_parser.py              # ported from repowise tests/unit/ingestion
  docs/
    plan-pascal-support.md           # original "two missing links" diagnosis
    metrics-tool-spec.md             # JSON schema for the codereview metrics contract
```

## Quick start

```powershell
# Clone with submodule
git clone --recurse-submodules <repo>
cd winterthur

# Install in dev mode
uv sync                              # or: pip install -e ".[dev]"

# Test
pytest

# CLI
winterthur FrontPOS/Source/AccessTicketUtil.pas > metrics.json
```

## Provenance

- Parser, queries, special handlers, plan doc, and tests carried over
  from `C:\vsdev\repowise` (commit context preserved in `docs/`).
- The Pascal grammar is a fork at
  [wpostma/tree-sitter-pascal](https://github.com/wpostma/tree-sitter-pascal)
  with: `$` in identifier continuation, bare `raise` rebroadcast,
  property re-publication, kSafecall/kSealed regex fixes, modernised
  bindings.

## License

MIT.
