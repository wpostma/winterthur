"""TypeScript-specific AST-pattern smell finder.

Phase 1 detectors selected from the intersection of:

* the academic React+TS code-smell survey (the top six TS smells),
* typescript-eslint's most-recommended quality rules, and
* what the codurance ``typescript-code-smells`` kata's ``Game.ts``
  actually exhibits.

The six rules:

* **N1 — non-null-assertion**: ``x!`` bypasses the type system. Flagged
  every occurrence; the ESLint ``no-non-null-assertion`` and academic
  survey both rank this as a top-two TS smell.
* **Y1 — any-type**: explicit ``any`` annotation anywhere (parameter,
  return, variable, generic argument, ``as any`` cast). Disables
  type-checking on whatever it touches.
* **Q1 — loose-equality**: ``==`` or ``!=`` (the JS coercion form).
  ``x == null`` / ``x == undefined`` (and the symmetric forms) are
  exempted as the canonical "is-nullish" idiom.
* **TS1 — ts-suppression**: ``// @ts-ignore`` or ``// @ts-nocheck``
  comments. ``// @ts-expect-error`` is intentionally NOT flagged
  because it's the recommended pattern (auto-errors when the
  suppression is no longer needed).
* **EC1 — empty-catch**: ``catch (e) { }`` with no named statements
  in the body. Parallel to Python's E2 silent-except.
* **AS1 — angle-bracket-cast**: ``<Foo>x`` cast syntax. The ``x as
  Foo`` form is preferred because it works in JSX files.

NOT included (each has a real reason):

* *Multiple booleans for state* — class-level semantic smell, needs
  cross-method analysis.
* *Missing union abstraction*, *enum implicit values* — useful but
  lower-frequency, save for Phase 2.
* *``var`` instead of let/const* — mostly stylistic.
* *Empty interface* — sometimes intentional (declaration merging).
"""

from __future__ import annotations

from typing import ClassVar

from ..walkers.base import _iter_descendants
from .base import SmellFinder, SmellHit


# Comment text prefixes (after stripping ``// `` whitespace) that we treat
# as suppressions. ``@ts-expect-error`` is deliberately NOT here.
_TS_SUPPRESSION_DIRECTIVES = ("@ts-ignore", "@ts-nocheck")

# Operator children of ``binary_expression`` that count as loose equality.
_LOOSE_EQ_OPERATORS = frozenset({"==", "!="})

# Operand node types that, when paired with ``==``/``!=``, make the
# comparison the canonical "is nullish" idiom — exempted from Q1.
_NULLISH_OPERAND_TYPES = frozenset({"null", "undefined"})


class TypeScriptSmellFinder(SmellFinder):
    language: ClassVar[str] = "typescript"

    def find(self, root_node, source: bytes) -> list[SmellHit]:
        # Pre-compute function ranges so each finding can be attributed
        # to its enclosing function (or None for top-level smells).
        # Skip unnamed scopes (inline arrow callbacks like
        # ``arr.find(t => t.x == 1)``) so a finding inside the callback
        # is reported against the enclosing class method, not against an
        # anonymous ``?``.
        from ..walkers import get_walker
        walker = get_walker(self.language)
        function_ranges = [
            (start, end, fn_node)
            for start, end, fn_node in self._function_ranges(root_node)
            if walker is not None
            and walker.qualified_name(fn_node, source) is not None
        ]

        hits: list[SmellHit] = []
        for node in _iter_descendants(root_node):
            t = node.type
            if t == "non_null_expression":
                hits.append(_make_hit(
                    "N1",
                    node,
                    function_ranges,
                    "non-null assertion `!` bypasses the type system",
                ))
            elif t == "predefined_type" and _decode(node, source).strip() == "any":
                hits.append(_make_hit(
                    "Y1",
                    node,
                    function_ranges,
                    "explicit `any` disables type checking on this annotation",
                ))
            elif t == "binary_expression":
                hit = _loose_equality_hit(node, source, function_ranges)
                if hit is not None:
                    hits.append(hit)
            elif t == "comment":
                hit = _ts_suppression_hit(node, source, function_ranges)
                if hit is not None:
                    hits.append(hit)
            elif t == "catch_clause":
                hit = _empty_catch_hit(node, function_ranges)
                if hit is not None:
                    hits.append(hit)
            elif t == "type_assertion":
                hits.append(_make_hit(
                    "AS1",
                    node,
                    function_ranges,
                    "angle-bracket cast `<Foo>x`; prefer `x as Foo` (works in JSX)",
                ))
        return hits


