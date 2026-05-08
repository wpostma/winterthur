"""Tests for Pascal smell finder Phase 2 detectors.

Phase 1 (W1 with-statement) coverage is in :mod:`test_smells_pascal`,
which uses ``tmp_path`` fixtures. This file uses the in-memory
:class:`FileInfo` pattern (matches ``test_smells_python`` and
``test_smells_gexperts``) for the seven new Phase 2 rules:

* **E3 — empty-except** (bare ``try…except…end`` and empty
  ``on E: T do begin end`` forms; the code is E3 because Python's
  E1/E2 already occupy the global rule-code namespace for the
  conceptually-similar bare-except / silent-except)
* **G1 — goto-statement**
* **R1 — redundant-bool-compare** (``if X = True``, ``X <> False``)
* **U1 — uses-bloat** (>= 30 imports across all uses clauses)
* **UV1 — untyped-var-parameter** (``var X`` with no type)
* **PP1 — pointer-typed-parameter** (``Pointer``, ``P*``, ``^T``)
* **C1 — allocator-not-named-Create** (``Result := X.Create`` but the
  function name lacks Create/Make/New/etc.)
"""

from __future__ import annotations

from datetime import datetime

from winterthur.commands.smells import _scan_file
from winterthur.models import FileInfo


def _make_info(name: str = "fixture.pas") -> FileInfo:
    return FileInfo(
        path=name,
        abs_path=name,
        language="pascal",
        size_bytes=0,
        git_hash="",
        last_modified=datetime.fromtimestamp(0),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _scan(source: str, name: str = "fixture.pas") -> dict:
    return _scan_file(_make_info(name), source.encode("utf-8"), rule_filter=None)


def _findings_for(rec: dict, rule: str) -> list[dict]:
    return [f for f in rec["findings"] if f["rule"] == rule]


# ---------------------------------------------------------------------------
# E1 — empty-except
# ---------------------------------------------------------------------------


class TestEmptyExcept:
    def test_bare_empty_except_fires_E1(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo;
begin
  try
    Risky;
  except
  end;
end;

end.
""")
        e3 = _findings_for(rec, "E3")
        assert len(e3) == 1
        assert e3[0]["severity"] == "yellow"
        assert "Foo" in (e3[0]["function"] or "")

    def test_empty_on_handler_fires_E1(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo;
begin
  try
    Risky;
  except
    on E: Exception do
    begin
    end;
  end;
end;

end.
""")
        assert len(_findings_for(rec, "E3")) == 1

    def test_handler_with_logging_does_not_fire(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo;
begin
  try
    Risky;
  except
    on E: Exception do
      LogError(E.Message);
  end;
end;

end.
""")
        assert _findings_for(rec, "E3") == []

    def test_bare_except_with_statement_does_not_fire(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo;
begin
  try
    Risky;
  except
    LogIt;
  end;
end;

end.
""")
        assert _findings_for(rec, "E3") == []


# ---------------------------------------------------------------------------
# G1 — goto-statement
# ---------------------------------------------------------------------------


class TestGoto:
    def test_goto_fires_G1(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo;
label
  Done;
begin
  if X then
    goto Done;
  Done:
  ;
end;

end.
""")
        g1 = _findings_for(rec, "G1")
        assert len(g1) == 1
        assert g1[0]["severity"] == "red"

    def test_no_goto_no_finding(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo;
begin
  if X then DoIt;
end;

end.
""")
        assert _findings_for(rec, "G1") == []


# ---------------------------------------------------------------------------
# R1 — redundant-bool-compare
# ---------------------------------------------------------------------------


class TestRedundantBoolCompare:
    def test_eq_true_fires_R1(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(X: Boolean);
begin
  if X = True then DoIt;
end;

end.
""")
        assert len(_findings_for(rec, "R1")) == 1

    def test_eq_false_fires_R1(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(X: Boolean);
begin
  if X = False then DoIt;
end;

end.
""")
        assert len(_findings_for(rec, "R1")) == 1

    def test_neq_true_fires_R1(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(X: Boolean);
begin
  if X <> True then DoIt;
end;

end.
""")
        assert len(_findings_for(rec, "R1")) == 1

    def test_neq_false_fires_R1(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(X: Boolean);
begin
  if X <> False then DoIt;
end;

end.
""")
        assert len(_findings_for(rec, "R1")) == 1

    def test_plain_bool_does_not_fire(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(X: Boolean);
begin
  if X then DoIt;
  if not X then DoOther;
end;

end.
""")
        assert _findings_for(rec, "R1") == []

    def test_int_compare_does_not_fire(self) -> None:
        # `X = 0` is fine — not a comparison to True/False.
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(X: Integer);
begin
  if X = 0 then DoIt;
end;

end.
""")
        assert _findings_for(rec, "R1") == []


# ---------------------------------------------------------------------------
# U1 — uses-bloat
# ---------------------------------------------------------------------------


class TestUsesBloat:
    def test_uses_with_thirty_or_more_fires_U1(self) -> None:
        # Build a uses clause with 32 entries spread across interface
        # and implementation sections.
        iface_uses = ", ".join(f"U{i}" for i in range(20))
        impl_uses = ", ".join(f"V{i}" for i in range(12))
        rec = _scan(f"""\
unit T;
interface
uses {iface_uses};
implementation
uses {impl_uses};

procedure Foo;
begin
end;

end.
""")
        u1 = _findings_for(rec, "U1")
        assert len(u1) == 1
        assert u1[0]["severity"] == "yellow"
        # File-level finding has no enclosing function.
        assert u1[0]["function"] is None
        # Detail mentions the count.
        assert "32" in u1[0]["detail"]

    def test_below_threshold_does_not_fire(self) -> None:
        rec = _scan("""\
unit T;
interface
uses A, B, C, D, E;
implementation
uses F, G, H;

procedure Foo;
begin
end;

end.
""")
        assert _findings_for(rec, "U1") == []

    def test_at_threshold_exactly_fires(self) -> None:
        # 30 imports — boundary case.
        names = ", ".join(f"U{i}" for i in range(30))
        rec = _scan(f"""\
unit T;
interface
uses {names};
implementation

procedure Foo;
begin
end;

end.
""")
        assert len(_findings_for(rec, "U1")) == 1


# ---------------------------------------------------------------------------
# UV1 — untyped-var-parameter
# ---------------------------------------------------------------------------


class TestUntypedVar:
    def test_untyped_var_fires_UV1(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(var X);
begin
end;

end.
""")
        uv1 = _findings_for(rec, "UV1")
        assert len(uv1) == 1
        assert uv1[0]["severity"] == "yellow"

    def test_typed_var_does_not_fire(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(var X: Integer);
begin
end;

end.
""")
        assert _findings_for(rec, "UV1") == []

    def test_const_param_does_not_fire(self) -> None:
        # Untyped var is dangerous; const without type isn't quite the
        # same risk and wouldn't compile anyway in modern Delphi.
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(const X: string);
begin
end;

end.
""")
        assert _findings_for(rec, "UV1") == []


# ---------------------------------------------------------------------------
# PP1 — pointer-typed-parameter
# ---------------------------------------------------------------------------


class TestPointerParam:
    def test_pointer_type_fires_PP1(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(P: Pointer);
begin
end;

end.
""")
        pp1 = _findings_for(rec, "PP1")
        assert len(pp1) == 1
        assert "Pointer" in pp1[0]["detail"]

    def test_pchar_fires_PP1(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(S: PChar);
begin
end;

end.
""")
        assert len(_findings_for(rec, "PP1")) == 1

    def test_caret_pointer_fires_PP1(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(P: ^Integer);
begin
end;

end.
""")
        assert len(_findings_for(rec, "PP1")) == 1

    def test_normal_class_type_does_not_fire(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(L: TStringList);
begin
end;

end.
""")
        assert _findings_for(rec, "PP1") == []

    def test_basic_type_does_not_fire(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

procedure Foo(I: Integer; S: string; B: Boolean);
begin
end;

end.
""")
        assert _findings_for(rec, "PP1") == []


