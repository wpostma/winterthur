"""Tests for the Python smell finder (Phase 1 detectors).

Covers:

* **E1 — bare-except** — ``except:`` with no exception type
* **E2 — silent-except** — ``except`` body is just ``pass``
* **M1 — mutable-default-arg** — list/dict/set literal as default value

Plus the metric-driven rules that work on Python via the walker:
``L1`` god-method, ``L2`` god-decisions, ``L3`` deep-nesting, ``P1``
many-params, ``A4`` multiple-exits. We verify a couple here too so
the language wiring is end-to-end exercised.

All fixtures are inline triple-quoted strings; the scan runs
in-memory by constructing a :class:`FileInfo` directly.
"""

from __future__ import annotations

from datetime import datetime

from winterthur.commands.smells import _scan_file
from winterthur.models import FileInfo


def _make_info(name: str = "fixture.py") -> FileInfo:
    return FileInfo(
        path=name,
        abs_path=name,
        language="python",
        size_bytes=0,
        git_hash="",
        last_modified=datetime.fromtimestamp(0),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _scan(source: str, name: str = "fixture.py") -> dict:
    return _scan_file(_make_info(name), source.encode("utf-8"), rule_filter=None)


def _findings_for(rec: dict, rule: str) -> list[dict]:
    return [f for f in rec["findings"] if f["rule"] == rule]


def _rules(rec: dict) -> list[str]:
    return [f["rule"] for f in rec["findings"]]


# ---------------------------------------------------------------------------
# E1: bare except
# ---------------------------------------------------------------------------


class TestBareExcept:
    def test_bare_except_fires_E1(self) -> None:
        rec = _scan("""\
def f():
    try:
        risky()
    except:
        log.warn('oops')
""")
        e1 = _findings_for(rec, "E1")
        assert len(e1) == 1
        assert e1[0]["severity"] == "red"
        assert e1[0]["function"] and "f" in e1[0]["function"]

    def test_typed_except_does_not_fire_E1(self) -> None:
        rec = _scan("""\
def f():
    try:
        risky()
    except ValueError:
        log.warn('oops')
""")
        assert _findings_for(rec, "E1") == []

    def test_except_with_as_does_not_fire_E1(self) -> None:
        rec = _scan("""\
def f():
    try:
        risky()
    except ValueError as e:
        log.warn(str(e))
""")
        assert _findings_for(rec, "E1") == []

    def test_except_tuple_does_not_fire_E1(self) -> None:
        # `except (A, B):` is typed — ValueError tuple counts as a type.
        rec = _scan("""\
def f():
    try:
        risky()
    except (ValueError, KeyError):
        log.warn('caught')
""")
        assert _findings_for(rec, "E1") == []


# ---------------------------------------------------------------------------
# E2: silent except (body is just pass)
# ---------------------------------------------------------------------------


class TestSilentExcept:
    def test_pass_only_body_fires_E2(self) -> None:
        rec = _scan("""\
def f():
    try:
        risky()
    except ValueError:
        pass
""")
        e2 = _findings_for(rec, "E2")
        assert len(e2) == 1
        assert e2[0]["severity"] == "yellow"

    def test_bare_except_with_pass_fires_both_E1_and_E2(self) -> None:
        # ``except: pass`` is the worst form — fires E1 AND E2 at the
        # same line. Both should appear; the smell command sorts them
        # red-before-yellow, so E1 comes first.
        rec = _scan("""\
def f():
    try:
        risky()
    except:
        pass
""")
        rules = {f["rule"] for f in rec["findings"]}
        assert "E1" in rules
        assert "E2" in rules

    def test_logging_body_does_not_fire_E2(self) -> None:
        # Body is a log call, not pass — not silent in the strict sense.
        rec = _scan("""\
def f():
    try:
        risky()
    except ValueError:
        log.warn('oops')
""")
        assert _findings_for(rec, "E2") == []

    def test_reraise_body_does_not_fire_E2(self) -> None:
        rec = _scan("""\
def f():
    try:
        risky()
    except ValueError:
        raise
""")
        assert _findings_for(rec, "E2") == []


# ---------------------------------------------------------------------------
# M1: mutable default argument
# ---------------------------------------------------------------------------


class TestMutableDefaultArg:
    def test_list_literal_default_fires_M1(self) -> None:
        rec = _scan("def f(x=[]):\n    return x\n")
        m1 = _findings_for(rec, "M1")
        assert len(m1) == 1
        assert m1[0]["severity"] == "red"
        assert "list" in m1[0]["detail"]

    def test_dict_literal_default_fires_M1(self) -> None:
        rec = _scan("def f(x={}):\n    return x\n")
        m1 = _findings_for(rec, "M1")
        assert len(m1) == 1
        assert "dict" in m1[0]["detail"]

    def test_set_literal_default_fires_M1(self) -> None:
        rec = _scan("def f(x={1, 2}):\n    return x\n")
        m1 = _findings_for(rec, "M1")
        assert len(m1) == 1
        assert "set" in m1[0]["detail"]

    def test_typed_default_with_list_fires_M1(self) -> None:
        # `x: list = []` — typed_default_parameter shape, same bug.
        rec = _scan("""\
def f(x: list = []):
    return x
""")
        assert len(_findings_for(rec, "M1")) == 1

    def test_immutable_defaults_do_not_fire_M1(self) -> None:
        rec = _scan("""\
def f(a=None, b=0, c='', d=(1, 2), e=3.14, g=True):
    return (a, b, c, d, e, g)
""")
        assert _findings_for(rec, "M1") == []

    def test_set_call_does_not_fire_M1(self) -> None:
        # ``set()`` has the same bug semantically, but detecting call
        # forms is a Phase-2 enhancement; first cut is literal-only.
        # Pin current behaviour so the future improvement shows up in
        # the diff.
        rec = _scan("def f(x=set()):\n    return x\n")
        assert _findings_for(rec, "M1") == []

    def test_multiple_mutable_defaults_each_fire(self) -> None:
        rec = _scan("def f(a=[], b={}, c={1}):\n    return (a, b, c)\n")
        m1 = _findings_for(rec, "M1")
        assert len(m1) == 3


# ---------------------------------------------------------------------------
# D1: missing-docstring on a non-trivial public function
# ---------------------------------------------------------------------------


class TestMissingDocstring:
    """D1 fires when a public, non-trivial function has no docstring.

    Public = name does not start with ``_`` (so private helpers and
    dunders are exempt). Non-trivial = clears at least one of:
    >= 5 source lines, >= 3 non-self/cls params, any mutable-typed
    param.
    """

    def test_long_public_function_no_docstring_fires(self) -> None:
        # 6-line public function, no docstring -> "6 lines" signal.
        rec = _scan("""\
def render(template, data):
    result = []
    for k, v in data.items():
        result.append(f'{k}={v}')
    body = '|'.join(result)
    return template.format(body=body)
""")
        d1 = _findings_for(rec, "D1")
        assert len(d1) == 1
        assert d1[0]["severity"] == "yellow"
        assert "lines" in d1[0]["detail"]
        assert d1[0]["function"] == "render"

    def test_function_with_docstring_does_not_fire(self) -> None:
        rec = _scan('''\
def render(template, data):
    """Render template with the given data dict."""
    result = []
    for k, v in data.items():
        result.append(f'{k}={v}')
    return template.format(body='|'.join(result))
''')
        assert _findings_for(rec, "D1") == []

    def test_underscore_prefix_skipped(self) -> None:
        # Long, no docstring — but private. No D1.
        rec = _scan("""\
def _internal_helper(template, data):
    result = []
    for k, v in data.items():
        result.append(f'{k}={v}')
    body = '|'.join(result)
    return template.format(body=body)
""")
        assert _findings_for(rec, "D1") == []

    def test_dunder_skipped(self) -> None:
        # __init__ is exempt because the rule is "starts with _" and
        # dunders share that prefix. Long body, but no D1.
        rec = _scan("""\
class C:
    def __init__(self, a, b, c, d):
        self.a = a
        self.b = b
        self.c = c
        self.d = d
""")
        assert _findings_for(rec, "D1") == []

    def test_tiny_function_skipped(self) -> None:
        # 3 lines, 2 params, no mutable, no docstring -> trivial -> no D1.
        rec = _scan("""\
def add(a, b):
    return a + b
""")
        assert _findings_for(rec, "D1") == []

    def test_three_visible_params_triggers(self) -> None:
        # Short body but 3 params -> param-count signal trips.
        rec = _scan("""\
def configure(host, port, db):
    return None
""")
        d1 = _findings_for(rec, "D1")
        assert len(d1) == 1
        assert "3 params" in d1[0]["detail"]

    def test_self_does_not_count_toward_param_threshold(self) -> None:
        # Method with self + 2 args -> 2 visible -> trivial -> no D1.
        rec = _scan("""\
class C:
    def two_args(self, a, b):
        return a + b
""")
        assert _findings_for(rec, "D1") == []

    def test_method_with_three_visible_args_triggers(self) -> None:
        # self + 3 args -> 3 visible -> D1 fires.
        rec = _scan("""\
class C:
    def three_args(self, a, b, c):
        return a + b + c
""")
        d1 = _findings_for(rec, "D1")
        assert len(d1) == 1
        assert d1[0]["function"] == "C.three_args"

    def test_cls_excluded_like_self(self) -> None:
        rec = _scan("""\
class C:
    @classmethod
    def factory(cls, a, b):
        return cls()
""")
        assert _findings_for(rec, "D1") == []

    def test_mutable_list_annotation_triggers(self) -> None:
        rec = _scan("""\
def append_to(items: list, value):
    items.append(value)
""")
        d1 = _findings_for(rec, "D1")
        assert len(d1) == 1
        assert "mutable: items" in d1[0]["detail"]

    def test_mutable_dict_annotation_triggers(self) -> None:
        rec = _scan("""\
def update_with(target: dict, key, value):
    target[key] = value
""")
        d1 = _findings_for(rec, "D1")
        # Note: also has 3 params, so both signals trip; just confirm fire.
        assert len(d1) == 1
        assert "mutable: target" in d1[0]["detail"]

    def test_subscripted_list_annotation_triggers(self) -> None:
        # `list[int]` head is `list` -> mutable.
        rec = _scan("""\
def total(values: list[int]):
    return sum(values)
""")
        d1 = _findings_for(rec, "D1")
        assert len(d1) == 1
        assert "mutable: values" in d1[0]["detail"]

    def test_typing_dict_annotation_triggers(self) -> None:
        # ``Dict[str, int]`` head is ``Dict``.
        rec = _scan("""\
def merge(left: Dict[str, int], right):
    left.update(right)
""")
        d1 = _findings_for(rec, "D1")
        assert len(d1) == 1
        assert "mutable: left" in d1[0]["detail"]

    def test_immutable_annotations_do_not_trigger_mutable_signal(self) -> None:
        # `int` and `str` heads are not in the mutable set.
        rec = _scan("""\
def add(a: int, b: int) -> int:
    return a + b
""")
        # Trivial -> no D1.
        assert _findings_for(rec, "D1") == []

    def test_optional_wrapper_not_seen_as_mutable(self) -> None:
        # Known limitation documented in _annotation_head_name: we only
        # look at the outer head identifier. ``Optional[list]`` reads as
        # head=``Optional`` and does NOT trigger the mutable signal.
        rec = _scan("""\
def process(x: Optional[list]):
    return x
""")
        # Tiny function, no other signal -> no D1.
        assert _findings_for(rec, "D1") == []

    def test_multiple_signals_combined_in_detail(self) -> None:
        # Long + many params + mutable -> all three reported in detail.
        rec = _scan("""\
def configure(host, port, db, options: list):
    log.info('configuring')
    if options:
        for opt in options:
            apply(opt)
    connect(host, port, db)
""")
        d1 = _findings_for(rec, "D1")
        assert len(d1) == 1
        detail = d1[0]["detail"]
        assert "lines" in detail
        assert "params" in detail
        assert "mutable: options" in detail


# ---------------------------------------------------------------------------
# Metric-driven rules on Python (via walkers/python.py)
# ---------------------------------------------------------------------------


class TestMetricDrivenRulesOnPython:
    def test_multiple_returns_fire_A4(self) -> None:
        # In Python, `return` is the exit-equivalent — A4 is language-
        # neutral and fires on count regardless of keyword.
        rec = _scan("""\
def find(items, target):
    if items is None:
        return None
    if not items:
        return None
    for x in items:
        if x == target:
            return x
    return None
""")
        a4 = _findings_for(rec, "A4")
        assert len(a4) == 1
        assert a4[0]["metric"] == 4
        assert a4[0]["severity"] == "red"  # >=4 is red

    def test_many_params_fire_P1(self) -> None:
        rec = _scan("""\
def configure(host, port, user, password, db, timeout, ssl, retries, pool):
    pass
""")
        p1 = _findings_for(rec, "P1")
        assert len(p1) == 1
        assert p1[0]["metric"] == 9

    def test_clean_function_no_findings(self) -> None:
        rec = _scan("""\
def add(a, b):
    return a + b
""")
        assert rec["findings"] == []


# ---------------------------------------------------------------------------
# Function attribution — findings should carry the qualified name
# ---------------------------------------------------------------------------


class TestFindingAttribution:
    def test_class_method_findings_show_qualified_name(self) -> None:
        rec = _scan("""\
class Service:
    def fetch(self, url=[]):
        try:
            return get(url)
        except:
            pass
""")
        for f in rec["findings"]:
            assert f["function"] is not None
            assert "Service.fetch" == f["function"]

    def test_top_level_function_findings_show_bare_name(self) -> None:
        rec = _scan("""\
def helper(opts={}):
    pass
""")
        m1 = _findings_for(rec, "M1")
        assert len(m1) == 1
        assert m1[0]["function"] == "helper"
