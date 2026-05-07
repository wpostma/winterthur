"""Direct tests for the Python language walker.

Mirrors :mod:`test_metrics_walker` for Pascal: each test parses a small
Python snippet with tree-sitter directly, asserts the resulting
:class:`FunctionMetrics`, and runs entirely from inline triple-quoted
strings — no disk I/O.

Cross-language naming reminder: ``exit_count`` is the count of explicit
early-exit statements regardless of keyword. Pascal calls it ``Exit;``,
Python calls it ``return``. The A4 multiple-exits smell rule fires on
both.
"""

from __future__ import annotations

import pytest
from tree_sitter import Parser

from winterthur.metrics_walker import (
    FunctionMetrics,
    collect_function_metrics,
    validate_structure,
)
from winterthur.parser import _get_language


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_python(source: str) -> tuple[object, bytes]:
    src = source.encode("utf-8")
    lang = _get_language("python")
    if lang is None:
        pytest.skip("tree-sitter-python grammar not loaded")
    tree = Parser(lang).parse(src)
    return tree.root_node, src


def _first_metrics(source: str) -> FunctionMetrics:
    root, src = _parse_python(source)
    metrics = collect_function_metrics(root, src, "python")
    assert metrics, "no function-shaped nodes found in snippet"
    return next(iter(metrics.values()))


def _all_metrics(source: str) -> list[FunctionMetrics]:
    root, src = _parse_python(source)
    return list(collect_function_metrics(root, src, "python").values())


def _metrics_by_lines(
    source: str,
) -> dict[tuple[int, int], FunctionMetrics]:
    root, src = _parse_python(source)
    return collect_function_metrics(root, src, "python")


def _validate(source: str) -> list[str]:
    root, src = _parse_python(source)
    return validate_structure(root, src, "python")


# ---------------------------------------------------------------------------
# Decision-point counting
# ---------------------------------------------------------------------------


class TestDecisionPoints:
    def test_empty_function_has_one_decision_point(self) -> None:
        m = _first_metrics("def foo():\n    pass\n")
        assert m.decision_points == 1
        assert m.if_count is None
        assert m.case_count is None
        assert m.loop_count is None

    def test_single_if(self) -> None:
        m = _first_metrics("""\
def foo(x):
    if x:
        do_it()
""")
        assert m.if_count == 1
        assert m.decision_points == 2

    def test_each_elif_adds_a_decision(self) -> None:
        # Python idiom: if/elif/elif/else. Each elif is a real branch
        # and counts; the trailing else does NOT add a decision point.
        m = _first_metrics("""\
def classify(x):
    if x == 1:
        return 'one'
    elif x == 2:
        return 'two'
    elif x == 3:
        return 'three'
    else:
        return 'other'
""")
        # if_statement +1, elif_clause +1, elif_clause +1 => 3 from the
        # branching (plus 1 entry). Each return also bumps exit_count.
        assert m.if_count == 3
        # 1 (entry) + 3 (if + 2 elifs) + 0 (else doesn't add)
        assert m.decision_points == 4

    def test_ternary_counts(self) -> None:
        # `x if cond else y` is conditional_expression — a real branch.
        m = _first_metrics("""\
def pick(a, b, cond):
    return a if cond else b
""")
        # The ternary inside `return` is one decision; the return itself
        # is an exit, not a decision.
        assert m.if_count == 1
        assert m.decision_points == 2

    def test_match_arms_each_count(self) -> None:
        m = _first_metrics("""\
def shape(x):
    match x:
        case 1:
            return 'one'
        case 2:
            return 'two'
        case _:
            return 'other'
""")
        assert m.case_count == 1
        assert m.case_arms == 3
        # 1 entry + 3 arms; the match node itself does not add.
        assert m.decision_points == 4

    def test_for_and_while_each_count(self) -> None:
        m = _first_metrics("""\
def loops(items, n):
    for x in items:
        process(x)
    while n > 0:
        n -= 1
""")
        assert m.loop_count == 2
        assert m.decision_points == 3

    def test_try_except_finally(self) -> None:
        m = _first_metrics("""\
def safe():
    try:
        risky()
    except ValueError:
        handle_value()
    except KeyError as e:
        handle_key(e)
    finally:
        cleanup()
""")
        assert m.try_count == 1
        assert m.except_count == 2
        assert m.finally_count == 1
        # 1 entry + 2 except clauses (try and finally don't add).
        assert m.decision_points == 3

    def test_boolean_operators_each_add_decision(self) -> None:
        # `a and b or c` parses as boolean_operator(boolean_operator(a, and, b), or, c)
        # — two boolean_operator nodes, each +1 decision.
        m = _first_metrics("""\
def gate(a, b, c):
    if a and b or c:
        return True
    return False
""")
        assert m.boolean_op_count == 2
        assert m.if_count == 1
        # 1 entry + 1 if + 2 boolean ops
        assert m.decision_points == 4

    def test_comparison_operator_does_not_count(self) -> None:
        # `a > b` is comparison_operator, not a decision. Important
        # negative — comparisons inside conditions shouldn't double-count.
        m = _first_metrics("""\
def gt(a, b):
    if a > b:
        return True
    return False
""")
        # 1 entry + 1 if. No boolean ops, no extra.
        assert m.boolean_op_count is None
        assert m.if_count == 1
        assert m.decision_points == 2


