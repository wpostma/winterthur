"""Per-function metrics walker.

Given a tree-sitter parse tree, descends into each function-shaped subtree
and counts decision points, exits, loops, etc. Output keyed by function
line-range so the metrics command can zip results onto :class:`Symbol`
records emitted by :mod:`winterthur.parser`.

The walker is language-aware via :data:`NODE_KINDS_BY_LANGUAGE` — node-type
names differ between grammars. New languages = one entry in that table.
The codereview metrics-tool-spec field names are the source of truth;
the walker fills as many of them as it can per language.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# Tree-sitter node-type sets keyed by metric category, then by language.
# Empty set = "this language doesn't have this construct."
NODE_KINDS_BY_LANGUAGE: dict[str, dict[str, frozenset[str]]] = {
    "pascal": {
        "function_def": frozenset({"defProc"}),
        # Tree-sitter-pascal splits if-without-else and if-with-else into
        # two node types — both are ONE decision point each.
        "if": frozenset({"if", "ifElse"}),
        "case": frozenset({"case"}),
        "case_arm": frozenset({"caseLabel", "caseCase"}),
        "loop": frozenset({"while", "for", "foreach", "repeat"}),
        "try": frozenset({"try"}),
        "except": frozenset({"exceptionHandler"}),
        "finally": frozenset({"kFinally"}),
        "raise": frozenset({"raise"}),
        # exit / break / continue are NOT keywords in Pascal — they're regular
        # procedure-call identifiers. Match them by name in _walk's exprCall
        # special case below, not by node type. Empty set here on purpose.
        "exit": frozenset(),
        "break": frozenset(),
        "continue": frozenset(),
        "boolean_op": frozenset({"kAnd", "kOr", "kXor"}),
        "assignment": frozenset({"assignment"}),
        "anon_proc": frozenset({"anonymousMethod", "lambda"}),  # tolerate either spelling
        "formal_param": frozenset({"declArg"}),
        "local_var_decl": frozenset({"declVars", "declVar"}),
        "block": frozenset({"block"}),
    },
    # Other languages can be filled in later. Walker tolerates a missing entry
    # by emitting all-zero metrics rather than failing.
}


@dataclass
class FunctionMetrics:
    """Counts collected by walking one function subtree.

    Field names match :file:`~/.claude/skills/codereview/metrics-tool-spec.md`
    so they can be merged into the metrics command's JSON record without
    renaming.

    All counter fields default to ``None`` (not zero). The walker increments
    via ``_bump`` which lifts ``None`` to ``1``. This lets JSON serialization
    drop fields that were never observed, keeping output terse for LLM
    consumers — a function with no try/except shouldn't waste tokens
    reporting ``"try_count": 0, "except_count": 0, "finally_count": 0``.
    """

    # Decision points always include the function entry, so this stays at 1
    # even when nothing else is found. Always serialised.
    decision_points: int = 1

    if_count: int | None = None
    case_count: int | None = None
    case_arms: int | None = None
    loop_count: int | None = None
    try_count: int | None = None
    except_count: int | None = None
    finally_count: int | None = None
    raise_count: int | None = None
    exit_count: int | None = None
    break_count: int | None = None
    continue_count: int | None = None
    boolean_op_count: int | None = None
    result_assign_count: int | None = None
    anon_proc_count: int | None = None
    max_anon_proc_depth: int | None = None
    max_nesting_depth: int | None = None
    param_count: int | None = None
    local_var_count: int | None = None
    logger_call_count: int | None = None

    params: list[str] = field(default_factory=list)


def _bump(value: int | None, by: int = 1) -> int:
    """Increment ``value`` by ``by``, treating ``None`` as 0."""
    return (value or 0) + by


# Block-like node types contribute to nesting depth (per language). Pascal:
# anything that opens a `begin…end` or a control structure body.
_NESTING_NODES_PASCAL = frozenset({
    "block", "if", "case", "while", "for", "foreach", "repeat",
    "try", "exceptionHandler", "anonymousMethod",
})


def validate_structure(root_node, source: bytes, language: str) -> list[str]:
    """Return human-readable structural errors for *root_node*.

    For Pascal: surfaces tree-sitter parse errors, begin/end token imbalance,
    and a missing ``end.`` unit terminator. Returns ``[]`` for healthy files
    and for languages we don't yet validate.
    """
    if language != "pascal":
        return []

    errors: list[str] = []

    # 1. Tree-sitter says the parse contains structural errors. The grammar
    #    wraps unrecoverable input in an ERROR node; root.has_error captures
    #    every variant including missing-token recovery.
    if root_node.has_error:
        # Find the first ERROR node so we can point at a line.
        err_line = _first_error_line(root_node)
        if err_line:
            errors.append(f"parse error starting at line {err_line}")
        else:
            errors.append("parse error (tree-sitter could not fully parse this unit)")

    # 2. begin/end token imbalance. In a well-formed unit kEnd >= kBegin
    #    (extra kEnds close class/record declarations and the unit itself).
    #    kEnd < kBegin always means a missing end.
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
            f"begin/end mismatch: {n_begin} 'begin' but only {n_end} 'end' "
            f"(missing {n_begin - n_end})"
        )

    # 3. Unit terminator. Pascal's grammar emits a kEndDot node for the
    #    final ``end.``. Absence of any kEndDot in the tree means the file
    #    never closed its unit/program.
    if not has_kEndDot:
        # Don't double-report if we already noted a parse error — but do
        # call out the specific missing-terminator condition; it's the
        # single most common author mistake when truncating output.
        errors.append("missing 'end.' unit terminator")

    return errors


def _first_error_line(root_node) -> int | None:
    for n in _iter_descendants(root_node):
        if n.type == "ERROR":
            return n.start_point[0] + 1
    return None


def collect_function_metrics(
    root_node, source: bytes, language: str
) -> dict[tuple[int, int], FunctionMetrics]:
    """Walk *root_node* and return metrics keyed by ``(start_line, end_line)``.

    Lines are 1-indexed (matching :class:`Symbol`).
    Returns an empty dict for languages without a NODE_KINDS_BY_LANGUAGE entry.
    """
    kinds = NODE_KINDS_BY_LANGUAGE.get(language)
    if kinds is None:
        return {}

    out: dict[tuple[int, int], FunctionMetrics] = {}
    fn_kinds = kinds["function_def"]
    for fn_node in _iter_descendants(root_node):
        if fn_node.type not in fn_kinds:
            continue
        metrics = FunctionMetrics()
        _walk(fn_node, kinds, metrics, depth=0, anon_depth=0, language=language)
        _populate_function_signature(fn_node, source, metrics)
        # Pascal: count `Result := X` AND `FuncName := X` as result_assign
        _count_result_assignments(fn_node, source, kinds, metrics)
        key = (fn_node.start_point[0] + 1, fn_node.end_point[0] + 1)
        out[key] = metrics
    return out


def _iter_descendants(node) -> Iterable:
    """Pre-order traversal generator."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        # push children in reverse so we descend left-to-right
        stack.extend(reversed(n.children))


