"""Tests for the ``winterthur smells`` command and its supporting walker.

Each test parses a small Pascal fixture and asserts that the expected
rule codes fire. The fixtures intentionally pile up several smells in
one function so we can verify the per-function attribution as well as
the rule firing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from winterthur.commands.smells import _scan_file, run, RULE_NAMES
from winterthur.io_helpers import file_info_from_path


_PASCAL_HEAVY = """\
unit smelltest;

interface

type
  TThing = class
    procedure Smelly;
    procedure Tiny;
    procedure ManyParams(A, B, C, D, E, F, G, H: Integer);
  end;

implementation

procedure TThing.Smelly;
var
  Q: Integer;
begin
  with Self do
  begin
    while Q < 10 do
    begin
      if Q = 5 then
      begin
        Exit;
      end;
      Inc(Q);
    end;
  end;
  if Q > 0 then
    if Q > 1 then
      if Q > 2 then
        if Q > 3 then
          if Q > 4 then
            if Q > 5 then
              Q := Q + 1;
  Exit;
end;

procedure TThing.Tiny;
begin
end;

procedure TThing.ManyParams(A, B, C, D, E, F, G, H: Integer);
begin
end;

end.
"""


def _scan(tmp_path: Path, source: str) -> dict:
    p = tmp_path / "fixture.pas"
    p.write_bytes(source.encode("utf-8"))
    info, src_bytes = file_info_from_path(p)
    return _scan_file(info, src_bytes, rule_filter=None)


def _rules_in(rec: dict) -> set[str]:
    return {f["rule"] for f in rec["findings"]}


def _findings_for_rule(rec: dict, rule: str) -> list[dict]:
    return [f for f in rec["findings"] if f["rule"] == rule]


class TestSmellsCore:
    def test_with_statement_fires_W1(self, tmp_path: Path) -> None:
        rec = _scan(tmp_path, _PASCAL_HEAVY)
        w1 = _findings_for_rule(rec, "W1")
        assert len(w1) == 1
        assert w1[0]["severity"] == "yellow"
        assert "TThing.Smelly" in w1[0]["function"]

    def test_no_A1_rule_anymore(self, tmp_path: Path) -> None:
        # A1 (exit-in-loop) was removed: it false-positived on every
        # idiomatic Find/Get/TryGet linear-search pattern. The actually
        # useful exit-related signal is COUNT (A4), not LOCATION.
        rec = _scan(tmp_path, _PASCAL_HEAVY)
        assert _findings_for_rule(rec, "A1") == []

    def test_multiple_exits_fires_A4(self, tmp_path: Path) -> None:
        rec = _scan(tmp_path, _PASCAL_HEAVY)
        a4 = _findings_for_rule(rec, "A4")
        assert len(a4) == 1
        assert a4[0]["metric"] == 2  # one in the loop, one at the bottom

    def test_many_params_counts_each_identifier(self, tmp_path: Path) -> None:
        rec = _scan(tmp_path, _PASCAL_HEAVY)
        p1 = _findings_for_rule(rec, "P1")
        # Pascal packs `A, B, C, D, E, F, G, H: Integer` into one declArg.
        # The walker must count each identifier before the `:`, not each
        # declArg group — so 8 params, not 1.
        assert len(p1) == 1
        assert p1[0]["metric"] == 8

    def test_tiny_function_produces_no_findings(self, tmp_path: Path) -> None:
        rec = _scan(tmp_path, _PASCAL_HEAVY)
        tiny_findings = [
            f for f in rec["findings"]
            if f.get("function") and "Tiny" in f["function"]
        ]
        assert tiny_findings == []

    def test_findings_sorted_red_before_yellow(self, tmp_path: Path) -> None:
        rec = _scan(tmp_path, _PASCAL_HEAVY)
        severities = [f["severity"] for f in rec["findings"]]
        # Once we hit yellow we should not see red again.
        seen_yellow = False
        for s in severities:
            if s == "yellow":
                seen_yellow = True
            elif s == "red":
                assert not seen_yellow, "red finding appeared after yellow"


class TestSmellsCli:
    def test_run_returns_nonzero_when_findings_present(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        p = tmp_path / "fixture.pas"
        p.write_bytes(_PASCAL_HEAVY.encode("utf-8"))

        import argparse
        ns = argparse.Namespace(
            files=[str(p)],
            json=True,
            rules=None,
        )
        rc = run(ns)
        assert rc == 1  # findings exist
        out = json.loads(capsys.readouterr().out)
        assert out["files"][0]["findings"]

    def test_run_returns_zero_for_clean_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        clean = (
            "unit clean;\n"
            "interface\n"
            "implementation\n"
            "procedure A; begin end;\n"
            "end.\n"
        )
        p = tmp_path / "clean.pas"
        p.write_bytes(clean.encode("utf-8"))

        import argparse
        ns = argparse.Namespace(
            files=[str(p)],
            json=True,
            rules=None,
        )
        rc = run(ns)
        assert rc == 0

    def test_rule_filter_drops_unselected(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        p = tmp_path / "fixture.pas"
        p.write_bytes(_PASCAL_HEAVY.encode("utf-8"))

        import argparse
        ns = argparse.Namespace(
            files=[str(p)],
            json=True,
            rules="W1",
        )
        rc = run(ns)
        assert rc == 1
        out = json.loads(capsys.readouterr().out)
        rules = {f["rule"] for f in out["files"][0]["findings"]}
        assert rules == {"W1"}

    def test_unknown_rule_is_a_hard_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        p = tmp_path / "fixture.pas"
        p.write_bytes(_PASCAL_HEAVY.encode("utf-8"))

        import argparse
        ns = argparse.Namespace(
            files=[str(p)],
            json=True,
            rules="W1,X9",
        )
        rc = run(ns)
        assert rc == 2
        err = capsys.readouterr().err
        assert "X9" in err


class TestRuleNamesSanity:
    def test_every_severity_band_threshold_has_a_name(self) -> None:
        # Each rule code that smells.py knows about should have a friendly
        # name — otherwise text output prints the bare code with no label.
        for rule in ("L1", "L2", "L3", "P1", "A4", "W1"):
            assert rule in RULE_NAMES
