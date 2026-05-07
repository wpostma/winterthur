"""Direct tests for :mod:`winterthur.metrics_walker`.

Each test parses a small Pascal snippet with tree-sitter directly and
asserts the resulting :class:`FunctionMetrics`. No disk I/O — snippets
are inline triple-quoted strings handed straight to the parser.

This file is the regression net for any future split of the walker into
a strategy/registry shape (one walker class per language). The walker
is currently Pascal-only; non-Pascal language tags must return an empty
result rather than crash.
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


def _parse_pascal(source: str) -> tuple[object, bytes]:
    src = source.encode("utf-8")
    lang = _get_language("pascal")
    if lang is None:
        pytest.skip("tree-sitter-pascal grammar not loaded")
    tree = Parser(lang).parse(src)
    return tree.root_node, src


def _first_metrics(source: str) -> FunctionMetrics:
    root, src = _parse_pascal(source)
    metrics = collect_function_metrics(root, src, "pascal")
    assert metrics, "no function-shaped nodes found in snippet"
    # collect_function_metrics walks in source order and dict preserves
    # insertion order, so the first value is the first function.
    return next(iter(metrics.values()))


def _all_metrics(source: str) -> list[FunctionMetrics]:
    root, src = _parse_pascal(source)
    return list(collect_function_metrics(root, src, "pascal").values())


def _validate(source: str) -> list[str]:
    root, src = _parse_pascal(source)
    return validate_structure(root, src, "pascal")


# ---------------------------------------------------------------------------
# Decision-point counting
# ---------------------------------------------------------------------------


class TestDecisionPoints:
    def test_empty_function_has_one_decision_point(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo;
begin
end;
end.
""")
        assert m.decision_points == 1
        assert m.if_count is None
        assert m.case_count is None
        assert m.loop_count is None

    def test_single_if_adds_one(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo(X: Integer);
begin
  if X = 1 then DoSomething;
end;
end.
""")
        assert m.if_count == 1
        assert m.decision_points == 2

    def test_if_else_is_one_decision(self) -> None:
        # ifElse is grammatically distinct from if, but it remains a single
        # decision point — the else is the other arm of the same branch.
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo(X: Integer);
begin
  if X = 1 then DoOne else DoOther;
end;
end.
""")
        assert m.if_count == 1
        assert m.decision_points == 2

    def test_case_arms_each_count_as_decision(self) -> None:
        # Regression net for the caseLabel/caseCase double-count fix:
        # tree-sitter-pascal emits caseCase (the whole arm) wrapping a
        # caseLabel (the `1:` child); the walker counts only caseCase so
        # each arm contributes one decision point.
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo(X: Integer);
begin
  case X of
    1: DoOne;
    2: DoTwo;
    3: DoThree;
  end;
end;
end.
""")
        assert m.case_count == 1
        assert m.case_arms == 3
        # 1 (entry) + 3 arms; the case node itself does not add.
        assert m.decision_points == 4

    def test_each_loop_kind_adds_a_decision(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo(N: Integer);
var I: Integer;
begin
  while I < N do Inc(I);
  for I := 0 to N do Inc(I);
  repeat Dec(I); until I = 0;
end;
end.
""")
        assert m.loop_count == 3
        assert m.decision_points == 4

    def test_except_arm_counts(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo;
begin
  try
    DoIt;
  except
    on E: Exception do Log(E);
  end;
end;
end.
""")
        assert m.try_count == 1
        assert m.except_count == 1
        # try opens a region but doesn't add a decision; except does.
        assert m.decision_points == 2

    def test_boolean_operators_each_add_decision(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo(A, B, C: Boolean);
begin
  if A and B or C then DoIt;
end;
end.
""")
        assert m.boolean_op_count == 2  # one and, one or
        assert m.if_count == 1
        # 1 entry + 1 if + 2 boolean ops
        assert m.decision_points == 4


# ---------------------------------------------------------------------------
# Exit / Break / Continue (Pascal: identifiers, not keywords)
# ---------------------------------------------------------------------------


