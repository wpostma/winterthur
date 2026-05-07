"""Tests for ``winterthur metrics`` command-shape helpers.

Walker behaviour itself is covered in :mod:`test_metrics_walker`; this
file confirms the JSON-record shape (terse output, zero counters
dropped, params only when non-empty) that LLM consumers depend on.

All tests build :class:`Symbol` and :class:`FunctionMetrics` objects
directly — no disk, no FileInfo, no tree-sitter parse.
"""

from __future__ import annotations

from winterthur.commands.metrics import _effective_loc, _function_record
from winterthur.metrics_walker import FunctionMetrics
from winterthur.models import Symbol


def _make_symbol(
    name: str = "Foo",
    start: int = 1,
    end: int = 5,
    kind: str = "function",
) -> Symbol:
    return Symbol(
        id=f"x::{name}",
        name=name,
        qualified_name=f"x.{name}",
        kind=kind,  # type: ignore[arg-type]
        signature="",
        start_line=start,
        end_line=end,
        docstring=None,
        language="pascal",
    )


class TestFunctionRecordShape:
    def test_basic_fields_always_present(self) -> None:
        sym = _make_symbol(name="Bar", start=5, end=10, kind="method")
        rec = _function_record(sym, b"\n" * 10, {})
        assert rec["name"] == "Bar"
        assert rec["qualified_name"] == "x.Bar"
        assert rec["kind"] == "method"
        assert rec["line_start"] == 5
        assert rec["line_end"] == 10
        assert rec["loc_total"] == 6
        assert "loc_effective" in rec

    def test_walker_miss_keeps_decision_points_at_one(self) -> None:
        sym = _make_symbol(start=1, end=3)
        rec = _function_record(sym, b"a\nb\nc\n", {})
        # decision_points always serialised, even with no walker data.
        assert rec["decision_points"] == 1
        # No counter fields when walker had nothing.
        assert "if_count" not in rec
        assert "try_count" not in rec
        assert "params" not in rec

    def test_walker_counters_pass_through(self) -> None:
        sym = _make_symbol(start=10, end=20)
        fm = FunctionMetrics(decision_points=3, if_count=2, try_count=1)
        rec = _function_record(sym, b"\n" * 20, {(10, 20): fm})
        assert rec["decision_points"] == 3
        assert rec["if_count"] == 2
        assert rec["try_count"] == 1
        # Counters that were never observed remain absent.
        assert "raise_count" not in rec
        assert "loop_count" not in rec
        assert "exit_count" not in rec

    def test_zero_valued_counter_dropped(self) -> None:
        # A counter that was explicitly set to 0 (rather than left None) is
        # also dropped — both `None` and `0` are uninformative for an LLM
        # consumer and waste tokens. The walker never emits 0, but be
        # defensive against future contributors who might.
        sym = _make_symbol(start=1, end=2)
        fm = FunctionMetrics(decision_points=1, if_count=0, loop_count=0)
        rec = _function_record(sym, b"\n", {(1, 2): fm})
        assert "if_count" not in rec
        assert "loop_count" not in rec

    def test_params_present_only_when_nonempty(self) -> None:
        sym = _make_symbol(start=1, end=2)
        fm_empty = FunctionMetrics()
        rec = _function_record(sym, b"\n", {(1, 2): fm_empty})
        assert "params" not in rec

        fm_with = FunctionMetrics(param_count=2, params=["A", "B"])
        rec2 = _function_record(sym, b"\n", {(1, 2): fm_with})
        assert rec2["params"] == ["A", "B"]
        assert rec2["param_count"] == 2

    def test_loc_total_handles_single_line_function(self) -> None:
        sym = _make_symbol(start=7, end=7)
        rec = _function_record(sym, b"\n" * 10, {})
        assert rec["loc_total"] == 1


class TestEffectiveLoc:
    def test_blank_lines_excluded(self) -> None:
        src = b"line1\n\nline2\n\n\nline3\n"
        # Lines 1..6: line1, blank, line2, blank, blank, line3 -> 3 effective
        assert _effective_loc(src, 1, 6) == 3

    def test_pascal_brace_block_comment_skipped(self) -> None:
        src = b"begin\n{ comment }\nDoIt;\nend;\n"
        assert _effective_loc(src, 1, 4) == 3

    def test_double_slash_comment_skipped(self) -> None:
        src = b"foo;\n// note\nbar;\n"
        assert _effective_loc(src, 1, 3) == 2

    def test_python_hash_comment_skipped(self) -> None:
        src = b"def f():\n    # comment\n    pass\n"
        assert _effective_loc(src, 1, 3) == 2

    def test_sql_dash_comment_skipped(self) -> None:
        src = b"SELECT 1\n-- note\nFROM dual\n"
        assert _effective_loc(src, 1, 3) == 2

    def test_invalid_range_returns_zero(self) -> None:
        assert _effective_loc(b"x\ny\n", 0, 5) == 0
        assert _effective_loc(b"x\ny\n", 5, 1) == 0
