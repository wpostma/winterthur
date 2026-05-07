"""Python-specific AST-pattern smell finder.

Detectors:

* **E1 — bare-except**: ``except:`` with no exception type. Catches
  ``BaseException``, which means it swallows ``SystemExit``,
  ``KeyboardInterrupt``, and ``MemoryError`` along with anything else.
  Almost always a bug; "I want to catch everything" should be written
  as ``except Exception:`` so the three control-flow exceptions still
  propagate.
* **E2 — silent-except**: an ``except`` clause whose body is exactly
  ``pass``. The exception is silently swallowed — no log, no reraise,
  no fallback. Sometimes deliberate (best-effort cleanup) but worth
  surfacing every time.
* **M1 — mutable-default-arg**: a parameter whose default value is a
  ``list``/``dict``/``set`` literal. The default is evaluated ONCE at
  function-definition time, so the same mutable object is shared
  across all calls — the canonical Python footgun.
* **D1 — missing-docstring**: non-trivial public function with no
  docstring. *Public* means name does not start with ``_`` (so private
  helpers ``_foo`` AND dunders ``__init__`` / ``__call__`` are
  exempt). *Non-trivial* means the function clears at least one of:
  >= 5 source lines, >= 3 non-self/cls parameters, or any parameter
  typed with a mutable container annotation (``list``, ``dict``,
  ``set``, ``bytearray``, the ``typing`` aliases, ``deque``, etc.,
  including subscripted forms ``list[int]``). Tiny well-named
  functions (``def add(a, b): return a + b``) don't trigger.
"""

from __future__ import annotations

from typing import ClassVar

from ..walkers.base import _iter_descendants
from .base import SmellFinder, SmellHit


# Literal node types that are mutable. ``set()`` / ``list()`` / ``dict()``
# call expressions ALSO bind a fresh-looking-but-shared object, but
# detecting them requires identifying the callee — left for a follow-up.
_MUTABLE_LITERAL_TYPES = frozenset({"list", "dictionary", "set"})

# Annotation head identifiers that signal a mutable container parameter
# for the D1 size-signal check. Subscripted forms (``list[int]``,
# ``Dict[str, int]``) match by their head identifier — we only inspect
# the outermost name, not type args.
_MUTABLE_TYPE_HEADS = frozenset({
    "list", "dict", "set", "bytearray",
    # typing module classics
    "List", "Dict", "Set",
    "MutableMapping", "MutableSequence", "MutableSet",
    "DefaultDict", "OrderedDict", "Counter", "ChainMap",
    # collections containers
    "deque",
})

# Conventional first-parameter names that don't count toward the D1
# parameter-threshold check.
_RECEIVER_NAMES = frozenset({"self", "cls"})

# D1 thresholds (inclusive). A function clears the docstring requirement
# only if it stays under all of them AND has no mutable-typed parameter.
_D1_LINE_THRESHOLD = 5     # source lines (start..end inclusive)
_D1_PARAM_THRESHOLD = 3    # non-self/cls parameter count


class PythonSmellFinder(SmellFinder):
    language: ClassVar[str] = "python"

    def find(self, root_node, source: bytes) -> list[SmellHit]:
        hits: list[SmellHit] = []
        for start, end, fn_node in self._function_ranges(root_node):
            key = (start, end)

            # Whole-function check: D1 missing-docstring on a
            # non-trivial public function.
            d1 = _missing_docstring(fn_node, source, start, end, key)
            if d1 is not None:
                hits.append(d1)

            # Per-descendant checks: E1, E2, M1.
            for node in _iter_descendants(fn_node):
                t = node.type
                if t == "except_clause":
                    hits.extend(_except_smells(node, key))
                elif t in ("default_parameter", "typed_default_parameter"):
                    hit = _mutable_default_arg(node, key)
                    if hit is not None:
                        hits.append(hit)
        return hits


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def _except_smells(except_clause, key: tuple[int, int]) -> list[SmellHit]:
    out: list[SmellHit] = []
    line = except_clause.start_point[0] + 1
    if _is_bare_except(except_clause):
        out.append(SmellHit(
            rule="E1",
            line=line,
            function_key=key,
            detail=(
                "bare except: catches BaseException — also swallows "
                "SystemExit/KeyboardInterrupt; use 'except Exception:' "
                "instead"
            ),
        ))
    if _is_silent_except(except_clause):
        out.append(SmellHit(
            rule="E2",
            line=line,
            function_key=key,
            detail=(
                "except clause body is just 'pass' — exception is "
                "silently swallowed (no log, no reraise)"
            ),
        ))
    return out


def _is_bare_except(except_clause) -> bool:
    """``except_clause`` with no exception-type child.

    A typed ``except ValueError:`` has an ``identifier`` child; an
    ``except ValueError as e:`` has an ``as_pattern`` child;
    ``except (A, B):`` has a ``tuple`` child. A bare ``except:`` has
    only the ``except`` keyword (anonymous), the ``:`` punctuation
    (anonymous), and the body ``block`` — no other named children.
    """
    for c in except_clause.children:
        if c.is_named and c.type != "block":
            return False
    return True


def _is_silent_except(except_clause) -> bool:
    """``except`` body block contains exactly ``pass`` and nothing else."""
    body = None
    for c in except_clause.children:
        if c.type == "block":
            body = c
            break
    if body is None:
        return False
    named = [c for c in body.children if c.is_named]
    return len(named) == 1 and named[0].type == "pass_statement"


