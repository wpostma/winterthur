"""TypeScript language walker.

Encodes the tree-sitter-typescript node-type names for each metric
category, plus parameter extraction across TypeScript's parameter
shapes (``required_parameter``, ``optional_parameter``, ``rest_pattern``,
single-bare-identifier arrow params).

A few TypeScript-specific design calls:

* **arrow_function is a function_node_type.** Each arrow gets its own
  metrics record rather than being folded into the enclosing
  function's anon-proc count. Modern TS commonly defines public
  functions as ``const fn = () => …``, so a metrics record per arrow
  matches how authors and reviewers think about them. Trade-off: a
  ``forEach(x => …)`` callback chain produces several small records.
* **anon_proc tracking is empty.** Since arrows are top-level
  function-shaped, there's no useful "closure nesting depth" metric
  to track separately.
* **Logical operators ride pre_visit.** tree-sitter-typescript uses
  one ``binary_expression`` node type for both arithmetic (``+``,
  ``>``) and logical (``&&``, ``||``, ``??``) operators; only the
  logical ones contribute to decision points. The hook inspects the
  operator child and bumps ``boolean_op_count`` selectively.

Cross-language naming reminder: ``exit_count`` is the count of
explicit early-exit statements. TypeScript ``return``, Python
``return``, Pascal ``Exit;`` all increment it; the A4 multiple-exits
smell rule fires on the count regardless of keyword.
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


# Operator children of binary_expression that count as logical/short-circuit.
# `??` is the nullish-coalescing operator (ES2020) — included because it
# behaves as a decision: "evaluate left, fall back to right if nullish."
_LOGICAL_OPERATORS = frozenset({"&&", "||", "??"})


class TypeScriptWalker(LanguageWalker):
    language: ClassVar[str] = "typescript"

    function_node_types: ClassVar[frozenset[str]] = frozenset({
        "function_declaration",
        "generator_function_declaration",
        "arrow_function",
        "method_definition",
        "function_expression",
    })

    decision_kinds: ClassVar[dict[str, frozenset[str]]] = {
        # if + ternary; else_clause does NOT add a decision point on its
        # own (else if chains as nested if_statement so each chained
        # branch gets its own +1).
        "if": frozenset({"if_statement", "ternary_expression"}),
        "case": frozenset({"switch_statement"}),
        # switch_default does NOT add a decision (it's the fallthrough).
        "case_arm": frozenset({"switch_case"}),
        "loop": frozenset({
            "for_statement",
            "for_in_statement",  # covers both `for…of` and `for…in`
            "while_statement",
            "do_statement",
        }),
        "try": frozenset({"try_statement"}),
        "except": frozenset({"catch_clause"}),
        "finally": frozenset({"finally_clause"}),
        "raise": frozenset({"throw_statement"}),
        "exit": frozenset({"return_statement"}),
        "break": frozenset({"break_statement"}),
        "continue": frozenset({"continue_statement"}),
        # boolean_op: handled in pre_visit — tree-sitter-typescript packs
        # arithmetic and logical operators into the same binary_expression
        # node, so node-type matching alone overcounts.
        "boolean_op": frozenset(),
        # anon_proc: empty by design — arrow_function is in
        # function_node_types so each arrow becomes its own record.
        "anon_proc": frozenset(),
    }

    # Block-like constructs that contribute to nesting depth. Mirrors
    # the Pascal/Python pattern of counting both the control-flow node
    # AND its body block, so depth=2 for ``if (…) { x; }`` and the L3
    # smells thresholds (calibrated for that style) keep working.
    # arrow_function / function_expression are intentionally NOT here:
    # they're function_node_types and the walker skips into them as new
    # roots.
    nesting_node_types: ClassVar[frozenset[str]] = frozenset({
        "statement_block",
        "if_statement", "else_clause",
        "for_statement", "for_in_statement",
        "while_statement", "do_statement",
        "try_statement", "catch_clause", "finally_clause",
        "switch_statement", "switch_case", "switch_default",
    })

    # ------------------------------------------------------------------
    # Hook overrides
    # ------------------------------------------------------------------

    def pre_visit(self, node, ctx: WalkContext) -> None:
        # Logical operators (&&, ||, ??) inside binary_expression count
        # as decision points; arithmetic operators (+, >, ===) do not.
        # The operator is an anonymous child token; check by string match.
        if node.type != "binary_expression":
            return
        for c in node.children:
            if not c.is_named and c.type in _LOGICAL_OPERATORS:
                ctx.metrics.boolean_op_count = _bump(
                    ctx.metrics.boolean_op_count
                )
                ctx.metrics.decision_points += 1
                return

    def extract_signature(
        self, fn_node, source: bytes, m: FunctionMetrics
    ) -> None:
        """Populate ``m.params`` and ``m.param_count`` from the parameters node.

        Three parameter-list shapes:

        * ``formal_parameters`` wrapper containing ``required_parameter``
          / ``optional_parameter`` / ``rest_pattern`` children — the
          common case for ``function f(...)`` and ``(x, y) => …``.
        * Single bare identifier — only legal for an arrow function with
          one parameter and no parens: ``x => x``. Walked at the
          function-node level rather than via formal_parameters.
        * No parameters at all — ``() => 1`` has an empty
          formal_parameters node, no children to count.
        """
        params_node = fn_node.child_by_field_name("parameters")
        if params_node is None:
            for c in fn_node.children:
                if c.type == "formal_parameters":
                    params_node = c
                    break
                if c.type == "identifier":
                    # ``x => x`` style arrow function — single bare param.
                    m.param_count = _bump(m.param_count)
                    m.params.append(_decode(c, source))
                    return
        if params_node is None:
            return
        for child in params_node.children:
            name = _param_name(child, source)
            if name is None:
                continue
            m.param_count = _bump(m.param_count)
            m.params.append(name)

    def validate_structure(self, root_node, source: bytes) -> list[str]:
        """Surface tree-sitter parse errors only.

        TypeScript has no begin/end-style tokens to balance and no
        required terminator; an unmatched brace shows up as a parse
        error from tree-sitter-typescript directly.
        """
        if not root_node.has_error:
            return []
        line = _first_error_line(root_node)
        if line is not None:
            return [f"parse error starting at line {line}"]
        return ["parse error (tree-sitter could not fully parse this file)"]

    def qualified_name(self, fn_node, source: bytes) -> str | None:
        """Return ``Class.method`` for class methods, bare name otherwise.

        Naming sources:

        * ``function_declaration`` / ``generator_function_declaration``:
          ``identifier`` child.
        * ``method_definition``: ``property_identifier`` child.
        * ``arrow_function`` / ``function_expression``: anonymous on the
          node itself; we look at the parent ``variable_declarator``
          (``const f = …``) or ``pair`` (``{ name: () => … }``).

        For a class method, walk up to find the enclosing
        ``class_declaration`` / ``abstract_class_declaration`` and
        prefix with the class name.
        """
        name = _function_name(fn_node, source)
        if name is None:
            return None

        parent = fn_node.parent
        while parent is not None:
            if parent.type in (
                "class_declaration",
                "abstract_class_declaration",
            ):
                cls = _class_name_node(parent)
                if cls is not None:
                    return f"{_decode(cls, source)}.{name}"
                return name
            if parent.type in (
                "function_declaration",
                "method_definition",
                "arrow_function",
                "function_expression",
                "generator_function_declaration",
            ):
                return name  # nested function — stay unqualified
            parent = parent.parent
        return name


# ---------------------------------------------------------------------------
# Helpers (file-private)
# ---------------------------------------------------------------------------


def _param_name(node, source: bytes) -> str | None:
    """Extract one parameter's name from a formal_parameters child node."""
    if not node.is_named:
        return None  # punctuation: ( ) , …
    t = node.type
    if t == "identifier":
        return _decode(node, source)
    if t in ("required_parameter", "optional_parameter"):
        for c in node.children:
            if c.type == "identifier":
                return _decode(c, source)
            if c.type == "rest_pattern":
                # ``...rest: any[]`` — take the inner identifier.
                for sub in c.children:
                    if sub.type == "identifier":
                        return _decode(sub, source)
                return None
            if c.type in ("object_pattern", "array_pattern"):
                # Destructured params don't have a single name; surface
                # the source text so the user sees what's being unpacked
                # (matches how reviewers usually refer to it).
                return _decode(c, source).replace("\n", " ")
        return None
    if t == "rest_pattern":
        # When rest_pattern is a direct child of formal_parameters
        # rather than wrapped in required_parameter.
        for c in node.children:
            if c.type == "identifier":
                return _decode(c, source)
        return None
    return None