# ---------------------------------------------------------------------------
# C1 — allocator-not-named-Create
# ---------------------------------------------------------------------------


class TestAllocatorNotNamed:
    def test_get_returning_create_fires_C1(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

function GetList: TStringList;
begin
  Result := TStringList.Create;
end;

end.
""")
        c1 = _findings_for(rec, "C1")
        assert len(c1) == 1
        assert c1[0]["severity"] == "red"
        assert "GetList" in (c1[0]["function"] or "")

    def test_create_in_name_does_not_fire(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

function CreateList: TStringList;
begin
  Result := TStringList.Create;
end;

end.
""")
        assert _findings_for(rec, "C1") == []

    def test_make_in_name_does_not_fire(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

function MakeList: TStringList;
begin
  Result := TStringList.Create;
end;

end.
""")
        assert _findings_for(rec, "C1") == []

    def test_new_in_name_does_not_fire(self) -> None:
        rec = _scan("""\
unit T;
interface
implementation

function NewList: TStringList;
begin
  Result := TStringList.Create;
end;

end.
""")
        assert _findings_for(rec, "C1") == []

    def test_function_without_create_call_does_not_fire(self) -> None:
        # Returns an allocated object — but doesn't itself allocate it
        # via X.Create. We can only flag what we see; this is fine.
        rec = _scan("""\
unit T;
interface
implementation

function GetList: TStringList;
begin
  Result := FList;
end;

end.
""")
        assert _findings_for(rec, "C1") == []

    def test_class_method_uses_unqualified_name(self) -> None:
        # Method qualified as TFoo.Get — the unqualified `Get` doesn't
        # contain Create/Make/New, so C1 fires. Confirms we walk past
        # the genericDot to extract the unqualified name.
        rec = _scan("""\
unit T;
interface

type
  TFoo = class
    function Get: TStringList;
  end;

implementation

function TFoo.Get: TStringList;
begin
  Result := TStringList.Create;
end;

end.
""")
        c1 = _findings_for(rec, "C1")
        assert len(c1) == 1
        assert "TFoo.Get" in (c1[0]["function"] or "")


# ---------------------------------------------------------------------------
# Multi-smell combined fixture
# ---------------------------------------------------------------------------


_DELPHI_SMELL_SOUP = """\
unit smelltest;
interface
uses System, Classes, SysUtils;
implementation

procedure SmellySoup(var Buffer; P: PChar);
label
  Done;
begin
  try
    if Buffer = True then
      goto Done;
  except
  end;
  Done:;
end;

function GetWidget: TStringList;
begin
  Result := TStringList.Create;
end;

end.
"""


class TestMultiSmellSoup:
    def test_all_targeted_rules_fire(self) -> None:
        rec = _scan(_DELPHI_SMELL_SOUP)
        rules = {f["rule"] for f in rec["findings"]}
        # Each Phase 2 rule should trigger somewhere in this soup
        # (U1 won't — only 3 imports).
        for expected in ("E3", "G1", "R1", "UV1", "PP1", "C1"):
            assert expected in rules, (
                f"expected {expected} in {sorted(rules)}"
            )