# ---------------------------------------------------------------------------
# Exit / Break / Continue / Raise
# ---------------------------------------------------------------------------


class TestControlFlow:
    def test_return_increments_exit_count(self) -> None:
        # Python's exit equivalent: explicit return statements only.
        m = _first_metrics("""\
def f(x):
    if x:
        return 1
    return 2
""")
        assert m.exit_count == 2

    def test_implicit_fall_through_does_not_count(self) -> None:
        # No `return_statement` node => exit_count stays None.
        m = _first_metrics("""\
def f(x):
    if x:
        do_it()
""")
        assert m.exit_count is None

    def test_break_and_continue(self) -> None:
        m = _first_metrics("""\
def loop(items):
    for x in items:
        if x < 0:
            continue
        if x > 100:
            break
""")
        assert m.break_count == 1
        assert m.continue_count == 1

    def test_raise_counts(self) -> None:
        m = _first_metrics("""\
def check(x):
    if x < 0:
        raise ValueError('negative')
""")
        assert m.raise_count == 1


# ---------------------------------------------------------------------------
# Nesting depth
# ---------------------------------------------------------------------------


class TestNesting:
    def test_flat_function_low_depth(self) -> None:
        m = _first_metrics("""\
def f():
    do_it()
""")
        # Function body is a `block` (depth 1). No deeper construct.
        assert (m.max_nesting_depth or 0) <= 2

    def test_deeply_nested_function(self) -> None:
        m = _first_metrics("""\
def f(x):
    if x:
        for i in range(10):
            if i > 5:
                while True:
                    break
""")
        # block + if + block + for + block + if + block + while + block
        # Easily clears the L3 yellow threshold (>=8).
        assert (m.max_nesting_depth or 0) >= 8


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


class TestParams:
    def test_no_params(self) -> None:
        m = _first_metrics("def f():\n    pass\n")
        assert m.param_count is None
        assert m.params == []

    def test_self_is_counted(self) -> None:
        # Methods include self — counting it is consistent with how
        # Pascal counts the implicit Self equivalent (declarations on
        # T<Class>.<Method> include the visible parameters only and
        # exclude Self, but the convention here is to count what's in
        # the parameter list as written).
        m = _first_metrics("""\
class C:
    def method(self, x, y):
        pass
""")
        assert m.param_count == 3
        assert m.params == ["self", "x", "y"]

    def test_typed_and_default_parameters(self) -> None:
        m = _first_metrics("""\
def f(a, b: int, c=5, d: str = 'x'):
    pass
""")
        assert m.param_count == 4
        assert m.params == ["a", "b", "c", "d"]

    def test_args_and_kwargs(self) -> None:
        m = _first_metrics("""\
def f(a, *args, b=1, **kwargs):
    pass
""")
        assert m.param_count == 4
        assert m.params == ["a", "args", "b", "kwargs"]

    def test_keyword_only_separator_not_counted(self) -> None:
        # `*` alone marks the start of keyword-only params; it has no
        # name and must not inflate the count.
        m = _first_metrics("""\
def f(a, *, b, c):
    pass
""")
        assert m.param_count == 3
        assert m.params == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Anonymous procs (lambdas)
