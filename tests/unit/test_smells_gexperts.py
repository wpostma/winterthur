"""Smell-detector tests with fixtures inspired by real gexperts code.

Each fixture is a condensed/edited version of a pattern actually present
in the GExperts 1.3.26 source tree (``Framework`` directory). Citing the
inspiration matters because:

* It anchors the test to a real Delphi idiom rather than an artificial
  construct that might not survive contact with production code.
* It lets reviewers cross-check our rule firing against the original —
  the tradeoff being readability vs. lint sensitivity.

Fixtures live as triple-quoted strings; the scan runs entirely
in-memory by constructing a :class:`FileInfo` directly and calling
:func:`winterthur.commands.smells._scan_file`. No disk I/O, no
``tmp_path``.

Inspiration sources (paths under
``C:\\delphidev\\gexperts-1.3.26\\source\\gexperts\\source\\Framework\\``):

* ``GX_ClassMgr.pas:409-474``      ``TClassItem.LoadFromDir`` — deeply
  nested for/try/except/if with an explicit ``// Swallow exception``.
* ``GX_BaseExpert.pas:144-156``    ``TGX_BaseExpert.Destroy`` — bare
  ``except`` block in a destructor with the FixInsight-suppression
  comment ``FI:W501 Empty EXCEPT block``.
* ``GX_MacroParser.pas:387-426``   ``TMacroReplacer.GetParserToken`` —
  ``with FParser do case AToken of …`` (the only ``with`` statement in
  the framework directory).
* ``GX_MacroParser.pas:336-378``   ``TMacroSourceParser.GetClassToken``
  — many-decision parser inner loop with nested repeat/case.
"""

from __future__ import annotations

from datetime import datetime

from winterthur.commands.smells import _scan_file
from winterthur.models import FileInfo


# ---------------------------------------------------------------------------
# In-memory scan helper — no disk
# ---------------------------------------------------------------------------


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


def _rules(rec: dict) -> list[str]:
    return [f["rule"] for f in rec["findings"]]


def _findings_for(rec: dict, rule: str) -> list[dict]:
    return [f for f in rec["findings"] if f["rule"] == rule]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# Inspired by GX_ClassMgr.pas:409-474 (TClassItem.LoadFromDir + nested
# CollectFiles). The condensed version keeps the for/try/if/while/if
# spine that drives the deep-nesting metric and the swallowed-except
# pattern. Local procedure CollectFiles is dropped — Pascal nested procs
# are useful real-world but orthogonal to L3/A4.
_GEXPERTS_LOAD_FROM_DIR = """\
unit FixtureLoadFromDir;
interface
implementation

procedure LoadFromDir(const Dir: string; const Recurse: Boolean);
var
  i: Integer;
  FileList: TStringList;
  Search: TSearchRec;
  Code: Integer;
begin
  FileList := TStringList.Create;
  try
    Code := FindFirst(Dir + '*.pas', faAnyFile, Search);
    try
      while Code = 0 do
      begin
        if (Search.Attr and faDirectory) <> 0 then
        begin
          if Recurse and (Search.Name <> '.') and (Search.Name <> '..') then
            FileList.Add(Dir + Search.Name);
        end
        else
          if Pos('.pas', Search.Name) <> 0 then
          begin
            FileList.Add(Dir + Search.Name);
          end;
        Code := FindNext(Search);
      end;
    finally
      FindClose(Search);
    end;

    for i := 0 to FileList.Count - 1 do
    begin
      if FileList[i] <> '' then
      begin
        try
          LoadClasses(FileList[i]);
        except
          on E: Exception do
          begin
            if Verbose then
              LogException(E, FileList[i]);
            // Swallow exception
          end;
        end;
      end;
      DoOnParseFile(FileList[i], i + 1, FileList.Count);
    end;
  finally
    FreeAndNil(FileList);
  end;
end;

end.
"""


