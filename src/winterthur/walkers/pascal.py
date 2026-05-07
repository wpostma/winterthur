"""Pascal language walker.

Encodes the tree-sitter-pascal node-type names for each metric category
plus the Pascal-specific bits that don't reduce to a node-type match:
identifier-call ``Exit;`` / ``Break;`` / ``Continue;``, ``declArg``
parameter group expansion, and ``Result := X`` / ``<FuncName> := X``
tracking. Begin/end balance and ``end.`` terminator validation also
live here.
"""

from __future__ import annotations

from typing import ClassVar

from .base import (
    FunctionMetrics,
    LanguageWalker,
    WalkContext,
    _bump,
    _iter_descendants,
)


_CONTROL_FLOW_CALLS = frozenset({"exit", "break", "continue"})


class PascalWalker(LanguageWalker):
    language: ClassVar[str] = "pascal"

    function_node_types: ClassVar[frozenset[str]] = frozenset({"defProc"})

    decision_kinds: ClassVar[dict[str, frozenset[str]]] = {
        # Tree-sitter-pascal splits if-without-else and if-with-else into
        # two node types — both contribute ONE decision point.
        "if": frozenset({"if", "ifElse"}),
        "case": frozenset({"case"}),
        # caseCase wraps the whole arm; caseLabel is its `1:` child. We
        # count caseCase only — counting both double-counts every arm.
        "case_arm": frozenset({"caseCase"}),
        "loop": frozenset({"while", "for", "foreach", "repeat"}),
        "try": frozenset({"try"}),
        "except": frozenset({"exceptionHandler"}),
        "finally": frozenset({"kFinally"}),
        "raise": frozenset({"raise"}),
        # Exit/Break/Continue are NOT keywords in Pascal — they're regular
        # procedure-call identifiers. Match them by name in pre_visit
        # below, not by node type. Empty sets here on purpose.
        "exit": frozenset(),
        "break": frozenset(),
        "continue": frozenset(),
        "boolean_op": frozenset({"kAnd", "kOr", "kXor"}),
        # Tolerate either spelling — older grammars used "lambda".
        "anon_proc": frozenset({"anonymousMethod", "lambda"}),
    }

    # Block-like constructs that contribute to nesting depth. Any node
    # that opens a `begin…end` or a control-structure body belongs here.
    nesting_node_types: ClassVar[frozenset[str]] = frozenset({
        "block", "if", "case", "while", "for", "foreach", "repeat",
        "try", "exceptionHandler", "anonymousMethod",
    })

    # ------------------------------------------------------------------
    # Hook overrides
    # ------------------------------------------------------------------

    def pre_visit(self, node, ctx: WalkContext) -> None:
        # Pascal: Exit/Break/Continue can appear as `statement → identifier`
        # (bare, common case) or as `statement → exprCall` (with a return
        # value, e.g. `Exit(Result)`). Detection at the statement level
        # catches both.
        if node.type != "statement":
            return
        cf = _control_flow_name(node)
        if cf == "exit":
            ctx.metrics.exit_count = _bump(ctx.metrics.exit_count)
        elif cf == "break":
            ctx.metrics.break_count = _bump(ctx.metrics.break_count)
        elif cf == "continue":
            ctx.metrics.continue_count = _bump(ctx.metrics.continue_count)

    def extract_signature(
        self, fn_node, source: bytes, m: FunctionMetrics
    ) -> None:
        """Count formal parameters and capture their names.

        Pascal packs comma-separated parameters of the same type into a
        single ``declArg`` node — ``(A, B, C: Integer; D: string)``
        produces TWO declArg nodes (one for the integers, one for the
        string), but the integers' declArg holds 3 identifier children
        before its ``:``. Count identifiers up to the first ``:`` so
        each comma-separated name counts.
        """
        decl = next(
            (c for c in fn_node.children if c.type == "declProc"),
            None,
        )
        if decl is None:
            return
        for n in _iter_descendants(decl):
            if n.type != "declArg":
                continue
            for c in n.children:
                if c.type == ":":
                    break  # everything after `:` is the type, not a param
                if c.type != "identifier":
                    continue
                m.param_count = _bump(m.param_count)
                m.params.append(
                    source[c.start_byte:c.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                )

    def count_result_assignments(
        self, fn_node, source: bytes, m: FunctionMetrics
    ) -> None:
        """Count ``Result := X`` and ``<FuncName> := X`` assignments.

        The function name is the last identifier under the function's
        ``genericDot`` (qualified) or the first identifier child of
        ``declProc`` (unqualified).
        """
        fn_name = _function_name(fn_node, source)
        targets = {"result"}
        if fn_name:
            targets.add(fn_name.lower())

        for n in _iter_descendants(fn_node):
            if n.type != "assignment":
                continue
            # First child of `assignment` is the LHS. For `Result := X`
            # it's an identifier; for `Self.Field := X` it's an exprDot
            # — only the bare-identifier case can be Result/FuncName.
            if not n.children:
                continue
            lhs = n.children[0]
            if lhs.type != "identifier":
                continue
            text = source[lhs.start_byte:lhs.end_byte].decode(
                "utf-8", errors="replace"
            )
            if text.lower() in targets:
                m.result_assign_count = _bump(m.result_assign_count)

    def validate_structure(self, root_node, source: bytes) -> list[str]:
        """Surface tree-sitter parse errors, begin/end token imbalance,
        and a missing ``end.`` unit terminator.
        """
        errors: list[str] = []

        # 1. Tree-sitter says the parse contains structural errors.
        if root_node.has_error:
            err_line = _first_error_line(root_node)
            if err_line:
                errors.append(f"parse error starting at line {err_line}")
            else:
                errors.append(
                    "parse error (tree-sitter could not fully parse this unit)"
                )

        # 2. begin/end token imbalance. In a well-formed unit
        #    kEnd >= kBegin (extra kEnds close class/record declarations
        #    and the unit itself). kEnd < kBegin always means a missing
        #    end.
        n_begin = 0
        n_end = 0
        has_kEndDot = False
        for n in _iter_descendants(root_node):
            if n.type == "kBegin":
                n_begin += 1
            elif n.type == "kEnd":
                n_end += 1
            elif n.type == "kEndDot":
                has_kEndDot = True
        if n_end < n_begin:
            errors.append(
                f"begin/end mismatch: {n_begin} 'begin' but only {n_end} "
                f"'end' (missing {n_begin - n_end})"
            )

        # 3. Unit terminator. Pascal's grammar emits a kEndDot node for
        #    the final ``end.``. Absence of any kEndDot in the tree
        #    means the file never closed its unit/program.
        if not has_kEndDot:
            errors.append("missing 'end.' unit terminator")

        return errors


# ---------------------------------------------------------------------------
# Helpers (file-private)
# ---------------------------------------------------------------------------


def _control_flow_name(node) -> str | None:
    """Return ``'exit'`` / ``'break'`` / ``'continue'`` if *node* is one.

    Pascal's tree-sitter grammar treats Exit/Break/Continue as ordinary
    identifiers, so we have to recognise them in two shapes:

    1. ``Exit;`` — ``statement → identifier ;``. The bare identifier IS
       the call. This is the common case.
    2. ``Exit(Result);`` — ``statement → exprCall(Exit, exprArgs(...))``.
       Here the call is wrapped in an exprCall node.

    Returns ``None`` if *node* isn't a control-flow statement. Bare
    identifiers only — ``Self.Exit`` (which is an exprDot head) is
    deliberately not matched.
    """
    if not node.children:
        return None
    head = node.children[0]
    if head.type == "identifier":
        if not head.text:
            return None
        name = head.text.decode("utf-8", errors="replace").lower()
    elif head.type == "exprCall":
        name = _first_identifier_name(head)
        if name is None:
            return None
        name = name.lower()
    else:
        return None
    return name if name in _CONTROL_FLOW_CALLS else None


def _first_identifier_name(node) -> str | None:
    """Return the source text of the first descendant identifier."""
    if not node.children:
        return None
    head = node.children[0]
    if head.type != "identifier":
        return None
    if not head.text:
        return None
    return head.text.decode("utf-8", errors="replace")


def _function_name(fn_node, source: bytes) -> str | None:
    """Return the unqualified function name (last segment of ``Class.Method``)."""
    decl = next((c for c in fn_node.children if c.type == "declProc"), None)
    if decl is None:
        return None
    last_id: str | None = None
    for c in decl.children:
        if c.type == "identifier":
            return source[c.start_byte:c.end_byte].decode(
                "utf-8", errors="replace"
            )
        if c.type == "genericDot":
            for sub in c.children:
                if sub.type == "identifier":
                    last_id = source[sub.start_byte:sub.end_byte].decode(
                        "utf-8", errors="replace"
                    )
            if last_id is not None:
                return last_id
    return None


def _first_error_line(root_node) -> int | None:
    for n in _iter_descendants(root_node):
        if n.type == "ERROR":
            return n.start_point[0] + 1
    return None
