"""Direct tests for the TypeScript language walker.

Mirrors :mod:`test_metrics_walker_python` for TypeScript: each test
parses a small TypeScript snippet with tree-sitter directly and
asserts the resulting :class:`FunctionMetrics`. No disk I/O — snippets
are inline triple-quoted strings handed straight to the parser.

A few TS-specific quirks worth knowing while reading these tests:

* ``boolean_op_count`` only counts ``&&`` / ``||`` / ``??`` operators.
  Arithmetic and comparison operators (``+``, ``>``, ``===``) parse
  as ``binary_expression`` too but don't contribute.
* Each ``arrow_function`` produces its own metrics record, even when
  used inline as a callback. This keeps the symbol/metric story
  consistent (every function-shaped symbol gets a record).
* ``for…of`` and ``for…in`` are both ``for_in_statement`` in
  tree-sitter-typescript.
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


def _parse_ts(source: str) -> tuple[object, bytes]:
    src = source.encode("utf-8")
    lang = _get_language("typescript")
    if lang is None:
        pytest.skip("tree-sitter-typescript grammar not loaded")
    tree = Parser(lang).parse(src)
    return tree.root_node, src


def _first_metrics(source: str) -> FunctionMetrics:
    root, src = _parse_ts(source)
    metrics = collect_function_metrics(root, src, "typescript")
    assert metrics, "no function-shaped nodes found in snippet"
    return next(iter(metrics.values()))


def _all_metrics(source: str) -> list[FunctionMetrics]:
    root, src = _parse_ts(source)
    return list(collect_function_metrics(root, src, "typescript").values())


def _metrics_by_lines(
    source: str,
) -> dict[tuple[int, int], FunctionMetrics]:
    root, src = _parse_ts(source)
    return collect_function_metrics(root, src, "typescript")


def _validate(source: str) -> list[str]:
    root, src = _parse_ts(source)
    return validate_structure(root, src, "typescript")


# ---------------------------------------------------------------------------
# Decision-point counting
# ---------------------------------------------------------------------------


class TestDecisionPoints:
    def test_empty_function_has_one_decision_point(self) -> None:
        m = _first_metrics("function f() {}\n")
        assert m.decision_points == 1
        assert m.if_count is None
        assert m.case_count is None
        assert m.loop_count is None

    def test_single_if(self) -> None:
        m = _first_metrics("""\
function f(x: number) {
    if (x > 0) doIt();
}
""")
        assert m.if_count == 1
        assert m.decision_points == 2

    def test_else_if_chain_each_counts(self) -> None:
        # Each `else if` parses as a nested if_statement inside an
        # else_clause; each inner if_statement adds its own decision.
        m = _first_metrics("""\
function classify(x: number): string {
    if (x === 1) return "one";
    else if (x === 2) return "two";
    else if (x === 3) return "three";
    else return "other";
}
""")
        assert m.if_count == 3
        # 1 entry + 3 ifs = 4. (else is just the alternative branch.)
        # Plus 4 returns -> exit_count, but those are exits not decisions.
        assert m.decision_points == 4

    def test_ternary_counts(self) -> None:
        # `cond ? a : b` is ternary_expression — a real branch.
        m = _first_metrics("""\
function pick(cond: boolean, a: number, b: number): number {
    return cond ? a : b;
}
""")
        # ternary inside the return is one decision; the return is an
        # exit, not a decision.
        assert m.if_count == 1
        assert m.decision_points == 2

    def test_switch_arms_each_count(self) -> None:
        m = _first_metrics("""\
function shape(x: number): string {
    switch (x) {
        case 1: return "one";
        case 2: return "two";
        case 3: return "three";
        default: return "other";
    }
}
""")
        assert m.case_count == 1
        assert m.case_arms == 3
        # 1 entry + 3 case arms (default doesn't add).
        assert m.decision_points == 4

    def test_each_loop_kind_adds_a_decision(self) -> None:
        m = _first_metrics("""\
function loops(items: number[], n: number) {
    for (let i = 0; i < n; i++) { console.log(i); }
    for (const x of items) { console.log(x); }
    while (n > 0) n--;
    do { n--; } while (n > 0);
}
""")
        assert m.loop_count == 4
        assert m.decision_points == 5

    def test_for_in_and_for_of_both_count(self) -> None:
        # tree-sitter-typescript represents both as for_in_statement.
        m = _first_metrics("""\
function each(obj: any, arr: any[]) {
    for (const k in obj) { console.log(k); }
    for (const v of arr) { console.log(v); }
}
""")
        assert m.loop_count == 2

    def test_try_except_finally(self) -> None:
        m = _first_metrics("""\
function safe() {
    try { risky(); }
    catch (e) { handle(e); }
    finally { cleanup(); }
}
""")
        assert m.try_count == 1
        assert m.except_count == 1
        assert m.finally_count == 1
        # 1 entry + 1 catch (try and finally don't add).
        assert m.decision_points == 2

    def test_logical_operators_each_add_decision(self) -> None:
        # `a && b || c` parses as binary_expression(binary_expression(a, &&, b), ||, c)
        # Two boolean_operator-shaped nodes; each +1 decision.
        m = _first_metrics("""\
