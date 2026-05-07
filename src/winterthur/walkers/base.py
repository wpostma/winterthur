"""Base types and generic driver for per-language metrics walkers.

A :class:`LanguageWalker` subclass declares class-level data (frozensets
of node types per metric category) and optionally overrides hook
methods for things that don't reduce to a node-type lookup. The
generic :func:`walk_function` driver consumes that surface and produces
a :class:`FunctionMetrics` record.

Subclass surface:

============================  ==========  =========================
Attribute / method            Required?   Notes
============================  ==========  =========================
``language``                  yes         language tag string
``function_node_types``       yes         "this is a function" node types
``decision_kinds``            yes         dict[category -> frozenset]
``nesting_node_types``        no          defaults to empty
``pre_visit``                 no          per-node language hook
``extract_signature``         no          populates params / param_count
``count_result_assignments``  no          Pascal-style ``Result := X``
``validate_structure``        no          structural correctness errors
============================  ==========  =========================

Standard ``decision_kinds`` keys: ``if, case, case_arm, loop, try,
except, finally, raise, exit, break, continue, boolean_op, anon_proc``.
A walker may leave any key unset (defaults to empty); use this for
languages where the construct doesn't exist or is recognised via
``pre_visit`` instead of a node-type match (Pascal's identifier-call
``Exit;`` is the canonical example).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Iterable


@dataclass
class FunctionMetrics:
    """Counts collected by walking one function subtree.

    Field names match :file:`~/.claude/skills/codereview/metrics-tool-spec.md`
    so they can be merged into the metrics command's JSON record without
    renaming. All counter fields default to ``None`` (not zero) so that
    JSON serialisation can drop fields that were never observed; this
    keeps output terse for LLM consumers — a function with no
    try/except shouldn't waste tokens reporting three zero-valued
    fields.
    """

    decision_points: int = 1
    if_count: int | None = None
    case_count: int | None = None
    case_arms: int | None = None
    loop_count: int | None = None
    try_count: int | None = None
    except_count: int | None = None
    finally_count: int | None = None
    raise_count: int | None = None
    exit_count: int | None = None
    break_count: int | None = None
    continue_count: int | None = None
    boolean_op_count: int | None = None
    result_assign_count: int | None = None
    anon_proc_count: int | None = None
    max_anon_proc_depth: int | None = None
    max_nesting_depth: int | None = None
    param_count: int | None = None
    local_var_count: int | None = None
    logger_call_count: int | None = None

    params: list[str] = field(default_factory=list)


@dataclass
class WalkContext:
    """Mutable state threaded through the recursive walk.

    ``depth`` and ``anon_depth`` are pushed before descending into
    nesting / anon-proc nodes and popped on the way back up. Hooks may
    read them but should not mutate them — push/pop is the driver's
    responsibility.
    """

    metrics: FunctionMetrics
    depth: int = 0
    anon_depth: int = 0


def _bump(value: int | None, by: int = 1) -> int:
    """Increment ``value`` by ``by``, treating ``None`` as 0."""
    return (value or 0) + by


def _iter_descendants(node) -> Iterable:
    """Pre-order traversal generator over a tree-sitter node subtree."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        # push children in reverse so descent is left-to-right
        stack.extend(reversed(n.children))


# ---------------------------------------------------------------------------
# Strategy base class
# ---------------------------------------------------------------------------


_EMPTY: frozenset[str] = frozenset()


class LanguageWalker:
    """Per-language strategy for the metrics walker.

    Subclasses set the class attributes below and optionally override
    the hook methods. The driver in :func:`walk_function` is generic.
    """

    language: ClassVar[str]
    function_node_types: ClassVar[frozenset[str]]
    decision_kinds: ClassVar[dict[str, frozenset[str]]]
    nesting_node_types: ClassVar[frozenset[str]] = _EMPTY

    # ------------------------------------------------------------------
    # Hooks — defaults are no-ops.
    # ------------------------------------------------------------------

    def pre_visit(self, node, ctx: WalkContext) -> None:
        """Called on every node before the standard counter dispatch.

        Use for recognition that doesn't reduce to a node-type lookup —
        e.g. Pascal's ``Exit;`` / ``Break;`` / ``Continue;``, which are
        ordinary identifier calls in the grammar.
        """

    def extract_signature(
        self, fn_node, source: bytes, m: FunctionMetrics
    ) -> None:
        """Populate ``m.params`` and ``m.param_count`` from *fn_node*."""

    def count_result_assignments(
        self, fn_node, source: bytes, m: FunctionMetrics
    ) -> None:
        """Pascal-style ``Result := X`` / ``<FuncName> := X`` tracking."""

    def validate_structure(self, root_node, source: bytes) -> list[str]:
        """Return human-readable structural errors for *root_node*."""
        return []

    def qualified_name(self, fn_node, source: bytes) -> str | None:
        """Return the function's qualified name, e.g. ``Class.method``.

        Used by the smells command to label findings. Defaults to
        ``None``, which the caller renders as ``"?"``. Subclasses should
        return the unqualified name for top-level functions and a
        dotted form for class methods (e.g. Python ``Foo.bar`` or
        Pascal ``TFoo.Bar``).
        """
        return None