# Inspired by GX_BaseExpert.pas:144-156 (TGX_BaseExpert.Destroy). The
# ``// FI:W501 Empty EXCEPT block`` suppression in the original is the
# tell — gexperts authors knew a static checker would flag it. We can't
# yet detect "empty except block" as a rule, but the fixture is here so
# a future E1/E2 rule has a canonical regression input.
_GEXPERTS_DESTRUCTOR = """\
unit FixtureDestructor;
interface
implementation

destructor TThing.Destroy;
begin
  FActionInt := nil;
  FreeAndNil(FBitmap);
  try
    SetTotalCallCount(GetTotalCallCount + FCallCount);
  except
    // ignore exceptions in the destructor
  end;
  inherited;
end;

end.
"""


# Inspired by GX_MacroParser.pas:395-426 (TMacroReplacer.GetParserToken).
# The original wraps a 7-arm case in `with FParser do` — the only `with`
# in the framework directory, and a textbook W1 trigger. We collapse to
# 3 arms; the `with` is what we're testing, not the case shape.
_GEXPERTS_WITH_CASE = """\
unit FixtureWithCase;
interface
implementation

function GetToken(AToken: TMacroToken): string;
begin
  Result := '';
  with FParser do
    case AToken of
      matMethodClass:
        Result := GetMethodProcToken(True);
      matClass:
        Result := GetClassToken;
      matIdent:
        Result := GetIdentToken(True);
    end;
end;

end.
"""


# Inspired by the inner repeat/case spine of
# GX_MacroParser.pas:336-378 (TMacroSourceParser.GetClassToken). The
# original has ~20 decision points across nested repeat/if/case; this
# fixture stacks enough branches to clear the L2 yellow band (>=15
# decision points) without ballooning to L1 (>=150 LOC).
_GEXPERTS_MANY_DECISIONS = """\
unit FixtureManyDecisions;
interface
implementation

function ClassifyToken(TokenID: Integer; const Token: string;
                      const InClass: Boolean): string;
begin
  Result := '';
  if TokenID = 1 then
  begin
    if Token = 'class' then Result := 'class-decl'
    else if Token = 'record' then Result := 'record-decl'
    else if Token = 'interface' then Result := 'interface-decl'
    else if Token = 'object' then Result := 'object-decl';
  end
  else if TokenID = 2 then
  begin
    case Length(Token) of
      0: Result := 'empty';
      1: Result := 'single';
      2: Result := 'double';
      3: Result := 'short';
    else
      Result := 'long';
    end;
  end
  else if (TokenID = 3) and InClass then
  begin
    if Token = 'procedure' then Result := 'method'
    else if Token = 'function' then Result := 'func-method'
    else if Token = 'property' then Result := 'prop'
    else if Token = 'constructor' then Result := 'ctor'
    else if Token = 'destructor' then Result := 'dtor';
  end
  else
    Result := 'unknown';
end;

end.
"""


# Inspired by Find-style functions common in gexperts parsers (e.g.
# the inner identifier-search loops in GX_MacroParser.pas). The smell
# being targeted is "multiple Exits as the early-return idiom" — a
# single exit is fine, but two or more in one function is the A4
# yellow signal that the function should be decomposed.
_GEXPERTS_MULTI_EXIT = """\
unit FixtureMultiExit;
interface
implementation

function FindClass(const Tokens: TStringList; out ClassName: string): Boolean;
var
  I: Integer;
begin
  Result := False;
  ClassName := '';
  if Tokens = nil then Exit;
  if Tokens.Count = 0 then Exit;

  for I := 0 to Tokens.Count - 1 do
  begin
    if Pos('class', Tokens[I]) > 0 then
    begin
      ClassName := Tokens[I];
      Result := True;
      Exit;
    end;
  end;
end;

end.
"""


