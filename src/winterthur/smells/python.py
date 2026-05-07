"""Python-specific AST-pattern smell finder.

Phase 1 detectors:

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
"""

from __future__ import annotations

from typing import ClassVar

from ..walkers.base import _iter_descendants
from .base import SmellFinder, SmellHit


# Literal node types that are mutable. ``set()`` / ``list()`` / ``dict()``
# call expressions ALSO bind a fresh-looking-but-shared object, but
# detecting them requires identifying the callee — left for a follow-up.
_MUTABLE_LITERAL_TYPES = frozenset({"list", "dictionary", "set"})


class PythonSmellFinder(SmellFinder):
    language: ClassVar[str] = "python"

    def find(self, root_node, source: bytes) -> list[SmellHit]:
        hits: list[SmellHit] = []
        for start, end, fn_node in self._function_ranges(root_node):
            key = (start, end)
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
