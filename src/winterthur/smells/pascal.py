"""Pascal-specific AST-pattern smell finder.

Detectors:

* **W1 — with-statement**: every ``with X do …`` shadows scope and
  kills static lookups.
* **E3 — empty-except**: ``try…except…end`` with no statements (the
  bare form), or ``on E: T do begin end`` with an empty handler block.
  The gexperts framework directory has 5+ literal ``// Swallow
  exception`` comments illustrating this pattern. (Code is ``E3``
  rather than ``E1`` because Python already owns ``E1`` (bare-except)
  and ``E2`` (silent-except) in the global rule-code namespace.)
* **G1 — goto-statement**: any ``goto`` keyword. Universal
  anti-pattern; rarely seen in modern Delphi but always wrong.
* **R1 — redundant-bool-compare**: ``if X = True then`` /
  ``X = False`` / ``X <> True`` / ``X <> False``. ``if X then`` works.
* **U1 — uses-bloat**: a unit's combined ``uses`` clauses (interface +
  implementation) total >= 30 imports. Indicates a god-unit; bumps
  compile dependencies and review cost.
* **UV1 — untyped-var-parameter**: ``procedure Foo(var X)`` with no
  type. Reference-by-address with the type system disabled — caller
  responsible for size and meaning.
* **PP1 — pointer-typed-parameter**: parameter typed as ``Pointer``,
  any ``P*`` Win32-style alias (``PChar``, ``PWideChar``, ``PByte`` …)
  or ``^TFoo`` direct pointer. Almost always a Win32-API edge or a
  type-system bypass.
* **C1 — allocator-not-named-Create**: a function whose body contains
  ``Result := X.Create(...)`` (or ``X.NewInstance``) but whose name
  doesn't include any of Create/Make/Allocate/Alloc/New/Build/
  Construct/From/Init. Caller can't tell from the name that they own
  the result and have to ``Free`` it — the canonical Delphi
  ownership-leak trap.

NOT included, deliberately:

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

# Method names that signal "this returns an allocated object."
# Convention covers Delphi VCL constructors plus a few RTL/3rd-party
# patterns. Case-sensitive — Pascal is case-insensitive but the
# tree-sitter source bytes preserve the author's casing, and these
# names are conventionally Pascal-cased.
_ALLOCATOR_METHODS = frozenset({
    "Create",
    "NewInstance",
    "Construct",
})

# Substrings (case-insensitive) that, when present in a function name,
# tell the caller "you'll get an allocated object back; you own it."
_ALLOCATOR_NAMING_TOKENS = (
    "create", "make", "alloc", "new", "build", "construct",
    "from", "init", "spawn",
)

# U1: imports threshold — keep in sync with smells.md if it codifies one.
_USES_BLOAT_THRESHOLD = 30


class PascalSmellFinder(SmellFinder):
    language: ClassVar[str] = "pascal"

    def find(self, root_node, source: bytes) -> list[SmellHit]:
        hits: list[SmellHit] = []

        # File-level: U1 uses-bloat (counts across all declUses, both
        # interface and implementation sections).
        u1 = _uses_bloat(root_node)
        if u1 is not None:
            hits.append(u1)

        for start, end, fn_node in self._function_ranges(root_node):
            key = (start, end)

            # Whole-function check: C1 allocator-not-named.
            c1 = _allocator_not_named(fn_node, source, key)
            if c1 is not None:
                hits.append(c1)

            # Per-descendant checks: W1, E1, G1, R1, UV1, PP1.
            for node in _iter_descendants(fn_node):
                t = node.type
                if t in _WITH_NODE_TYPES:
                    hits.append(SmellHit(
                        rule="W1",
                        line=node.start_point[0] + 1,
                        function_key=key,
                        detail="with-statement (scope shadowing)",
                    ))
                elif t == "try":
                    hits.extend(_empty_except_smells(node, key))
                elif t == "kGoto":
                    hits.append(SmellHit(
                        rule="G1",
                        line=node.start_point[0] + 1,
                        function_key=key,
                        detail="goto statement — restructure into a normal "
                               "control-flow construct",
                    ))
                elif t == "exprBinary":
                    if _is_redundant_bool_compare(node):
                        hits.append(SmellHit(
                            rule="R1",
                            line=node.start_point[0] + 1,
                            function_key=key,
                            detail="redundant comparison to True/False — "
                                   "use the boolean expression directly",
                        ))
                elif t == "declArg":
                    if _is_untyped_var(node):
                        hits.append(SmellHit(
                            rule="UV1",
                            line=node.start_point[0] + 1,
                            function_key=key,
                            detail="untyped `var` parameter — caller must "
                                   "manage size and meaning manually",
                        ))
                    pname = _pointer_param_type_name(node, source)
                    if pname is not None:
                        hits.append(SmellHit(
                            rule="PP1",
                            line=node.start_point[0] + 1,
                            function_key=key,
                            detail=f"pointer-typed parameter (`{pname}`) — "
                                   "type-system edge or Win32-API surface",
                        ))
        return hits


# ---------------------------------------------------------------------------
# E3 — empty-except (Pascal-specific; E1/E2 are Python's bare/silent except)
# ---------------------------------------------------------------------------


def _empty_except_smells(
    try_node, key: tuple[int, int]
) -> list[SmellHit]:
    """Detect both the bare ``try…except…end`` and empty ``on E: T do begin end`` forms."""
    hits: list[SmellHit] = []

    handlers = [c for c in try_node.children if c.type == "exceptionHandler"]

    # Form 1: bare except with no handlers and no statements.
    if not handlers and _bare_except_is_empty(try_node):
        # Anchor the finding to the kExcept token's line for a sensible
        # column when the user opens the file.
        line = _child_line(try_node, "kExcept") or (try_node.start_point[0] + 1)
        hits.append(SmellHit(
            rule="E3",
            line=line,
            function_key=key,
            detail="empty `except` clause — exception silently swallowed "
                   "(no log, no reraise, no fallback)",
        ))

    # Form 2: each on-handler with an empty body. Body may be a
    # `begin…end` block OR an inline single statement; "empty" means
    # neither shape is present.
    for h in handlers:
        if _handler_is_empty(h):
            hits.append(SmellHit(
                rule="E3",
                line=h.start_point[0] + 1,
                function_key=key,
                detail="empty `on <Exception> do` handler — exception "
                       "silently swallowed",
            ))
    return hits


def _bare_except_is_empty(try_node) -> bool:
    """A try with no on-handlers is empty iff nothing meaningful sits
    between ``kExcept`` and the closing ``kEnd``.

    Comments don't count as "meaningful" — gexperts has multiple cases
    of ``except // ignore exceptions in the destructor end;`` which is
    exactly the swallow-pattern we want to surface.
    """
    saw_except = False
    for c in try_node.children:
        if c.type == "kExcept":
            saw_except = True
            continue
        if not saw_except:
            continue
        if c.type == "kEnd":
            return True
        # Comments are named nodes too; ignore them.
        if c.is_named and c.type != "comment":
            return False
    return False


def _block_is_empty(block_node) -> bool:
    """A block is empty if it has no statement children (kBegin/kEnd
    bracketing don't count, comments don't count either — a bare comment
    in an except block is still a swallow)."""
    if block_node is None:
        return True
    for c in block_node.children:
        if not c.is_named:
            continue
        if c.type in ("kBegin", "kEnd", "comment"):
            continue
        return False
    return True


def _handler_is_empty(handler_node) -> bool:
    """Return True when ``on E: T do …`` has neither a non-empty block
    nor an inline statement after ``do``.

    Pascal's grammar emits the body as either a ``block`` child (the
    ``begin…end`` form) or one of several statement-shaped nodes
    inline (``statement`` / ``if`` / ``while`` / ``for`` / etc.).
    Comments don't count — a comment-only handler is still a swallow.
    """
    saw_do = False
    for c in handler_node.children:
        if c.type == "kDo":
            saw_do = True
            continue
        if not saw_do:
            continue
        if c.type == "block":
            return _block_is_empty(c)
        if c.is_named and c.type != "comment":
            # Anything else after `do` that's a real named node is the
            # handler's inline statement — not empty.
            return False
    return True  # nothing after `do`


def _child_line(parent, child_type: str) -> int | None:
    for c in parent.children:
        if c.type == child_type:
            return c.start_point[0] + 1
    return None


# ---------------------------------------------------------------------------
# R1 — redundant boolean comparison
# ---------------------------------------------------------------------------


def _is_redundant_bool_compare(expr_binary) -> bool:
    """``X = True`` / ``X = False`` / ``X <> True`` / ``X <> False``."""
    has_eq_or_neq = False
    has_bool_literal = False
    for c in expr_binary.children:
        if c.type in ("kEq", "kNeq"):
            has_eq_or_neq = True
        elif c.type in ("kTrue", "kFalse"):
            has_bool_literal = True
    return has_eq_or_neq and has_bool_literal


# ---------------------------------------------------------------------------
# U1 — uses bloat
# ---------------------------------------------------------------------------


def _uses_bloat(root_node) -> SmellHit | None:
    """Sum ``moduleName`` children across every ``declUses`` in the unit."""
    count = 0
    first_line: int | None = None
    for n in _iter_descendants(root_node):
        if n.type != "declUses":
            continue
        if first_line is None:
            first_line = n.start_point[0] + 1
        for c in n.children:
            if c.type == "moduleName":
                count += 1
    if count < _USES_BLOAT_THRESHOLD or first_line is None:
        return None
    return SmellHit(
        rule="U1",
        line=first_line,
        function_key=None,
        detail=f"{count} units in `uses` (>= {_USES_BLOAT_THRESHOLD}) — "
               "consider splitting this unit or moving imports to "
               "implementation section",
    )


# ---------------------------------------------------------------------------
# UV1 — untyped var parameter
# ---------------------------------------------------------------------------


def _is_untyped_var(decl_arg) -> bool:
    """``var X`` with no type annotation."""
    has_var = False
    has_type = False
    for c in decl_arg.children:
        if c.type == "kVar":
            has_var = True
        elif c.type == "type":
            has_type = True
    return has_var and not has_type


# ---------------------------------------------------------------------------
# PP1 — pointer-typed parameter
# ---------------------------------------------------------------------------


def _pointer_param_type_name(decl_arg, source: bytes) -> str | None:
    """Return the printable type name if the parameter is pointer-typed.

    Three flavours match:

    * ``Pointer`` (the base type)
    * Identifier starting with ``P`` followed by an upper-case letter —
      the Win32 ``PChar``/``PByte``/``PWideChar`` family. Ambiguous with
      user-defined ``P``-prefixed types (``PCustomer``) but those are
      themselves usually pointer aliases.
    * ``^Foo`` — explicit pointer type (``typerefPtr``).
    """
    type_node = next(
        (c for c in decl_arg.children if c.type == "type"),
        None,
    )
    if type_node is None:
        return None
    typeref = next(
        (c for c in type_node.children if c.type == "typeref"),
        None,
    )
    if typeref is None:
        return None
    if any(c.type == "typerefPtr" for c in typeref.children):
        return _decode(type_node, source).strip()
    ident = next(
        (c for c in typeref.children if c.type == "identifier"),
        None,
    )
    if ident is None:
        return None
    name = _decode(ident, source)
    if name == "Pointer":
        return name
    if len(name) >= 2 and name[0] == "P" and name[1].isupper():
        return name
    return None


# ---------------------------------------------------------------------------
# C1 — allocator-not-named-Create
# ---------------------------------------------------------------------------


def _allocator_not_named(
    fn_node, source: bytes, key: tuple[int, int]
) -> SmellHit | None:
    """Function body has ``Result := X.Create`` but the name doesn't say so."""
    fn_name = _function_name(fn_node, source)
    if fn_name is None:
        return None
    fn_lower = fn_name.lower()
    if any(tok in fn_lower for tok in _ALLOCATOR_NAMING_TOKENS):
        return None  # name signals ownership

    method = _allocator_method_in_result_assignment(fn_node, source)
    if method is None:
        return None

    return SmellHit(
        rule="C1",
        line=fn_node.start_point[0] + 1,
        function_key=key,
        detail=f"`{fn_name}` returns a newly-`{method}`d object but the "
               "name doesn't include Create/Make/New/etc — caller can't "
               "tell they own the result",
    )