_PASCAL_CONTROL_FLOW_CALLS = frozenset({"exit", "break", "continue"})


def _pascal_control_flow_name(node) -> str | None:
    """Return ``'exit'`` / ``'break'`` / ``'continue'`` if *node* is one.

    Pascal's tree-sitter grammar treats Exit/Break/Continue as ordinary
    identifiers, so we have to recognise them in two shapes:

    1. ``Exit;`` — the grammar emits ``statement → identifier ;``. The bare
       identifier IS the call. This is the common case.
    2. ``Exit(Result);`` — emits ``statement → exprCall(Exit, exprArgs(...))``.
       Here the call is wrapped in an exprCall node.

    Detection at the ``statement`` level catches both: peek at the first
    non-trivia child, normalise to the head identifier text, and match
    case-insensitively against the three control-flow names.

    Returns ``None`` if *node* isn't a control-flow statement.
    """
    if node.type != "statement" or not node.children:
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
    if name in _PASCAL_CONTROL_FLOW_CALLS:
        return name
    return None


def _walk(
    node, kinds: dict[str, frozenset[str]], m: FunctionMetrics,
    depth: int, anon_depth: int, language: str,
) -> None:
    """Recursive counting walk. ``m`` is mutated in place."""
    t = node.type

    # Pascal-only: Exit/Break/Continue can appear as `statement → identifier`
    # (bare, common case) or as `statement → exprCall` (with a return value).
    # _pascal_control_flow_name handles both.
    if language == "pascal" and t == "statement":
        cf = _pascal_control_flow_name(node)
        if cf == "exit":
            m.exit_count = _bump(m.exit_count)
        elif cf == "break":
            m.break_count = _bump(m.break_count)
        elif cf == "continue":
            m.continue_count = _bump(m.continue_count)

    if t in kinds["if"]:
        m.if_count = _bump(m.if_count)
        m.decision_points += 1
    elif t in kinds["case"]:
        m.case_count = _bump(m.case_count)
        # case decision contribution is per-arm, counted separately
    elif t in kinds["case_arm"]:
        m.case_arms = _bump(m.case_arms)
        m.decision_points += 1
    elif t in kinds["loop"]:
        m.loop_count = _bump(m.loop_count)
        m.decision_points += 1
    elif t in kinds["try"]:
        m.try_count = _bump(m.try_count)
    elif t in kinds["except"]:
        m.except_count = _bump(m.except_count)
        m.decision_points += 1
    elif t in kinds["finally"]:
        m.finally_count = _bump(m.finally_count)
    elif t in kinds["raise"]:
        m.raise_count = _bump(m.raise_count)
    elif t in kinds["exit"]:
        m.exit_count = _bump(m.exit_count)
    elif t in kinds["break"]:
        m.break_count = _bump(m.break_count)
    elif t in kinds["continue"]:
        m.continue_count = _bump(m.continue_count)
    elif t in kinds["boolean_op"]:
        m.boolean_op_count = _bump(m.boolean_op_count)
        m.decision_points += 1
    elif t in kinds["anon_proc"]:
        m.anon_proc_count = _bump(m.anon_proc_count)
        anon_depth += 1
        if (m.max_anon_proc_depth or 0) < anon_depth:
            m.max_anon_proc_depth = anon_depth

    nesting_set = _NESTING_NODES_PASCAL if language == "pascal" else frozenset()
    bumped = False
    if t in nesting_set:
        depth += 1
        bumped = True
        if (m.max_nesting_depth or 0) < depth:
            m.max_nesting_depth = depth

    for c in node.children:
        _walk(c, kinds, m, depth, anon_depth, language)

    if bumped:
        depth -= 1  # noqa: F841  (tracked for clarity; recursion already restored)