# Constructor/setup methods in the IDE-integration units routinely take
# 7-10 parameters because they thread together IDE handles, callback
# refs, options flags and identifying strings. P1 yellow is >=8.
_GEXPERTS_MANY_PARAMS = """\
unit FixtureManyParams;
interface
implementation

procedure RegisterTool(const Name: string; const Caption: string;
                      const ShortCut: Word; const Hint: string;
                      const ImageIndex: Integer; const Visible: Boolean;
                      const Enabled: Boolean; const Category: string;
                      const Owner: TComponent);
begin
  DoRegister(Name, Caption, ShortCut, Hint, ImageIndex, Visible, Enabled,
             Category, Owner);
end;

end.
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeepNesting:
    def test_loadfromdir_pattern_fires_L3(self) -> None:
        # The for/try/while/if-cascade from gexperts' LoadFromDir clears
        # the L3 yellow threshold (>=8 max_nesting_depth in the walker's
        # block-and-control-flow scheme).
        rec = _scan(_GEXPERTS_LOAD_FROM_DIR)
        l3 = _findings_for(rec, "L3")
        assert l3, f"expected L3 firing on LoadFromDir-pattern, got rules={_rules(rec)}"
        assert l3[0]["function"] and "LoadFromDir" in l3[0]["function"]

    def test_loadfromdir_no_parse_errors(self) -> None:
        rec = _scan(_GEXPERTS_LOAD_FROM_DIR)
        assert "errors" not in rec, rec.get("errors")


class TestWithStatement:
    def test_with_fparser_fires_W1(self) -> None:
        # GX_MacroParser is the only framework unit with a `with` — and
        # it shows up exactly once as `with FParser do case ...`. W1
        # should fire and attribute to the enclosing function.
        rec = _scan(_GEXPERTS_WITH_CASE)
        w1 = _findings_for(rec, "W1")
        assert len(w1) == 1
        assert w1[0]["severity"] == "yellow"
        assert w1[0]["function"] and "GetToken" in w1[0]["function"]


class TestManyDecisions:
    def test_classify_token_pattern_fires_L2(self) -> None:
        rec = _scan(_GEXPERTS_MANY_DECISIONS)
        l2 = _findings_for(rec, "L2")
        assert l2, f"expected L2 firing, got rules={_rules(rec)}"
        assert l2[0]["metric"] >= 15
        assert l2[0]["function"] and "ClassifyToken" in l2[0]["function"]


class TestMultiExit:
    def test_find_function_with_three_exits_fires_A4(self) -> None:
        # 3 Exits: two guards + one mid-loop early return. A4 yellow is
        # >=2, red is >=4, so we expect yellow.
        rec = _scan(_GEXPERTS_MULTI_EXIT)
        a4 = _findings_for(rec, "A4")
        assert len(a4) == 1
        assert a4[0]["severity"] == "yellow"
        assert a4[0]["metric"] == 3

    def test_find_function_no_W1(self) -> None:
        # Sanity: this fixture should NOT have a with-statement.
        rec = _scan(_GEXPERTS_MULTI_EXIT)
        assert _findings_for(rec, "W1") == []


class TestManyParams:
    def test_register_tool_pattern_fires_P1(self) -> None:
        rec = _scan(_GEXPERTS_MANY_PARAMS)
        p1 = _findings_for(rec, "P1")
        assert len(p1) == 1
        assert p1[0]["metric"] == 9
        assert p1[0]["function"] and "RegisterTool" in p1[0]["function"]


class TestDestructorPattern:
    """Bare ``except`` in a destructor — gexperts uses this idiom in
    several places (GX_BaseExpert.Destroy is the canonical example).

    The Pascal Phase 2 ``E3 — empty-except`` rule now fires on this
    pattern, including when the only thing inside the except block is
    a comment like ``// ignore exceptions in the destructor``. A
    deliberate comment does NOT take an empty handler off the smell
    list — the goal is to surface every silent swallow.
    """

    def test_destructor_parses_cleanly(self) -> None:
        rec = _scan(_GEXPERTS_DESTRUCTOR)
        assert "errors" not in rec, rec.get("errors")

    def test_destructor_fires_E3_empty_except(self) -> None:
        # Once the Pascal Phase 2 detectors landed, this destructor's
        # ``except // ignore … end`` started reporting E3 — exactly
        # what gexperts authors had to suppress with their inline
        # ``// FI:W501 Empty EXCEPT block`` directive.
        rec = _scan(_GEXPERTS_DESTRUCTOR)
        e3 = _findings_for(rec, "E3")
        assert len(e3) == 1
        assert e3[0]["severity"] == "yellow"