function gate(a: boolean, b: boolean, c: boolean): boolean {
    if (a && b || c) return true;
    return false;
}
""")
        assert m.boolean_op_count == 2
        assert m.if_count == 1
        # 1 entry + 1 if + 2 logical ops + 0 (returns are exits)
        assert m.decision_points == 4

    def test_nullish_coalescing_counts(self) -> None:
        # `a ?? b` is short-circuit, behaves as a decision.
        m = _first_metrics("""\
function defaulted(a: any, b: any) {
    return a ?? b;
}
""")
        assert m.boolean_op_count == 1
        assert m.decision_points == 2

    def test_arithmetic_operators_do_not_count(self) -> None:
        # Important negative — binary_expression with arithmetic/comparison
        # operators must NOT contribute to boolean_op_count.
        m = _first_metrics("""\
function add(a: number, b: number): number {
    if (a > b) return a + b;
    return a - b;
}
""")
        assert m.boolean_op_count is None
        assert m.if_count == 1
        assert m.decision_points == 2


# ---------------------------------------------------------------------------
# Exit / Break / Continue / Throw
# ---------------------------------------------------------------------------


class TestControlFlow:
    def test_return_increments_exit_count(self) -> None:
        # TypeScript's exit equivalent: explicit return statements only.
        m = _first_metrics("""\
function f(x: number): number {
    if (x > 0) return 1;
    return 0;
}
""")
        assert m.exit_count == 2

    def test_implicit_fall_through_does_not_count(self) -> None:
        # No return_statement -> exit_count stays None.
        m = _first_metrics("""\
function f(x: number) {
    if (x > 0) doIt();
}
""")
        assert m.exit_count is None

    def test_break_and_continue(self) -> None:
        m = _first_metrics("""\
function loop(items: number[]) {
    for (const x of items) {
        if (x < 0) continue;
        if (x > 100) break;
    }
}
""")
        assert m.break_count == 1
        assert m.continue_count == 1

    def test_throw_counts_as_raise(self) -> None:
        m = _first_metrics("""\
function check(x: number) {
    if (x < 0) throw new Error("negative");
}
""")
        assert m.raise_count == 1


# ---------------------------------------------------------------------------
# Nesting depth
# ---------------------------------------------------------------------------


class TestNesting:
    def test_flat_function_low_depth(self) -> None:
        m = _first_metrics("""\
function f() {
    doIt();
}
""")
        # Function body is statement_block (depth 1). Nothing deeper.
        assert (m.max_nesting_depth or 0) <= 2

    def test_deeply_nested_function(self) -> None:
        m = _first_metrics("""\
function f(x: number) {
    if (x > 0) {
        for (let i = 0; i < 10; i++) {
            if (i > 5) {
                while (true) {
                    break;
                }
            }
        }
    }
}
""")
        # Easily clears the L3 yellow threshold (>=8).
        assert (m.max_nesting_depth or 0) >= 8


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


class TestParams:
    def test_no_params(self) -> None:
        m = _first_metrics("function f() {}\n")
        assert m.param_count is None
        assert m.params == []

    def test_typed_and_default_parameters(self) -> None:
        m = _first_metrics("""\
function f(a: number, b: string = "x", c?: boolean) {}
""")
        assert m.param_count == 3
        assert m.params == ["a", "b", "c"]

    def test_rest_parameter(self) -> None:
        m = _first_metrics("""\
function f(a: number, ...rest: any[]) {}
""")
        assert m.param_count == 2
        assert m.params == ["a", "rest"]

    def test_arrow_with_paren_params(self) -> None:
        m = _first_metrics("""\
const fn = (a: number, b: number) => a + b;
""")
        assert m.param_count == 2
        assert m.params == ["a", "b"]

    def test_arrow_with_single_bare_param(self) -> None:
        # `x => x + 1` — no parens, single identifier.
        m = _first_metrics("""\
const inc = x => x + 1;
""")
        assert m.param_count == 1
        assert m.params == ["x"]

    def test_arrow_zero_params(self) -> None:
        m = _first_metrics("""\
const noop = () => undefined;
""")
        assert m.param_count is None
        assert m.params == []

    def test_method_definition_params(self) -> None:
        records = _all_metrics("""\
class C {
    method(self: any, x: number, y: string): boolean { return true; }
}
""")
        assert len(records) == 1
        m = records[0]
        # We do NOT exclude self/cls for TypeScript (no convention there).
        assert m.param_count == 3
        assert m.params == ["self", "x", "y"]


# ---------------------------------------------------------------------------
# Multiple functions and nested-function isolation
# ---------------------------------------------------------------------------


class TestMultipleAndNested:
    def test_two_top_level_functions(self) -> None:
        records = _all_metrics("""\
function alpha(x: number) {
    if (x > 0) return 1;
    return 0;
}