def _function_name(fn_node, source: bytes) -> str | None:
    """Return the textual name of a function-shaped node, or None."""
    t = fn_node.type
    if t in ("function_declaration", "generator_function_declaration"):
        for c in fn_node.children:
            if c.type == "identifier":
                return _decode(c, source)
        return None
    if t == "method_definition":
        for c in fn_node.children:
            if c.type in ("property_identifier", "computed_property_name"):
                return _decode(c, source)
        return None
    if t in ("arrow_function", "function_expression"):
        # Anonymous on the node itself — derive a name from the parent
        # binding context.
        parent = fn_node.parent
        if parent is None:
            return None
        if parent.type == "variable_declarator":
            for c in parent.children:
                if c.type == "identifier":
                    return _decode(c, source)
        if parent.type == "pair":
            for c in parent.children:
                if c.type in ("property_identifier", "string"):
                    return _decode(c, source).strip("'\"")
        # public_field_definition for class field arrows: `class C { fn = () => {} }`
        if parent.type == "public_field_definition":
            for c in parent.children:
                if c.type == "property_identifier":
                    return _decode(c, source)
        return None
    return None


def _class_name_node(class_node):
    """Return the identifier-or-type-identifier child that names the class."""
    for c in class_node.children:
        if c.type in ("type_identifier", "identifier"):
            return c
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