# ---------------------------------------------------------------------------
# Per-detector helpers
# ---------------------------------------------------------------------------


def _loose_equality_hit(
    node, source: bytes, function_ranges
) -> SmellHit | None:
    """Q1: binary_expression with ``==``/``!=`` and non-nullish operands."""
    op_child = None
    operands: list[object] = []
    for c in node.children:
        if not c.is_named and c.type in _LOOSE_EQ_OPERATORS:
            op_child = c
        elif c.is_named:
            operands.append(c)
    if op_child is None:
        return None  # arithmetic / strict-equality binary_expression
    # `x == null` / `x == undefined` (and symmetric) are the canonical
    # "check for nullish" idiom — eslint's eqeqeq rule has a smart
    # `null` exception for the same reason. Skip them.
    for operand in operands:
        if operand.type in _NULLISH_OPERAND_TYPES:
            return None
    return _make_hit(
        "Q1",
        node,
        function_ranges,
        f"loose `{op_child.type}` — use `{op_child.type[0]}==` (or "
        f"`{op_child.type[0]}==`) for strict equality without coercion",
    )


def _ts_suppression_hit(
    node, source: bytes, function_ranges
) -> SmellHit | None:
    """TS1: `// @ts-ignore` / `// @ts-nocheck` comments."""
    text = _decode(node, source)
    # Strip the `//` (or `/* */`) prefix and surrounding whitespace, then
    # check for any of the disallowed directives at the start.
    body = text.lstrip("/").lstrip("*").strip()
    if any(body.startswith(d) for d in _TS_SUPPRESSION_DIRECTIVES):
        directive = body.split()[0]
        return _make_hit(
            "TS1",
            node,
            function_ranges,
            f"`{directive}` disables type checking; use `@ts-expect-error` "
            "with a reason (it auto-errors when the suppression is no "
            "longer needed)",
        )
    return None


def _empty_catch_hit(
    catch_node, function_ranges
) -> SmellHit | None:
    """EC1: catch_clause whose statement_block has no named statements.

    A body containing only comments still counts as empty — the goal is
    to surface "exception was caught and discarded," and a comment doesn't
    log, reraise, or fall back.
    """
    body = None
    for c in catch_node.children:
        if c.type == "statement_block":
            body = c
            break
    if body is None:
        return None
    for c in body.children:
        if c.is_named and c.type != "comment":
            return None  # has at least one real statement
    return _make_hit(
        "EC1",
        catch_node,
        function_ranges,
        "empty catch block — exception is silently swallowed (no log, "
        "no reraise, no fallback)",
    )


# ---------------------------------------------------------------------------
# Function-key attribution
# ---------------------------------------------------------------------------


def _make_hit(
    rule: str,
    node,
    function_ranges: list[tuple[int, int, object]],
    detail: str,
) -> SmellHit:
    line = node.start_point[0] + 1
    return SmellHit(
        rule=rule,
        line=line,
        function_key=_enclosing_function_key(node, function_ranges),
        detail=detail,
    )


def _enclosing_function_key(
    node, function_ranges: list[tuple[int, int, object]]
) -> tuple[int, int] | None:
    """Find the smallest function range containing *node*.

    ``function_ranges`` is the output of
    :meth:`SmellFinder._function_ranges` — a list of
    ``(start_line, end_line, fn_node)`` triples. We pick the innermost
    range so a finding inside a nested function attributes to the
    nested one (matching the metrics walker's per-function records).
    """
    line = node.start_point[0] + 1
    best: tuple[int, int] | None = None
    best_size = float("inf")
    for start, end, _ in function_ranges:
        if start <= line <= end:
            size = end - start
            if size < best_size:
                best = (start, end)
                best_size = size
    return best


def _decode(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode(
        "utf-8", errors="replace"
    )