def _allocator_method_in_result_assignment(
    fn_node, source: bytes
) -> str | None:
    """Walk fn_node for ``Result := <chain ending in .Create/.NewInstance>``.

    Both bare (``X.Create``) and called (``X.Create(args)``) shapes are
    covered — we look for any ``exprDot`` in the assignment subtree
    whose final identifier matches an allocator method name.
    """
    for n in _iter_descendants(fn_node):
        if n.type != "assignment":
            continue
        children = n.children
        if not children:
            continue
        lhs = children[0]
        if lhs.type != "identifier":
            continue
        if _decode(lhs, source).lower() != "result":
            continue
        # Scan the assignment's subtree for any exprDot whose last
        # identifier is in the allocator set.
        for sub in _iter_descendants(n):
            if sub.type != "exprDot":
                continue
            ids = [c for c in sub.children if c.type == "identifier"]
            if len(ids) >= 2:
                method = _decode(ids[-1], source)
                if method in _ALLOCATOR_METHODS:
                    return method
    return None


# ---------------------------------------------------------------------------
# Helpers (file-private)
# ---------------------------------------------------------------------------


def _function_name(fn_node, source: bytes) -> str | None:
    """Return the unqualified name (last segment of ``Class.Method``)."""
    decl = next(
        (c for c in fn_node.children if c.type == "declProc"),
        None,
    )
    if decl is None:
        return None
    last_id: str | None = None
    for c in decl.children:
        if c.type == "identifier":
            return _decode(c, source)
        if c.type == "genericDot":
            for sub in c.children:
                if sub.type == "identifier":
                    last_id = _decode(sub, source)
            return last_id
    return last_id


def _decode(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode(
        "utf-8", errors="replace"
    )
