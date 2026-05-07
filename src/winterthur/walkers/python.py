"""Python language walker.

Encodes the tree-sitter-python node-type names for each metric
category, plus parameter-name extraction across Python's several
parameter syntaxes (plain, typed, default, *args, **kwargs).

Cross-language naming convention: ``exit_count`` is the count of
explicit early-exit statements regardless of keyword. Pascal calls it
``Exit;``, Python calls it ``return``, future languages will have their
own term — the metric and the A4 "multiple-exits" smell rule both stay
language-agnostic. Implicit fall-through returns at the end of a
Python function do NOT increment ``exit_count`` because there is no
``return_statement`` node for them.
"""

from __future__ import annotations

from typing import ClassVar

from .base import (
    FunctionMetrics,
    LanguageWalker,
    WalkContext,
    _bump,
    _iter_descendants,
)


class PythonWalker(LanguageWalker):
    language: ClassVar[str] = "python"

    # tree-sitter-python: ``async def foo`` is still a function_definition
    # with an ``async`` keyword child, so we don't need a separate type.
    function_node_types: ClassVar[frozenset[str]] = frozenset({
        "function_definition",
    })

    decision_kinds: ClassVar[dict[str, frozenset[str]]] = {
        # if_statement covers the if-body; each elif_clause is a separate
        # decision (matches Python's idiomatic if/elif/elif chain).
        # conditional_expression is the ternary `x if c else y`.
        "if": frozenset({
            "if_statement", "elif_clause", "conditional_expression",
        }),
        "case": frozenset({"match_statement"}),
        "case_arm": frozenset({"case_clause"}),
        "loop": frozenset({"for_statement", "while_statement"}),
        "try": frozenset({"try_statement"}),
        # except_group_clause is the PEP 654 ``except*`` introduced in 3.11;
        # tree-sitter-python emits it as a separate node type.
        "except": frozenset({"except_clause", "except_group_clause"}),
        "finally": frozenset({"finally_clause"}),
        "raise": frozenset({"raise_statement"}),
        # return is Python's exit equivalent — see module docstring.
        "exit": frozenset({"return_statement"}),
        "break": frozenset({"break_statement"}),
        "continue": frozenset({"continue_statement"}),
        # boolean_operator is `a and b` / `a or b`. Each occurrence counts
        # once; chained `a and b and c` parses as nested boolean_operators
        # so the count naturally scales with logical complexity.
        "boolean_op": frozenset({"boolean_operator"}),
        "anon_proc": frozenset({"lambda"}),
    }

    # Constructs that contribute to nesting depth. We count both the
    # control-flow node AND its body block, mirroring the Pascal walker —
    # this means depth=2 for `if x:\n    pass` and depth=4 for one-level
    # of indented if-inside-if. The L3 thresholds in
    # commands/smells.THRESHOLDS were calibrated for this style.
    nesting_node_types: ClassVar[frozenset[str]] = frozenset({
        "block",
        "if_statement", "elif_clause", "else_clause",
        "for_statement", "while_statement",
        "try_statement", "except_clause", "except_group_clause",
        "finally_clause",
        "with_statement",
        "match_statement", "case_clause",
        "lambda",
        # function_definition is intentionally NOT here — the function
        # root itself shouldn't push depth on its own body, and nested
        # functions get skipped by the walker driver anyway.
    })

    # ------------------------------------------------------------------
    # Hook overrides
    # ------------------------------------------------------------------

    def extract_signature(
        self, fn_node, source: bytes, m: FunctionMetrics
    ) -> None:
        """Populate ``m.params`` and ``m.param_count`` from the
        ``parameters`` field child.

        Python parameter shapes we handle:

        ===========================  ===========================================
        Source                       Tree-sitter node type
        ===========================  ===========================================
        ``a``                        ``identifier``
        ``a: int``                   ``typed_parameter``
        ``a=1``                      ``default_parameter``
        ``a: int = 1``               ``typed_default_parameter``
        ``*args``                    ``list_splat_pattern``
        ``**kwargs``                 ``dictionary_splat_pattern``
        ``self`` / ``cls``           ``identifier`` — we count it; some
                                     metric tools strip the receiver but
                                     for parity with Pascal's ``Self``
                                     equivalent we keep it.
        ``/``, ``*``                 ``positional_separator``,
                                     ``keyword_separator`` — punctuation,
                                     skipped.
        ===========================  ===========================================
        """
        params_node = fn_node.child_by_field_name("parameters")
        if params_node is None:
            return
        for child in params_node.children:
            name = _param_name(child, source)
            if name is None:
                continue
            m.param_count = _bump(m.param_count)
            m.params.append(name)

    def qualified_name(self, fn_node, source: bytes) -> str | None:
        """Return ``Foo.bar`` for class methods, bare ``foo`` otherwise.

        Walks the parent chain looking for an enclosing
        ``class_definition`` to qualify the method name. Functions
        nested inside another function deliberately stay unqualified —
        producing ``outer.<locals>.inner`` would be more accurate but
        louder, and the line range disambiguates anyway.
        """
        name_node = _function_name_node(fn_node)
        if name_node is None:
            return None
        name = _decode(name_node, source)

        parent = fn_node.parent
        while parent is not None:
            if parent.type == "class_definition":
                class_name = _function_name_node(parent)  # also works for class
                if class_name is not None:
                    return f"{_decode(class_name, source)}.{name}"
                return name
            if parent.type == "function_definition":
                # Nested in a function — stay unqualified.
                return name
            parent = parent.parent
        return name

    def validate_structure(self, root_node, source: bytes) -> list[str]:
        """Surface tree-sitter parse errors only.

        Python has no begin/end-style tokens to balance and no required
        terminator; an indentation problem shows up as a parse error
        from tree-sitter-python directly. The metrics command's
        disclaimer about parse errors meaning parser bugs (not bad
        code) applies here exactly as it does for Pascal.
        """
        if not root_node.has_error:
            return []
        line = _first_error_line(root_node)
        if line is not None:
            return [f"parse error starting at line {line}"]
        return ["parse error (tree-sitter could not fully parse this file)"]