# ---------------------------------------------------------------------------
# Generic driver
# ---------------------------------------------------------------------------


def walk_function(
    fn_node, source: bytes, walker: LanguageWalker
) -> FunctionMetrics:
    """Walk one function subtree and return its :class:`FunctionMetrics`.

    Nested function definitions encountered below ``fn_node`` are
    skipped; they get their own metrics record from the outer
    :func:`collect_function_metrics` iteration. This keeps an outer
    function's counters clean of its inner functions' bodies — important
    for Python (decorators, closures) and the right thing for Pascal's
    local procedures too.
    """
    ctx = WalkContext(metrics=FunctionMetrics())
    _walk(fn_node, walker, ctx, is_root=True)
    walker.extract_signature(fn_node, source, ctx.metrics)
    walker.count_result_assignments(fn_node, source, ctx.metrics)
    return ctx.metrics


def _walk(
    node, walker: LanguageWalker, ctx: WalkContext, *, is_root: bool
) -> None:
    """Recursive counting walk. ``ctx.metrics`` is mutated in place.

    When *is_root* is False and *node* is a function-defining node, the
    subtree is skipped entirely — the inner function gets its own walk
    via :func:`collect_function_metrics`, so attributing its body to
    the outer would double-count.
    """
    if not is_root and node.type in walker.function_node_types:
        return

    walker.pre_visit(node, ctx)

    # Anonymous nodes (keywords like `def`, `lambda`, punctuation like
    # `:`/`(`) share their type-name with the surrounding AST node in
    # some grammars — tree-sitter-python's `lambda` keyword inside the
    # `lambda` AST node is the canonical example. Counter dispatch must
    # only run on named nodes to avoid double-counting; we still
    # descend so that named children of unnamed wrappers aren't lost.
    if not node.is_named:
        for c in node.children:
            _walk(c, walker, ctx, is_root=False)
        return

    t = node.type
    kinds = walker.decision_kinds
    m = ctx.metrics

    # Counter dispatch. The branches mirror the metric categories
    # documented on LanguageWalker; any kind set the walker leaves unset
    # falls through harmlessly via .get(..., _EMPTY).
    if t in kinds.get("if", _EMPTY):
        m.if_count = _bump(m.if_count)
        m.decision_points += 1
    elif t in kinds.get("case", _EMPTY):
        m.case_count = _bump(m.case_count)
    elif t in kinds.get("case_arm", _EMPTY):
        m.case_arms = _bump(m.case_arms)
        m.decision_points += 1
    elif t in kinds.get("loop", _EMPTY):
        m.loop_count = _bump(m.loop_count)
        m.decision_points += 1
    elif t in kinds.get("try", _EMPTY):
        m.try_count = _bump(m.try_count)
    elif t in kinds.get("except", _EMPTY):
        m.except_count = _bump(m.except_count)
        m.decision_points += 1
    elif t in kinds.get("finally", _EMPTY):
        m.finally_count = _bump(m.finally_count)
    elif t in kinds.get("raise", _EMPTY):
        m.raise_count = _bump(m.raise_count)
    elif t in kinds.get("exit", _EMPTY):
        m.exit_count = _bump(m.exit_count)
    elif t in kinds.get("break", _EMPTY):
        m.break_count = _bump(m.break_count)
    elif t in kinds.get("continue", _EMPTY):
        m.continue_count = _bump(m.continue_count)
    elif t in kinds.get("boolean_op", _EMPTY):
        m.boolean_op_count = _bump(m.boolean_op_count)
        m.decision_points += 1

    # anon_proc has BOTH a counter bump and a closure-depth push, so it
    # sits outside the elif chain — a node type might in principle also
    # match another category (none currently do, but the structure is
    # cleaner this way).
    pushed_anon = t in kinds.get("anon_proc", _EMPTY)
    if pushed_anon:
        m.anon_proc_count = _bump(m.anon_proc_count)
        ctx.anon_depth += 1
        if (m.max_anon_proc_depth or 0) < ctx.anon_depth:
            m.max_anon_proc_depth = ctx.anon_depth

    pushed_nest = t in walker.nesting_node_types
    if pushed_nest:
        ctx.depth += 1
        if (m.max_nesting_depth or 0) < ctx.depth:
            m.max_nesting_depth = ctx.depth

    for c in node.children:
        _walk(c, walker, ctx, is_root=False)

    if pushed_nest:
        ctx.depth -= 1
    if pushed_anon:
        ctx.anon_depth -= 1