def _first_identifier_name(node) -> str | None:
    """Return the source text of the first descendant identifier, or None.

    Used for matching call-site names like ``Exit``/``Break``/``Continue``
    where the grammar represents them as ordinary exprCall identifiers.
    The exprCall's first child is the callee (identifier or exprDot); we
    only treat bare identifiers as control-flow candidates so we don't
    misclassify ``Self.Exit`` or ``Object.Break`` as control flow.
    """
    if not node.children:
        return None
    head = node.children[0]
    if head.type != "identifier":
        return None
    return head.text.decode("utf-8", errors="replace") if head.text else None


def _populate_function_signature(fn_node, source: bytes, m: FunctionMetrics) -> None:
    """Count formal parameters and capture their names.

    Pascal packs comma-separated parameters of the same type into a single
    ``declArg`` node — ``(A, B, C: Integer; D: string)`` produces TWO declArg
    nodes (one for the integers, one for the string), but the integers'
    declArg holds 3 identifier children before its ``:``. We count
    identifiers up to the first ``:`` so each comma-separated name counts.
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
                break  # everything after `:` is the type, not a param name
            if c.type != "identifier":
                continue
            m.param_count = _bump(m.param_count)
            m.params.append(
                source[c.start_byte:c.end_byte].decode("utf-8", errors="replace")
            )


def _count_result_assignments(
    fn_node, source: bytes, kinds: dict[str, frozenset[str]], m: FunctionMetrics,
) -> None:
    """Pascal: count `Result := X` and `<FuncName> := X` assignments.

    The function name is the last identifier under the function's
    ``genericDot`` (qualified) or the first identifier child of declProc
    (unqualified).
    """
    fn_name = _extract_function_name(fn_node, source)
    targets = {"result"}
    if fn_name:
        targets.add(fn_name.lower())

    for n in _iter_descendants(fn_node):
        if n.type not in kinds["assignment"]:
            continue
        # First child of `assignment` is the LHS. For `Result := X` it's an
        # identifier; for `Self.Field := X` it's an exprDot — we only care
        # about the bare-identifier case for Result/FuncName tracking.
        if not n.children:
            continue
        lhs = n.children[0]
        if lhs.type != "identifier":
            continue
        text = source[lhs.start_byte:lhs.end_byte].decode("utf-8", errors="replace")
        if text.lower() in targets:
            m.result_assign_count = _bump(m.result_assign_count)


def _extract_function_name(fn_node, source: bytes) -> str | None:
    """Return the unqualified function name (last segment of `Class.Method`)."""
    decl = next((c for c in fn_node.children if c.type == "declProc"), None)
    if decl is None:
        return None
    last_id: str | None = None
    for c in decl.children:
        if c.type == "identifier":
            return source[c.start_byte:c.end_byte].decode("utf-8", errors="replace")
        if c.type == "genericDot":
            for sub in c.children:
                if sub.type == "identifier":
                    last_id = source[sub.start_byte:sub.end_byte].decode(
                        "utf-8", errors="replace"
                    )
            if last_id is not None:
                return last_id
    return None