# ---------------------------------------------------------------------------
# Helpers (file-private)
# ---------------------------------------------------------------------------


def _function_name_node(node):
    """Return the ``identifier`` child that names a function or class.

    Tries the named ``name`` field first (works on most tree-sitter-python
    versions), then falls back to the first identifier child — useful for
    decorated definitions where the field lookup may differ.
    """
    n = node.child_by_field_name("name")
    if n is not None:
        return n
    for c in node.children:
        if c.type == "identifier":
            return c
    return None


_PARAM_PUNCTUATION = frozenset({
    ",", "(", ")", "/", "*",
    "positional_separator", "keyword_separator",
})


def _param_name(node, source: bytes) -> str | None:
    """Extract the identifier name from one parameters-list child node.

    Returns ``None`` for punctuation tokens (``,`` ``(`` ``)`` etc.) and
    for the ``/`` / ``*`` markers used to split positional-only and
    keyword-only sections.
    """
    t = node.type
    if t in _PARAM_PUNCTUATION:
        return None
    if t == "identifier":
        return _decode(node, source)
    # typed_parameter / default_parameter / typed_default_parameter all
    # nest the name as an identifier (or a typed_parameter wrapping one)
    # in their first identifier-shaped child.
    if t in (
        "typed_parameter",
        "default_parameter",
        "typed_default_parameter",
    ):
        for c in node.children:
            inner = _param_name(c, source)
            if inner is not None:
                return inner
        return None
    # *args / **kwargs — find the inner identifier.
    if t in ("list_splat_pattern", "dictionary_splat_pattern"):
        for c in node.children:
            if c.type == "identifier":
                return _decode(c, source)
        return None
    return None


def _decode(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode(
        "utf-8", errors="replace"
    )


def _first_error_line(root_node) -> int | None:
    for n in _iter_descendants(root_node):
        if n.type == "ERROR":
            return n.start_point[0] + 1
    return None
