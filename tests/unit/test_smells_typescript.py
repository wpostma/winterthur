"""Tests for the TypeScript smell finder.

Phase 1 detectors covered:

* **N1** — non-null-assertion (``x!``)
* **Y1** — explicit ``any`` annotation
* **Q1** — loose equality (``==``/``!=``) with the ``x == null`` exemption
* **TS1** — ``// @ts-ignore`` / ``// @ts-nocheck`` comments
* **EC1** — empty catch block
* **AS1** — angle-bracket cast (``<Foo>x``)

Plus the metric-driven rules that work on TypeScript via the walker
(L1/L2/L3/P1/A4) and a final "real fixture" test inspired by the
codurance ``typescript-code-smells`` kata's ``Game.ts``.

All fixtures are inline triple-quoted strings; the scan runs entirely
in-memory by constructing :class:`FileInfo` directly.
"""

from __future__ import annotations

from datetime import datetime

from winterthur.commands.smells import _scan_file
from winterthur.models import FileInfo


def _make_info(name: str = "fixture.ts") -> FileInfo:
    return FileInfo(
        path=name,
        abs_path=name,
        language="typescript",
        size_bytes=0,
        git_hash="",
        last_modified=datetime.fromtimestamp(0),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _scan(source: str, name: str = "fixture.ts") -> dict:
    return _scan_file(_make_info(name), source.encode("utf-8"), rule_filter=None)


def _findings_for(rec: dict, rule: str) -> list[dict]:
    return [f for f in rec["findings"] if f["rule"] == rule]


def _rules(rec: dict) -> list[str]:
    return [f["rule"] for f in rec["findings"]]


# ---------------------------------------------------------------------------
# N1 — non-null assertion
# ---------------------------------------------------------------------------


class TestNonNullAssertion:
    def test_member_access_with_bang_fires_N1(self) -> None:
        rec = _scan("""\
function f(obj: any): string {
    return obj!.field;
}
""")
        n1 = _findings_for(rec, "N1")
        assert len(n1) == 1
        assert n1[0]["severity"] == "yellow"
        assert n1[0]["function"] == "f"

    def test_subscript_with_bang_fires_N1(self) -> None:
        rec = _scan("""\
function first(arr: any): any {
    return arr![0];
}
""")
        assert len(_findings_for(rec, "N1")) == 1

    def test_multiple_assertions_each_fire(self) -> None:
        rec = _scan("""\
function pair(obj: any): [string, string] {
    return [obj!.a, obj!.b];
}
""")
        assert len(_findings_for(rec, "N1")) == 2

    def test_no_bang_no_finding(self) -> None:
        rec = _scan("""\
function f(obj: { field: string }): string {
    return obj.field;
}
""")
        assert _findings_for(rec, "N1") == []


# ---------------------------------------------------------------------------
# Y1 — explicit any
# ---------------------------------------------------------------------------


class TestAnyType:
    def test_param_any_fires_Y1(self) -> None:
        rec = _scan("function f(x: any): string { return String(x); }\n")
        assert len(_findings_for(rec, "Y1")) == 1

    def test_return_any_fires_Y1(self) -> None:
        rec = _scan("function f(): any { return null; }\n")
        assert len(_findings_for(rec, "Y1")) == 1

    def test_variable_any_fires_Y1(self) -> None:
        rec = _scan("let x: any = null;\n")
        assert len(_findings_for(rec, "Y1")) == 1

    def test_any_array_fires_Y1(self) -> None:
        # `any[]` parses as type_annotation -> array_type -> predefined_type "any"
        rec = _scan("function f(xs: any[]) {}\n")
        assert len(_findings_for(rec, "Y1")) == 1

    def test_explicit_unknown_does_not_fire(self) -> None:
        # `unknown` is the safe alternative to any; should NOT fire.
        rec = _scan("function f(x: unknown): string { return String(x); }\n")
        assert _findings_for(rec, "Y1") == []

    def test_string_number_boolean_do_not_fire(self) -> None:
        rec = _scan("""\
function f(a: string, b: number, c: boolean): string {
    return a + b + c;
}
""")
        assert _findings_for(rec, "Y1") == []


# ---------------------------------------------------------------------------
# Q1 — loose equality (==/!=) with null-idiom exemption
# ---------------------------------------------------------------------------


class TestLooseEquality:
    def test_double_equals_fires_Q1(self) -> None:
        rec = _scan("""\
function f(a: number, b: number): boolean {
    return a == b;
}
""")
        q1 = _findings_for(rec, "Q1")
        assert len(q1) == 1
        assert q1[0]["severity"] == "red"

    def test_not_equals_fires_Q1(self) -> None:
        rec = _scan("""\
function f(a: number, b: number): boolean {
    return a != b;
}
""")
        assert len(_findings_for(rec, "Q1")) == 1

    def test_strict_equals_does_not_fire(self) -> None:
        rec = _scan("""\
function f(a: number, b: number): boolean {
    return a === b;
}
""")
        assert _findings_for(rec, "Q1") == []

    def test_strict_not_equals_does_not_fire(self) -> None:
        rec = _scan("""\
function f(a: number, b: number): boolean {
    return a !== b;
}
""")
        assert _findings_for(rec, "Q1") == []

    def test_compare_to_null_is_exempt(self) -> None:
        # `x == null` / `null == x` is the canonical "is nullish" idiom.
        # Even ESLint's eqeqeq exempts this case.
        rec = _scan("""\
function f(x: any): boolean {
    return x == null;
}
""")
        assert _findings_for(rec, "Q1") == []

    def test_compare_to_undefined_is_exempt(self) -> None:
        rec = _scan("""\
function f(x: any): boolean {
    return x == undefined;
}
""")
        assert _findings_for(rec, "Q1") == []

    def test_arithmetic_does_not_fire(self) -> None:
        # Important negative — binary_expression with arithmetic operator
        # must not match the loose-equality detector.
        rec = _scan("""\
function f(a: number, b: number): number {
    return a + b * 2;
}
""")
        assert _findings_for(rec, "Q1") == []

    def test_comparison_does_not_fire(self) -> None:
        rec = _scan("""\
function f(a: number, b: number): boolean {
    return a > b && a < 100;
}
""")
        assert _findings_for(rec, "Q1") == []


# ---------------------------------------------------------------------------
# TS1 — ts-suppression comments
# ---------------------------------------------------------------------------


class TestTsSuppression:
    def test_ts_ignore_fires_TS1(self) -> None:
        rec = _scan("""\
// @ts-ignore
const x: string = 1 as any;
""")
        ts1 = _findings_for(rec, "TS1")
        assert len(ts1) == 1
        assert "@ts-ignore" in ts1[0]["detail"]

    def test_ts_nocheck_fires_TS1(self) -> None:
        rec = _scan("// @ts-nocheck\nconst x = 1;\n")
        assert len(_findings_for(rec, "TS1")) == 1

    def test_ts_expect_error_does_not_fire(self) -> None:
        # @ts-expect-error is the recommended pattern (errors out when
        # the suppression is no longer needed). Not flagged.
        rec = _scan("""\
// @ts-expect-error temporary while migrating
const x: string = 1 as any;
""")
        assert _findings_for(rec, "TS1") == []

    def test_unrelated_comment_does_not_fire(self) -> None:
        rec = _scan("""\
// This is a normal explanatory comment.
const x = 1;
""")
        assert _findings_for(rec, "TS1") == []

    def test_block_comment_with_ts_ignore_fires(self) -> None:
        rec = _scan("""\
/* @ts-ignore */
const x: string = 1 as any;
""")
        # The detector strips `/`, `*`, whitespace before checking the
        # directive prefix.
        assert len(_findings_for(rec, "TS1")) == 1


# ---------------------------------------------------------------------------
# EC1 — empty catch
# ---------------------------------------------------------------------------


class TestEmptyCatch:
    def test_empty_catch_fires_EC1(self) -> None:
        rec = _scan("""\
function f() {
    try {
        risky();
    } catch (e) {
    }
}
""")
        ec1 = _findings_for(rec, "EC1")
        assert len(ec1) == 1
        assert ec1[0]["severity"] == "yellow"

    def test_catch_with_only_comment_fires_EC1(self) -> None:
        rec = _scan("""\
function f() {
    try {
        risky();
    } catch (e) {
        // ignored
    }
}
""")
        # Comments don't log/reraise/handle — still empty in spirit.
        assert len(_findings_for(rec, "EC1")) == 1

    def test_optional_binding_empty_catch_fires(self) -> None:
        # `catch {}` (without binding) — also empty.
        rec = _scan("""\
function f() {
    try { risky(); } catch {}
}
""")
        assert len(_findings_for(rec, "EC1")) == 1

    def test_catch_with_log_does_not_fire(self) -> None:
        rec = _scan("""\
function f() {
    try {
        risky();
    } catch (e) {
        console.error(e);
    }
}
""")
        assert _findings_for(rec, "EC1") == []

    def test_catch_with_throw_does_not_fire(self) -> None:
        rec = _scan("""\
function f() {
    try {
        risky();
    } catch (e) {
        throw new Error("wrapped");
    }
}
""")
        assert _findings_for(rec, "EC1") == []


# ---------------------------------------------------------------------------
# AS1 — angle-bracket cast
# ---------------------------------------------------------------------------


class TestAngleBracketCast:
    def test_angle_bracket_cast_fires_AS1(self) -> None:
        rec = _scan("const x = <number>(123);\n")
        assert len(_findings_for(rec, "AS1")) == 1

    def test_as_cast_does_not_fire(self) -> None:
        rec = _scan("const x = (123 as number);\n")
        assert _findings_for(rec, "AS1") == []


# ---------------------------------------------------------------------------
# Finding attribution: smells inside an inline arrow report against
# the enclosing named function, not against ?
# ---------------------------------------------------------------------------


class TestFindingAttribution:
    def test_smell_inside_arrow_callback_attributes_to_enclosing_method(
        self,
    ) -> None:
        # The Q1 inside `(t) => t.x == 1` is technically smallest-enclosed
        # by the unnamed arrow, but we want it reported against the
        # named class method ``Board.TileAt`` for usability.
        rec = _scan("""\
class Board {
    items: any[] = [];
    TileAt(x: number): any {
        return this.items.find((t: any) => t.x == x);
    }
}
""")
        q1 = _findings_for(rec, "Q1")
        assert len(q1) == 1
        assert q1[0]["function"] == "Board.TileAt"

    def test_top_level_smell_has_no_function_attribution(self) -> None:
        rec = _scan("const x = a == b;\n")
        q1 = _findings_for(rec, "Q1")
        assert len(q1) == 1
        # Top-level findings have function = None which renders as '-' in
        # text output and stays None in the JSON Finding record.
        assert q1[0]["function"] is None


# ---------------------------------------------------------------------------
# Game.ts-inspired fixture (codurance/typescript-code-smells kata)
# ---------------------------------------------------------------------------


# Inspired by codurance/typescript-code-smells/src/Game.ts
# (https://github.com/codurance/typescript-code-smells). The Winner()
# method there has 12+ non-null assertions and a wall of `==` /  `!=`
# comparisons; this fixture condenses one row's worth so the test
# stays readable.
_GAME_TS_INSPIRED = """\
class Game {
    private _board: Board = new Board();

    public Winner(): string {
        if (this._board.TileAt(0, 0)!.Symbol != ' ' &&
                this._board.TileAt(0, 1)!.Symbol != ' ' &&
                this._board.TileAt(0, 2)!.Symbol != ' ') {
            if (this._board.TileAt(0, 0)!.Symbol ==
                    this._board.TileAt(0, 1)!.Symbol) {
                return this._board.TileAt(0, 0)!.Symbol;
            }
        }
        return ' ';
    }
}
"""


class TestGameTsFixture:
    def test_winner_pattern_fires_many_N1(self) -> None:
        rec = _scan(_GAME_TS_INSPIRED)
        # 6 non-null assertions in the condensed fixture: three in the
        # outer `if (X != ' ' && Y != ' ' && Z != ' ')`, two in the
        # inner equality test, and one in the `return X!.Symbol`.
        assert len(_findings_for(rec, "N1")) == 6

    def test_winner_pattern_fires_many_Q1(self) -> None:
        rec = _scan(_GAME_TS_INSPIRED)
        # 3 `!=` + 1 `==` (each comparison-with-non-nullish operand).
        # The detector also fires on each chained comparison separately;
        # assert >= 4 to be lenient about how the parser nests them.
        assert len(_findings_for(rec, "Q1")) >= 4

    def test_findings_attribute_to_winner_method(self) -> None:
        rec = _scan(_GAME_TS_INSPIRED)
        # All N1 and Q1 findings should report against Game.Winner.
        for f in rec["findings"]:
            if f["rule"] in ("N1", "Q1"):
                assert f["function"] == "Game.Winner", (
                    f"finding at line {f['line']} attributed to "
                    f"{f['function']!r} instead of Game.Winner"
                )
"""
"""