class TestControlFlowKeywords:
    def test_bare_exit_statement(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo;
begin
  Exit;
end;
end.
""")
        assert m.exit_count == 1

    def test_exit_with_argument(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
function Foo: Integer;
begin
  Result := 0;
  Exit(42);
end;
end.
""")
        assert m.exit_count == 1

    def test_break_and_continue(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo(N: Integer);
var I: Integer;
begin
  for I := 0 to N do
  begin
    if I = 5 then Break;
    if I < 3 then Continue;
  end;
end;
end.
""")
        assert m.break_count == 1
        assert m.continue_count == 1

    def test_multiple_exits_in_one_function(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
function Find(X: Integer): Boolean;
var I: Integer;
begin
  Result := False;
  if X < 0 then Exit;
  for I := 0 to X do
    if I = 5 then
    begin
      Result := True;
      Exit;
    end;
end;
end.
""")
        assert m.exit_count == 2

    def test_case_insensitive_exit(self) -> None:
        # Pascal is case-insensitive; lower / mixed case must still match.
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo;
begin
  exit;
end;
end.
""")
        assert m.exit_count == 1

    def test_qualified_call_does_not_count_as_exit(self) -> None:
        # `Self.Exit` is a method dispatch, not a control-flow statement.
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo;
begin
  Self.Exit;
end;
end.
""")
        assert m.exit_count is None


# ---------------------------------------------------------------------------
# Nesting depth
# ---------------------------------------------------------------------------


class TestNesting:
    def test_flat_function_has_low_depth(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo;
begin
  DoIt;
end;
end.
""")
        # The procedure body is itself a block — depth 1 is acceptable.
        assert (m.max_nesting_depth or 0) <= 2

    def test_deeply_nested_function_records_high_depth(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo(X: Integer);
begin
  if X = 1 then
  begin
    while X > 0 do
    begin
      if X = 5 then
      begin
        Dec(X);
      end;
    end;
  end;
end;
end.
""")
        # if -> block -> while -> block -> if -> block (plus outer block) ->
        # at minimum 4 nested blocks/conditionals beneath the outer body.
        assert (m.max_nesting_depth or 0) >= 4


# ---------------------------------------------------------------------------
# Try / Except / Finally / Raise
# ---------------------------------------------------------------------------


class TestTryExceptFinally:
    def test_separate_try_blocks_counted(self) -> None:
        # NB: `except_count` is keyed on `exceptionHandler` nodes, which
        # tree-sitter-pascal emits for `on E: T do …` clauses — *not* for
        # the bare `except` keyword. So a try/except with no `on` produces
        # try_count=1 and except_count=None. Each typed handler increments
        # except_count by one.
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo;
begin
  try
    DoIt;
  except
    on E: Exception do HandleIt;
  end;
  try
    DoIt;
  finally
    Cleanup;
  end;
end;
end.
""")
        assert m.try_count == 2
        assert m.except_count == 1
        assert m.finally_count == 1

    def test_bare_except_does_not_increment_except_count(self) -> None:
        # Documents the (debatable) current behaviour: a try/except with no
        # `on E: T do` handler reports try_count=1 but except_count=None.
        # Whether to count the bare except clause is a future judgement
        # call; this test pins current behaviour so a change shows up.
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo;
begin
  try
    DoIt;
  except
    HandleIt;
  end;
end;
end.
""")
        assert m.try_count == 1
        assert m.except_count is None

    def test_raise(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo;
begin
  raise Exception.Create('boom');
end;
end.
""")
        assert m.raise_count == 1


# ---------------------------------------------------------------------------
# Parameter counting
# ---------------------------------------------------------------------------


class TestParams:
    def test_no_params(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo;
begin
end;
end.
""")
        assert m.param_count is None
        assert m.params == []

    def test_each_identifier_in_a_group_counted(self) -> None:
        # `A, B, C: Integer; D: string` is two declArg groups but four
        # parameters. Counting declArg nodes would give 2 — wrong.
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo(A, B, C: Integer; D: string);
begin
end;
end.
""")
        assert m.param_count == 4
        assert m.params == ["A", "B", "C", "D"]

    def test_var_const_out_modifiers_do_not_inflate_count(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
procedure Foo(const A: Integer; var B: Integer; out C: Integer);
begin
end;
end.
""")
        assert m.param_count == 3
        assert m.params == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# Result := X / FuncName := X assignment tracking