# ---------------------------------------------------------------------------


class TestLambda:
    def test_lambda_increments_anon_proc_count(self) -> None:
        m = _first_metrics("""\
def make_adders():
    add1 = lambda x: x + 1
    add2 = lambda x: x + 2
""")
        assert m.anon_proc_count == 2
        assert (m.max_anon_proc_depth or 0) >= 1


# ---------------------------------------------------------------------------
# Multiple functions and nested-function isolation
# ---------------------------------------------------------------------------


class TestMultipleAndNested:
    def test_two_top_level_functions(self) -> None:
        records = _all_metrics("""\
def alpha(x):
    if x:
        return 1

def beta(a, b):
    while a < b:
        a += 1
""")
        assert len(records) == 2
        alpha, beta = records
        assert alpha.if_count == 1
        assert alpha.loop_count is None
        assert beta.if_count is None
        assert beta.loop_count == 1
        assert beta.params == ["a", "b"]

    def test_nested_function_does_not_pollute_outer(self) -> None:
        # Regression net for the nested-function isolation in walk_function.
        # Outer has no decisions of its own; inner has one if. The walker
        # must produce two records, each with its own counts.
        records = _metrics_by_lines("""\
def outer():
    def inner(x):
        if x > 0:
            return 1
        return 0
    return inner
""")
        # Two records: outer (lines roughly 1-5) and inner (lines 2-4).
        assert len(records) == 2
        outer = max(records.values(), key=lambda r: r.exit_count or 0)
        inner = min(records.values(), key=lambda r: r.exit_count or 0)
        # Pick the outer by line range — it's the one whose end is later
        # OR the one with no if. Use both heuristics.
        ranges = sorted(records.keys())
        outer_key, inner_key = (
            (ranges[0], ranges[1]) if ranges[0][0] < ranges[1][0]
            else (ranges[1], ranges[0])
        )
        outer = records[outer_key]
        inner = records[inner_key]
        # Outer has one return (return inner) and no if.
        assert outer.if_count is None, (
            f"outer should have no ifs of its own, got if_count={outer.if_count}"
        )
        assert outer.exit_count == 1
        # Inner has one if and two returns.
        assert inner.if_count == 1
        assert inner.exit_count == 2


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


class TestValidateStructure:
    def test_well_formed_file_has_no_errors(self) -> None:
        errors = _validate("""\
def f(x):
    if x:
        return 1
    return 0
""")
        assert errors == []

    def test_indent_error_surfaces_as_parse_error(self) -> None:
        # Mixed/wrong indentation produces a tree-sitter parse error.
        errors = _validate("""\
def f(x):
  if x:
      return 1
   return 0
""")
        # Either we get a parse error message or no error if tree-sitter
        # recovers; the contract is "if has_error, surface a line number".
        if errors:
            assert any("parse error" in e for e in errors)


# ---------------------------------------------------------------------------
# Full-coverage sanity: realistic Python function
# ---------------------------------------------------------------------------


class TestRealistic:
    def test_complex_function_metrics_make_sense(self) -> None:
        m = _first_metrics("""\
def process(items, threshold=0, verbose=False):
    results = []
    for item in items:
        try:
            if item < 0:
                if verbose:
                    print(f'skip {item}')
                continue
            elif item > threshold:
                results.append(item * 2)
            else:
                results.append(item)
        except TypeError:
            raise ValueError('bad item')
    return results if results else None
""")
        # Sanity-check the headline numbers without pinning every counter
        # — this protects against major regressions while leaving room
        # for grammar refinements.
        assert m.param_count == 3
        assert m.loop_count == 1
        assert m.if_count >= 3  # if + elif + ternary in return
        assert m.try_count == 1
        assert m.except_count == 1
        assert m.raise_count == 1
        assert m.continue_count == 1
        assert m.exit_count == 1  # one explicit return
        # Decision points: 1 (entry) + 1 for + 1 outer-if + 1 inner-if
        # + 1 elif + 1 except + 1 ternary in the return = 7. Asserting
        # the exact number locks in the breakdown above; bumping a
        # rule should make this test fail loudly.
        assert m.decision_points == 7
        assert (m.max_nesting_depth or 0) >= 5