def _mutable_default_arg(
    node, key: tuple[int, int]
) -> SmellHit | None:
    """Flag a default_parameter whose default is a list/dict/set literal."""
    value = _value_of_default(node)
    if value is None or value.type not in _MUTABLE_LITERAL_TYPES:
        return None
    label = {
        "list": "list",
        "dictionary": "dict",
        "set": "set",
    }.get(value.type, value.type)
    return SmellHit(
        rule="M1",
        line=node.start_point[0] + 1,
        function_key=key,
        detail=(
            f"mutable default argument: {label} literal is shared across "
            "calls — use None and create a fresh one in the body"
        ),
    )


def _value_of_default(node):
    """Return the value child of a (typed_)default_parameter, or None.

    Tries the named ``value`` field first; falls back to the last named
    child, which handles both ``default_parameter`` (name, value) and
    ``typed_default_parameter`` (name, type, value) shapes.
    """
    v = node.child_by_field_name("value")
    if v is not None:
        return v
    named = [c for c in node.children if c.is_named]
    return named[-1] if named else None


# ---------------------------------------------------------------------------
# D1 — missing-docstring on a non-trivial public function
# ---------------------------------------------------------------------------


def _missing_docstring(
    fn_node, source: bytes, start: int, end: int, key: tuple[int, int]
) -> SmellHit | None:
    """Return a D1 hit if *fn_node* should have a docstring and doesn't.

    Skip cases (no hit):
      - Function name starts with ``_`` (private helpers + dunders).
      - Function is trivial: stays under all D1 thresholds and has no
        mutable-typed parameter.
      - Function already has a docstring as its first statement.
    """
    name_node = fn_node.child_by_field_name("name")
    if name_node is None:
        return None
    name = _decode(name_node, source)
    if name.startswith("_"):
        return None

    if _has_docstring(fn_node):
        return None

    loc_total = end - start + 1
    visible_params = _visible_param_count(fn_node, source)
    mutable_params = _mutable_typed_params(fn_node, source)

    signals: list[str] = []
    if loc_total >= _D1_LINE_THRESHOLD:
        signals.append(f"{loc_total} lines")
    if visible_params >= _D1_PARAM_THRESHOLD:
        signals.append(f"{visible_params} params")
    if mutable_params:
        signals.append(f"mutable: {', '.join(mutable_params)}")
    if not signals:
        return None  # trivial — exempt

    return SmellHit(
        rule="D1",
        line=start,
        function_key=key,
        detail=f"missing docstring on public function ({', '.join(signals)})",
    )


def _has_docstring(fn_node) -> bool:
    """First statement of the body block is a bare string literal."""
    body = fn_node.child_by_field_name("body")
    if body is None:
        return False
    for c in body.children:
        if not c.is_named:
            continue
        if c.type != "expression_statement":
            return False
        for sub in c.children:
            if sub.is_named:
                return sub.type == "string"
        return False
    return False


def _visible_param_count(fn_node, source: bytes) -> int:
    """Count parameters, excluding a leading ``self`` or ``cls``.

    Treats the convention "first identifier-named param is the receiver"
    rather than checking enclosing-class context — same as how ``staticmethod``
    is invisible to us anyway. Punctuation (``,`` ``(``) and the
    positional/keyword separators (``/``, ``*``) don't count.
    """
    params = fn_node.child_by_field_name("parameters")
    if params is None:
        return 0
    count = 0
    seen_first = False
    for c in params.children:
        if not c.is_named:
            continue
        if c.type in ("positional_separator", "keyword_separator"):
            continue
        if not seen_first:
            seen_first = True
            first_name = _param_first_identifier(c, source)
            if first_name in _RECEIVER_NAMES:
                continue  # receiver — don't count toward visible total
        count += 1
    return count


def _mutable_typed_params(fn_node, source: bytes) -> list[str]:
    """Return parameter names whose annotation head is in :data:`_MUTABLE_TYPE_HEADS`."""
    params = fn_node.child_by_field_name("parameters")
    if params is None:
        return []
    out: list[str] = []
    for c in params.children:
        if c.type not in ("typed_parameter", "typed_default_parameter"):
            continue
        type_node = c.child_by_field_name("type")
        if type_node is None:
            continue
        head = _annotation_head_name(type_node, source)
        if head is not None and head in _MUTABLE_TYPE_HEADS:
            param_name = _param_first_identifier(c, source)
            if param_name:
                out.append(param_name)
    return out


def _annotation_head_name(type_node, source: bytes) -> str | None:
    """Return the head identifier of an annotation expression.

    Examples (head returned in parentheses):

    * ``list``                  → ``list``
    * ``list[int]``             → ``list``
    * ``Dict[str, int]``        → ``Dict``
    * ``Optional[list]``        → ``Optional`` (we don't peer into the
                                  parameterisation; a known limitation)
    * ``list | None``           → ``None`` returned for first cut

    Returns ``None`` for shapes we don't recognise (binary_operator
    union types, attribute paths like ``typing.List``, etc.).
    """
    n = type_node
    # Some tree-sitter-python versions emit a `type` wrapper node; unwrap.
    if n.type == "type":
        for c in n.children:
            if c.is_named:
                n = c
                break
    if n.type == "identifier":
        return _decode(n, source)
    if n.type in ("generic_type", "subscript"):
        for c in n.children:
            if c.type == "identifier":
                return _decode(c, source)
    return None


def _param_first_identifier(param_node, source: bytes) -> str | None:
    """Get the parameter's name from any parameter-shape node."""
    if param_node.type == "identifier":
        return _decode(param_node, source)
    # typed_parameter / default_parameter / typed_default_parameter all
    # nest the name as the first identifier child.
    if param_node.type in (
        "typed_parameter",
        "default_parameter",
        "typed_default_parameter",
    ):
        for c in param_node.children:
            if c.type == "identifier":
                return _decode(c, source)
    if param_node.type in ("list_splat_pattern", "dictionary_splat_pattern"):
        for c in param_node.children:
            if c.type == "identifier":
                return _decode(c, source)
    return None


def _decode(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