# ---------------------------------------------------------------------------


class TestResultAssign:
    def test_result_assignment_counts(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
function Foo: Integer;
begin
  Result := 42;
end;
end.
""")
        assert m.result_assign_count == 1

    def test_function_name_assignment_counts(self) -> None:
        # Old-style Pascal: assign to the function name itself.
        m = _first_metrics("""\
unit T; interface implementation
function Foo: Integer;
begin
  Foo := 42;
end;
end.
""")
        assert m.result_assign_count == 1

    def test_other_assignments_do_not_count(self) -> None:
        m = _first_metrics("""\
unit T; interface implementation
function Foo: Integer;
var X: Integer;
begin
  X := 42;
  Result := X;
end;
end.
""")
        # Only `Result := X` matches; `X := 42` is an ordinary assignment.
        assert m.result_assign_count == 1


# ---------------------------------------------------------------------------
# Multiple-function snippets — confirm each function gets its own record
# ---------------------------------------------------------------------------


class TestMultipleFunctions:
    def test_two_functions_get_separate_records(self) -> None:
        records = _all_metrics("""\
unit T; interface implementation
procedure Alpha(X: Integer);
begin
  if X = 1 then DoIt;
end;

procedure Beta(A, B: Integer);
begin
  while A < B do Inc(A);
  while A > 0 do Dec(A);
end;
end.
""")
        assert len(records) == 2
        alpha, beta = records
        assert alpha.if_count == 1
        assert alpha.loop_count is None
        assert beta.if_count is None
        assert beta.loop_count == 2
        assert beta.params == ["A", "B"]


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


class TestValidateStructure:
    def test_well_formed_unit_no_errors(self) -> None:
        errors = _validate("""\
unit T;
interface
implementation
procedure Foo;
begin
end;
end.
""")
        assert errors == []

    def test_missing_end_dot_reported(self) -> None:
        errors = _validate("""\
unit T;
interface
implementation
procedure Foo;
begin
end;
""")
        assert any("missing 'end.'" in e for e in errors)

    def test_begin_end_imbalance_reported(self) -> None:
        # Two begins, one inner end — the missing inner end leaves the
        # procedure unclosed. tree-sitter will probably also flag a parse
        # error; we accept either signal as long as the file is reported
        # malformed.
        errors = _validate("""\
unit T;
interface
implementation
procedure Foo;
begin
  begin
end.
""")
        assert errors, "expected at least one structural error"
        text = " ".join(errors)
        assert "begin/end mismatch" in text or "parse error" in text


# ---------------------------------------------------------------------------
# Non-Pascal languages
# ---------------------------------------------------------------------------


class TestUnsupportedLanguage:
    def test_unknown_language_returns_empty_metrics_dict(self) -> None:
        # No walker registered for "rust" yet; contract is empty dict, no crash.
        # Use a Pascal tree as a stand-in — collect_function_metrics never
        # actually consults the tree's grammar when the language tag has no
        # walker.
        lang = _get_language("pascal")
        if lang is None:
            pytest.skip("tree-sitter-pascal grammar not loaded")
        src = b"unit T; interface implementation end."
        tree = Parser(lang).parse(src)
        assert collect_function_metrics(tree.root_node, src, "rust") == {}

    def test_unknown_language_validate_returns_empty(self) -> None:
        lang = _get_language("pascal")
        if lang is None:
            pytest.skip("tree-sitter-pascal grammar not loaded")
        src = b"unit T; interface implementation end."
        tree = Parser(lang).parse(src)
        assert validate_structure(tree.root_node, src, "rust") == []