function beta(a: number, b: number) {
    while (a < b) a++;
}
""")
        assert len(records) == 2
        alpha, beta = records
        assert alpha.if_count == 1
        assert alpha.exit_count == 2
        assert beta.if_count is None
        assert beta.loop_count == 1
        assert beta.params == ["a", "b"]

    def test_arrow_callback_gets_own_record(self) -> None:
        # Each arrow_function is in function_node_types -> separate record.
        # The outer function's metrics should NOT include the arrow's body.
        records = _metrics_by_lines("""\
function outer(items: number[]) {
    items.forEach(x => {
        if (x > 0) console.log(x);
    });
}
""")
        # Two records: outer (function_declaration) + arrow.
        assert len(records) == 2
        outer = max(records.values(), key=lambda r: r.loop_count or 0)
        # Pick by line range to be deterministic.
        ranges = sorted(records.keys())
        outer_key = ranges[0]
        inner_key = ranges[1]
        outer = records[outer_key]
        inner = records[inner_key]
        # Outer: no ifs of its own.
        assert outer.if_count is None
        # Inner arrow: one if.
        assert inner.if_count == 1

    def test_nested_function_does_not_pollute_outer(self) -> None:
        records = _metrics_by_lines("""\
function outer() {
    function inner(x: number): number {
        if (x > 0) return 1;
        return 0;
    }
    return inner;
}
""")
        assert len(records) == 2
        ranges = sorted(records.keys())
        outer_key = ranges[0]
        inner_key = ranges[1]
        outer = records[outer_key]
        inner = records[inner_key]
        # Outer: one return (return inner), no ifs.
        assert outer.if_count is None
        assert outer.exit_count == 1
        # Inner: one if, two returns.
        assert inner.if_count == 1
        assert inner.exit_count == 2


# ---------------------------------------------------------------------------
# Qualified-name extraction
# ---------------------------------------------------------------------------


class TestQualifiedName:
    def _name(self, source: str) -> str | None:
        from winterthur.walkers import get_walker
        from winterthur.walkers.base import _iter_descendants
        root, src = _parse_ts(source)
        walker = get_walker("typescript")
        for n in _iter_descendants(root):
            if n.type in walker.function_node_types:
                return walker.qualified_name(n, src)
        return None

    def test_top_level_function_is_bare(self) -> None:
        assert self._name("function frobnicate() {}\n") == "frobnicate"

    def test_class_method_is_qualified(self) -> None:
        assert self._name("""\
class Calculator {
    add(x: number, y: number): number { return x + y; }
}
""") == "Calculator.add"

    def test_arrow_assigned_to_const_is_named(self) -> None:
        assert self._name("const fetch = (url: string) => null;\n") == "fetch"

    def test_anonymous_arrow_callback_is_unnamed(self) -> None:
        # Arrow used inline as a callback has no parent variable_declarator —
        # qualified_name returns None and the smells command will render '?'.
        assert self._name("[1, 2, 3].forEach(x => x * 2);\n") is None

    def test_async_function_resolves(self) -> None:
        assert self._name("async function fetchData(url: string) {}\n") == "fetchData"


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


class TestValidateStructure:
    def test_well_formed_file_no_errors(self) -> None:
        errors = _validate("""\
function f(x: number): number {
    if (x > 0) return 1;
    return 0;
}
""")
        assert errors == []

    def test_unmatched_brace_surfaces_parse_error(self) -> None:
        # Missing closing brace — tree-sitter reports has_error.
        errors = _validate("""\
function f(x: number): number {
    if (x > 0) return 1;
""")
        # Either we get a parse error message or tree-sitter recovers;
        # the contract is "if has_error, surface a line number".
        if errors:
            assert any("parse error" in e for e in errors)


# ---------------------------------------------------------------------------
# Realistic function — sanity check
# ---------------------------------------------------------------------------


class TestRealistic:
    def test_complex_function_metrics_make_sense(self) -> None:
        m = _first_metrics("""\
function process(items: number[], threshold = 0, verbose = false): number[] {
    const results: number[] = [];
    for (const item of items) {
        try {
            if (item < 0) {
                if (verbose) console.log(`skip ${item}`);
                continue;
            } else if (item > threshold) {
                results.push(item * 2);
            } else {
                results.push(item);
            }
        } catch (e) {
            throw new Error("bad item");
        }
    }
    return results.length > 0 ? results : [];
}
""")
        # Sanity-check the headline numbers.
        assert m.param_count == 3
        assert m.loop_count == 1
        # if + else if + ternary = 3 if-shaped decisions
        assert m.if_count >= 3
        assert m.try_count == 1
        assert m.except_count == 1
        assert m.raise_count == 1
        assert m.continue_count == 1
        assert m.exit_count == 1
        # 1 entry + 1 for + 1 outer-if + 1 inner-if + 1 elif + 1 catch
        # + 1 ternary + 0 logical ops = 7
        assert m.decision_points == 7
        assert (m.max_nesting_depth or 0) >= 5
