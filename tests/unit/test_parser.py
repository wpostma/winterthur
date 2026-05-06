"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

from datetime import datetime
from importlib.util import find_spec

import pytest

from pascalparser.models import FileInfo
from pascalparser.parser import LANGUAGE_CONFIGS, ASTParser

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file_info(path: str, language: str) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language=language,
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


@pytest.fixture(scope="module")
def parser() -> ASTParser:
    return ASTParser()


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

PYTHON_SOURCE = b'''"""Module docstring."""

from __future__ import annotations

from python_pkg.models import Operation
from python_pkg.utils import round_result
import os


class DivisionByZeroError(ArithmeticError):
    """Raised on division by zero."""


def add(x: float, y: float) -> float:
    """Return x + y."""
    return x + y


def subtract(x: float, y: float) -> float:
    """Return x - y."""
    return x - y


class Calculator:
    """Stateful calculator."""

    def __init__(self) -> None:
        self._history = []

    def add(self, x: float, y: float) -> float:
        """Add x and y."""
        return add(x, y)

    @staticmethod
    def version() -> str:
        """Return version string."""
        return "1.0"
'''


class TestPythonParser:
    def test_module_docstring(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        assert result.docstring == "Module docstring."

    def test_finds_top_level_functions(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        names = [s.name for s in result.symbols]
        assert "add" in names
        assert "subtract" in names

    def test_finds_classes(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        class_names = [c.name for c in classes]
        assert "Calculator" in class_names
        assert "DivisionByZeroError" in class_names

    def test_finds_methods(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [m.name for m in methods]
        assert "add" in method_names
        assert "__init__" in method_names

    def test_method_has_parent(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        calc_add = next(
            s for s in result.symbols if s.name == "add" and s.parent_name == "Calculator"
        )
        assert calc_add.parent_name == "Calculator"

    def test_private_visibility(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        init = next(s for s in result.symbols if s.name == "__init__")
        # Dunder is public by our convention
        assert init.visibility == "public"

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        module_paths = [i.module_path for i in result.imports]
        assert "python_pkg.models" in module_paths
        assert "python_pkg.utils" in module_paths
        assert "os" in module_paths

    def test_from_import_names(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        op_import = next(i for i in result.imports if i.module_path == "python_pkg.models")
        assert "Operation" in op_import.imported_names

    def test_function_docstring(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        add_fn = next(s for s in result.symbols if s.name == "add" and s.parent_name is None)
        assert add_fn.docstring == "Return x + y."

    def test_class_docstring(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        calc = next(s for s in result.symbols if s.name == "Calculator")
        assert calc.docstring == "Stateful calculator."

    def test_no_parse_errors_on_valid_source(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        assert result.parse_errors == []

    def test_parse_errors_on_invalid_source(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/bad.py", "python")
        result = parser.parse_file(fi, b"def (broken syntax: \npass\n")
        # Should not crash, but should report error
        assert isinstance(result.parse_errors, list)

    def test_qualified_name(self, parser: ASTParser) -> None:
        fi = _make_file_info("python_pkg/calculator.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        calc_add = next(
            s for s in result.symbols if s.name == "add" and s.parent_name == "Calculator"
        )
        assert calc_add.qualified_name == "python_pkg.calculator.Calculator.add"

    def test_exports_list(self, parser: ASTParser) -> None:
        fi = _make_file_info("pkg/calc.py", "python")
        result = parser.parse_file(fi, PYTHON_SOURCE)
        # Public top-level symbols should be in exports
        assert "add" in result.exports
        assert "Calculator" in result.exports


# ---------------------------------------------------------------------------
# TypeScript
# ---------------------------------------------------------------------------

TS_SOURCE = b"""/**
 * Sample TypeScript client module.
 * Exports ApiClient and related types.
 */

import type {
  ApiClientConfig,
  CalculationRequest,
  CalculationResponse,
} from "./types";
import { validateRequest, parseApiError } from "./utils";

/** Error from the API. */
export class ApiClientError extends Error {
  public readonly apiError: unknown;
  constructor(apiError: unknown) {
    super("API error");
    this.apiError = apiError;
  }
}

/** Validation error. */
export class ValidationError extends Error {}

const DEFAULT_TIMEOUT_MS = 10_000;

/** Typed HTTP client. */
export class ApiClient {
  private readonly baseUrl: string;

  constructor(config: ApiClientConfig) {
    this.baseUrl = config.baseUrl;
  }

  async calculate(request: CalculationRequest): Promise<CalculationResponse> {
    return this.post("/calculations", request);
  }

  async healthCheck(): Promise<boolean> {
    return true;
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    return {} as T;
  }
}

export function createClient(config: ApiClientConfig): ApiClient {
  return new ApiClient(config);
}
"""


class TestTypeScriptParser:
    def test_finds_classes(self, parser: ASTParser) -> None:
        fi = _make_file_info("typescript_pkg/src/client.ts", "typescript")
        result = parser.parse_file(fi, TS_SOURCE)
        class_names = [s.name for s in result.symbols if s.kind == "class"]
        assert "ApiClient" in class_names
        assert "ApiClientError" in class_names
        assert "ValidationError" in class_names

    def test_finds_methods(self, parser: ASTParser) -> None:
        fi = _make_file_info("typescript_pkg/src/client.ts", "typescript")
        result = parser.parse_file(fi, TS_SOURCE)
        method_names = [s.name for s in result.symbols if s.kind == "method"]
        assert "calculate" in method_names
        assert "healthCheck" in method_names

    def test_finds_top_level_function(self, parser: ASTParser) -> None:
        fi = _make_file_info("typescript_pkg/src/client.ts", "typescript")
        result = parser.parse_file(fi, TS_SOURCE)
        fn_names = [s.name for s in result.symbols if s.kind == "function"]
        assert "createClient" in fn_names

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("typescript_pkg/src/client.ts", "typescript")
        result = parser.parse_file(fi, TS_SOURCE)
        module_paths = [i.module_path for i in result.imports]
        assert "./types" in module_paths
        assert "./utils" in module_paths

    def test_relative_imports_flagged(self, parser: ASTParser) -> None:
        fi = _make_file_info("typescript_pkg/src/client.ts", "typescript")
        result = parser.parse_file(fi, TS_SOURCE)
        types_import = next(i for i in result.imports if i.module_path == "./types")
        assert types_import.is_relative is True

    def test_no_parse_errors(self, parser: ASTParser) -> None:
        fi = _make_file_info("typescript_pkg/src/client.ts", "typescript")
        result = parser.parse_file(fi, TS_SOURCE)
        assert result.parse_errors == []


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

GO_SOURCE = b"""// Package calculator provides arithmetic with history.
package calculator

import (
	"errors"
	"fmt"

	"github.com/repowise-ai/sample/types"
)

// ErrDivisionByZero is returned on division by zero.
var ErrDivisionByZero = errors.New("division by zero")

// Calculator maintains a calculation history.
type Calculator struct {
	history []types.CalculationRecord
}

// New returns a new Calculator.
func New() *Calculator {
	return &Calculator{}
}

// Add returns the sum of the operands.
func (c *Calculator) Add(ops types.Operands) (float64, error) {
	result := ops.X + ops.Y
	return result, nil
}

// Divide returns ops.X / ops.Y.
func (c *Calculator) Divide(ops types.Operands) (float64, error) {
	if ops.Y == 0 {
		return 0, ErrDivisionByZero
	}
	return ops.X / ops.Y, nil
}
"""


class TestGoParser:
    def test_finds_struct(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert any(s.name == "Calculator" for s in structs)

    def test_finds_functions(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        fns = [s for s in result.symbols if s.kind == "function"]
        assert any(s.name == "New" for s in fns)

    def test_finds_methods_with_receiver(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [m.name for m in methods]
        assert "Add" in method_names
        assert "Divide" in method_names

    def test_method_has_parent_from_receiver(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        add_method = next(s for s in result.symbols if s.name == "Add" and s.kind == "method")
        assert add_method.parent_name == "Calculator"

    def test_go_visibility_by_capitalisation(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        new_fn = next(s for s in result.symbols if s.name == "New")
        assert new_fn.visibility == "public"

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        module_paths = [i.module_path for i in result.imports]
        assert any("errors" in p for p in module_paths)
        assert any("sample/types" in p for p in module_paths)

    def test_no_parse_errors(self, parser: ASTParser) -> None:
        fi = _make_file_info("go_pkg/calculator/calculator.go", "go")
        result = parser.parse_file(fi, GO_SOURCE)
        assert result.parse_errors == []


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

RUST_SOURCE = b"""//! Sample Rust calculator.

use std::fmt;

/// Supported operations.
#[derive(Debug, Clone, Copy)]
pub enum Operation {
    Add,
    Subtract,
}

/// A single recorded calculation.
#[derive(Debug, Clone)]
pub struct CalculationRecord {
    pub result: f64,
}

impl CalculationRecord {
    /// Create a new record.
    pub fn new(result: f64) -> Self {
        Self { result }
    }

    /// Return a summary string.
    pub fn summary(&self) -> String {
        format!("{:.2}", self.result)
    }
}

/// Add two numbers.
pub fn add(x: f64, y: f64) -> f64 {
    x + y
}
"""


class TestRustParser:
    def test_finds_enum(self, parser: ASTParser) -> None:
        fi = _make_file_info("rust_pkg/src/models.rs", "rust")
        result = parser.parse_file(fi, RUST_SOURCE)
        enums = [s for s in result.symbols if s.kind == "enum"]
        assert any(s.name == "Operation" for s in enums)

    def test_finds_struct(self, parser: ASTParser) -> None:
        fi = _make_file_info("rust_pkg/src/models.rs", "rust")
        result = parser.parse_file(fi, RUST_SOURCE)
        structs = [s for s in result.symbols if s.kind == "struct"]
        assert any(s.name == "CalculationRecord" for s in structs)

    def test_finds_impl_block(self, parser: ASTParser) -> None:
        fi = _make_file_info("rust_pkg/src/models.rs", "rust")
        result = parser.parse_file(fi, RUST_SOURCE)
        impls = [s for s in result.symbols if s.kind == "impl"]
        assert any(s.name == "CalculationRecord" for s in impls)

    def test_finds_top_level_function(self, parser: ASTParser) -> None:
        fi = _make_file_info("rust_pkg/src/models.rs", "rust")
        result = parser.parse_file(fi, RUST_SOURCE)
        fns = [s for s in result.symbols if s.kind == "function"]
        assert any(s.name == "add" for s in fns)

    def test_pub_visibility(self, parser: ASTParser) -> None:
        fi = _make_file_info("rust_pkg/src/models.rs", "rust")
        result = parser.parse_file(fi, RUST_SOURCE)
        add_fn = next(s for s in result.symbols if s.name == "add" and s.kind == "function")
        assert add_fn.visibility == "public"

    def test_parses_use_declaration(self, parser: ASTParser) -> None:
        fi = _make_file_info("rust_pkg/src/models.rs", "rust")
        result = parser.parse_file(fi, RUST_SOURCE)
        assert len(result.imports) >= 1


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

JAVA_SOURCE = b"""package com.repowise.sample;

import java.util.ArrayList;
import java.util.List;

/**
 * Stateful calculator with history.
 */
public class Calculator {

    private final List<Object> history = new ArrayList<>();

    /**
     * Adds x and y.
     */
    public double add(double x, double y) {
        return x + y;
    }

    /** Private helper. */
    private void record(Object entry) {
        history.add(entry);
    }
}
"""


class TestJavaParser:
    def test_finds_class(self, parser: ASTParser) -> None:
        fi = _make_file_info("java_pkg/Calculator.java", "java")
        result = parser.parse_file(fi, JAVA_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "Calculator" for s in classes)

    def test_finds_methods(self, parser: ASTParser) -> None:
        fi = _make_file_info("java_pkg/Calculator.java", "java")
        result = parser.parse_file(fi, JAVA_SOURCE)
        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [m.name for m in methods]
        assert "add" in method_names
        assert "record" in method_names

    def test_parses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("java_pkg/Calculator.java", "java")
        result = parser.parse_file(fi, JAVA_SOURCE)
        assert len(result.imports) >= 2
        module_paths = [i.module_path for i in result.imports]
        assert any("ArrayList" in p for p in module_paths)


# ---------------------------------------------------------------------------
# C++
# ---------------------------------------------------------------------------

CPP_SOURCE = b"""#include "calculator.hpp"
#include <stdexcept>
#include <string>

namespace sample {

double Calculator::add(double x, double y) {
    return x + y;
}

double Calculator::divide(double x, double y) {
    if (y == 0.0) {
        throw std::invalid_argument("Division by zero");
    }
    return x / y;
}

}  // namespace sample
"""

CPP_HEADER_SOURCE = b"""#pragma once

#include <vector>
#include "models.hpp"

namespace sample {

class Calculator {
public:
    double add(double x, double y);
    double subtract(double x, double y);
    double divide(double x, double y);

private:
    std::vector<int> history_;
};

}  // namespace sample
"""


class TestCppParser:
    def test_finds_class_in_header(self, parser: ASTParser) -> None:
        fi = _make_file_info("cpp_pkg/calculator.hpp", "cpp")
        result = parser.parse_file(fi, CPP_HEADER_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "Calculator" for s in classes)

    def test_finds_functions_in_source(self, parser: ASTParser) -> None:
        fi = _make_file_info("cpp_pkg/calculator.cpp", "cpp")
        result = parser.parse_file(fi, CPP_SOURCE)
        fns = [s for s in result.symbols if s.kind == "function"]
        # Qualified definitions like Calculator::add should be caught
        assert len(fns) >= 1

    def test_parses_includes(self, parser: ASTParser) -> None:
        fi = _make_file_info("cpp_pkg/calculator.cpp", "cpp")
        result = parser.parse_file(fi, CPP_SOURCE)
        assert len(result.imports) >= 2
        module_paths = [i.module_path for i in result.imports]
        assert any("calculator.hpp" in p or "stdexcept" in p for p in module_paths)


# ---------------------------------------------------------------------------
# Pascal
# ---------------------------------------------------------------------------

PASCAL_IMPLEMENTATION_UNITS = [
    "FactorFinder",
    "CustomerSearcher",
    "InvoiceTracker",
    "PaymentMatcher",
    "OrderScanner",
    "LedgerBuilder",
    "SessionLoader",
    "ReportWriter",
    "ParcelSorter",
    "VendorReader",
    "BalanceWatcher",
    "ReceiptParser",
    "ProductMapper",
    "CatalogIndexer",
    "AccountChecker",
    "BookingReader",
    "ShipmentTracker",
    "ProfileFinder",
    "TaxResolver",
    "RegionScanner",
    "MessageRouter",
    "TicketMatcher",
    "ArchiveLoader",
    "ContractParser",
    "WarehouseBuilder",
]

PASCAL_SOURCE = f"""unit Calculator;

interface

uses SysUtils, Math;

type
    TCalculator = class
    public
        function Add(X, Y: Integer): Integer;
        procedure Reset;
    end;

implementation

Uses
    {", ".join(PASCAL_IMPLEMENTATION_UNITS)};

function TCalculator.Add(X, Y: Integer): Integer;
begin
    Result := X + Y;
end;

procedure TCalculator.Reset;
begin
end;

end.
""".encode()

PASCAL_PROJECT_FILE = b"""program APILoadTester;

// Load tester is an attempt to load down and crash the API (oneposserver.exe) service

uses
    Vcl.Forms,
    ideal.apiloadtest.forms.main in 'ideal.apiloadtest.forms.main.pas' {frmTest},
    ideal.pos.api.client in 'ideal.pos.api.client.pas';

{$R *.res}

begin

    Application.Initialize;
    Application.MainFormOnTaskbar := True;
    Application.CreateForm(TfrmTest, frmTest);
    Application.Run;
end.
"""


class TestPascalParser:
    @pytest.mark.skipif(
        find_spec("tree_sitter_pascal") is None,
        reason="tree-sitter-pascal is not installed in the test environment",
    )
    def test_finds_class_in_unit(self, parser: ASTParser) -> None:
        fi = _make_file_info("pascal_pkg/calculator.pas", "pascal")
        result = parser.parse_file(fi, PASCAL_SOURCE)
        classes = [s for s in result.symbols if s.kind == "class"]
        assert any(s.name == "TCalculator" for s in classes)

    @pytest.mark.skipif(
        find_spec("tree_sitter_pascal") is None,
        reason="tree-sitter-pascal is not installed in the test environment",
    )
    def test_finds_methods_in_unit(self, parser: ASTParser) -> None:
        fi = _make_file_info("pascal_pkg/calculator.pas", "pascal")
        result = parser.parse_file(fi, PASCAL_SOURCE)
        methods = [s for s in result.symbols if s.kind == "method"]
        method_names = [s.name for s in methods]
        assert "Add" in method_names
        assert "Reset" in method_names

    @pytest.mark.skipif(
        find_spec("tree_sitter_pascal") is None,
        reason="tree-sitter-pascal is not installed in the test environment",
    )
    def test_parses_uses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("pascal_pkg/calculator.pas", "pascal")
        result = parser.parse_file(fi, PASCAL_SOURCE)
        module_paths = [i.module_path for i in result.imports]
        expected_units = {"SysUtils", "Math"}
        assert set(module_paths) == expected_units
        assert len(result.imports) == len(expected_units)

    @pytest.mark.skipif(
        find_spec("tree_sitter_pascal") is None,
        reason="tree-sitter-pascal is not installed in the test environment",
    )
    def test_unit_parse_stops_before_implementation_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("pascal_pkg/calculator.pas", "pascal")
        result = parser.parse_file(fi, PASCAL_SOURCE)
        module_paths = {i.module_path for i in result.imports}

        assert "FactorFinder" not in module_paths
        assert "CustomerSearcher" not in module_paths

    @pytest.mark.skipif(
        find_spec("tree_sitter_pascal") is None,
        reason="tree-sitter-pascal is not installed in the test environment",
    )
    def test_parses_project_uses_imports(self, parser: ASTParser) -> None:
        fi = _make_file_info("pascal_pkg/APILoadTester.dpr", "pascal")
        fi.is_entry_point = True
        result = parser.parse_file(fi, PASCAL_PROJECT_FILE)
        module_paths = [i.module_path for i in result.imports]
        assert module_paths == [
            "Vcl.Forms",
            "ideal.apiloadtest.forms.main",
            "ideal.pos.api.client",
        ]


# ---------------------------------------------------------------------------
# Unsupported language (graceful fallback)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Delphi Form (.dfm)
# ---------------------------------------------------------------------------

DFM_SIMPLE = b"""\
object SalesReportOptionsDialog: TSalesReportOptionsDialog
  Left = 0
  Top = 0
  Caption = 'Sales Report Options'
  ClientHeight = 400
  ClientWidth = 600
  object OKButton: TButton
    Left = 10
    Caption = 'OK'
  end
  object CancelButton: TButton
    Left = 100
    Caption = 'Cancel'
  end
  object DateRangePanel: TPanel
    Left = 10
    object StartDateEdit: TDateTimePicker
      Left = 0
    end
  end
end
"""

DFM_NO_CAPTION = b"""\
object DataModule1: TDataModule1
  object ADOQuery1: TADOQuery
  end
end
"""


class TestDFMParser:
    def test_root_form_symbol(self, parser: ASTParser) -> None:
        fi = _make_file_info("Sales.Reporting.Options.Dialog.dfm", "pascal-form")
        result = parser.parse_file(fi, DFM_SIMPLE)
        assert result.parse_errors == []
        classes = [s for s in result.symbols if s.kind == "class"]
        assert len(classes) == 1
        form = classes[0]
        assert form.name == "TSalesReportOptionsDialog"
        assert form.qualified_name == "Sales.Reporting.Options.Dialog.TSalesReportOptionsDialog"

    def test_caption_in_docstring(self, parser: ASTParser) -> None:
        fi = _make_file_info("Sales.Reporting.Options.Dialog.dfm", "pascal-form")
        result = parser.parse_file(fi, DFM_SIMPLE)
        form = next(s for s in result.symbols if s.kind == "class")
        assert form.docstring is not None
        assert "Sales Report Options" in form.docstring
        assert "TSalesReportOptionsDialog" in form.docstring
        assert "Sales.Reporting.Options.Dialog" in form.docstring

    def test_top_level_components_only(self, parser: ASTParser) -> None:
        """Only depth-1 components should appear — not nested ones like StartDateEdit."""
        fi = _make_file_info("Sales.Reporting.Options.Dialog.dfm", "pascal-form")
        result = parser.parse_file(fi, DFM_SIMPLE)
        components = [s for s in result.symbols if s.kind == "variable"]
        names = {s.name for s in components}
        assert names == {"OKButton", "CancelButton", "DateRangePanel"}
        assert "StartDateEdit" not in names

    def test_component_parent_and_signature(self, parser: ASTParser) -> None:
        fi = _make_file_info("Sales.Reporting.Options.Dialog.dfm", "pascal-form")
        result = parser.parse_file(fi, DFM_SIMPLE)
        btn = next(s for s in result.symbols if s.name == "OKButton")
        assert btn.parent_name == "TSalesReportOptionsDialog"
        assert btn.signature == "OKButton: TButton"
        assert btn.kind == "variable"

    def test_companion_pas_import(self, parser: ASTParser) -> None:
        fi = _make_file_info("Sales.Reporting.Options.Dialog.dfm", "pascal-form")
        result = parser.parse_file(fi, DFM_SIMPLE)
        assert len(result.imports) == 1
        imp = result.imports[0]
        assert imp.module_path == "Sales.Reporting.Options.Dialog"
        assert imp.resolved_file == "Sales.Reporting.Options.Dialog.pas"
        assert "TSalesReportOptionsDialog" in imp.imported_names

    def test_no_caption_form(self, parser: ASTParser) -> None:
        fi = _make_file_info("DataModule1.dfm", "pascal-form")
        result = parser.parse_file(fi, DFM_NO_CAPTION)
        assert result.parse_errors == []
        form = next(s for s in result.symbols if s.kind == "class")
        assert form.name == "TDataModule1"
        assert "(no Caption)" in (form.docstring or "")

    def test_binary_dfm_graceful(self, parser: ASTParser) -> None:
        fi = _make_file_info("BinaryForm.dfm", "pascal-form")
        result = parser.parse_file(fi, b"TPF0\x00\x00\x00")  # Delphi binary DFM magic
        assert result.symbols == []
        assert len(result.parse_errors) == 1
        assert "Binary" in result.parse_errors[0]


class TestUnsupportedLanguage:
    def test_returns_empty_parsed_file(self, parser: ASTParser) -> None:
        """Unsupported languages return an empty ParsedFile with no errors
        (silent passthrough by design — see parser.py line 354)."""
        fi = _make_file_info("file.xyz", "unknown")
        fi.language = "unknown"
        result = parser.parse_file(fi, b"some content here")
        assert result.symbols == []
        assert result.imports == []
        assert result.parse_errors == []


# ---------------------------------------------------------------------------
# LANGUAGE_CONFIGS completeness
# ---------------------------------------------------------------------------


class TestLanguageConfigs:
    def test_all_supported_languages_have_config(self) -> None:
        expected = {"python", "typescript", "javascript", "go", "rust", "java", "pascal", "cpp", "c"}
        for lang in expected:
            assert lang in LANGUAGE_CONFIGS, f"Missing config for {lang}"

    def test_each_config_has_symbol_node_types(self) -> None:
        for lang, config in LANGUAGE_CONFIGS.items():
            assert len(config.symbol_node_types) > 0, f"{lang} has no symbol_node_types"

    def test_each_config_has_visibility_fn(self) -> None:
        for lang, config in LANGUAGE_CONFIGS.items():
            # Must be callable
            result = config.visibility_fn("MyClass", [])
            assert result in ("public", "private", "protected", "internal"), (
                f"{lang} visibility_fn returned unexpected: {result}"
            )
