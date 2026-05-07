"""Pascal-specific AST-pattern smell finder.

Currently fires:

* **W1** — every ``with X do …`` statement (scope shadowing,
  greppability, kills static lookups).

NOT included here, deliberately:

* **A1 "exit-in-loop"** was rejected. Any single ``Exit;`` inside a
  ``while``/``for``/``repeat`` body is the idiomatic linear-search
  early-return pattern (every Find/Get/TryGet function uses it). The
  useful signal is COUNT, not LOCATION; two or more exits in one
  function is a sure sign the function should be decomposed. That's
  A4 (multiple-exits), driven from metrics_walker's ``exit_count`` —
  no positional walk needed.
"""

from __future__ import annotations

from typing import ClassVar

from ..walkers.base import _iter_descendants
from .base import SmellFinder, SmellHit


# tree-sitter-pascal emits one ``with`` node per source statement —
# even ``with X, Y, Z do …`` is one node. Counting nodes equals
# counting written statements.
_WITH_NODE_TYPES = frozenset({"with"})


class PascalSmellFinder(SmellFinder):
    language: ClassVar[str] = "pascal"

    def find(self, root_node, source: bytes) -> list[SmellHit]:
        hits: list[SmellHit] = []
        for start, end, fn_node in self._function_ranges(root_node):
            key = (start, end)
            for node in _iter_descendants(fn_node):
                if node.type in _WITH_NODE_TYPES:
                    hits.append(
                        SmellHit(
                            rule="W1",
                            line=node.start_point[0] + 1,
                            function_key=key,
                            detail="with-statement (scope shadowing)",
                        )
                    )
        return hits
