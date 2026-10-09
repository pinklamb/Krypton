"""What counts as a decision (spec §6) and how much it weighs (§7)."""

from __future__ import annotations

from dataclasses import dataclass

from ast_grep_py import SgNode

from argon.engine.config import EngineConfig
from argon.engine.node_utils import parent_kind


BRANCH_NODE_TYPES = frozenset(
    {
        "if_statement",
        "for_statement",
        "for_in_statement",
        "while_statement",
        "do_statement",
        "switch_case",
        "catch_clause",
        "elif_clause",
        "except_clause",
        "assert_statement",
        "ternary_expression",
        "conditional_expression",
        "for_in_clause",
    }
)
COMPREHENSION_TYPES = frozenset(
    {"list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression"}
)
# Python loop/try `else` blocks are decisions (radon: +1); an `if ... else` is not.
ELSE_BRANCH_PARENTS = frozenset({"for_statement", "while_statement", "try_statement"})
BOOLEAN_OPERATORS = frozenset({"&&", "||", "??", "&&=", "||=", "??=", "and", "or"})
CONTROL_FLOW_OPERATORS = frozenset({"&&", "||", "??", "and", "or"})
BOOLEAN_KINDS = frozenset({"binary_expression", "boolean_operator"})
# Every node kind branch_for() may count; anything else is skipped without a check.
BRANCH_CANDIDATE_TYPES = BRANCH_NODE_TYPES | {
    "case_clause",
    "if_clause",
    "else_clause",
    "binary_expression",
    "augmented_assignment_expression",
    "boolean_operator",
    "call_expression",
}


@dataclass(frozen=True)
class BranchNode:
    """A counted decision, its one-based line, and its local nesting depth (from 0)."""

    node_type: str
    line: int
    operator: str | None = None
    depth: int = 0
    control_flow: bool = False  # §7.1: a boolean whose value is discarded
    end_line: int = 0  # one-based last line of the decision's node, for refactoring hints

    @property
    def weight(self) -> int:
        """Structures cost depth + 1; plain boolean operators a flat 1 (before scope tax)."""
        if self.operator and not self.control_flow:
            return 1
        return self.depth + 1


def branch_for(node: SgNode, kind: str, cfg: EngineConfig) -> BranchNode | None:
    """The decision this node represents, or None. Depth is filled in by the walk."""
    if kind in BRANCH_NODE_TYPES:
        return _branch(node)
    counts = _CONDITIONAL_KINDS.get(kind)
    if counts is not None:
        return _branch(node) if counts(node, cfg) else None
    operator = _operator(node)
    if operator not in BOOLEAN_OPERATORS:
        return None
    control_flow = cfg.control_flow_booleans and is_control_flow_boolean(node)
    return _branch(node, operator, control_flow)


def _counts_case(node: SgNode, cfg: EngineConfig) -> bool:
    """A match / switch case counts unless it is the first unguarded catch-all."""
    return not _is_default_case(node)


def _counts_if_clause(node: SgNode, cfg: EngineConfig) -> bool:
    """An if_clause counts as a comprehension filter, never as a match guard (B10)."""
    # B10: a comprehension filter counts; a match-case guard is the same kind and doesn't.
    return parent_kind(node) in COMPREHENSION_TYPES


def _counts_else(node: SgNode, cfg: EngineConfig) -> bool:
    """A Python else counts on a loop or try, not after an if."""
    return parent_kind(node) in ELSE_BRANCH_PARENTS


def _counts_call(node: SgNode, cfg: EngineConfig) -> bool:
    """An optional call (fn?.()) counts only when count_optional_calls is on (A2)."""
    return cfg.count_optional_calls and _is_optional_call(node)


# Kinds that are a decision only in some positions.
_CONDITIONAL_KINDS = {
    "case_clause": _counts_case,
    "if_clause": _counts_if_clause,
    "else_clause": _counts_else,
    "call_expression": _counts_call,
}


def is_control_flow_boolean(node: SgNode) -> bool:
    """§7.1: `ok && run()` as a statement is an `if` in disguise, not a condition."""
    if node.kind() not in BOOLEAN_KINDS or _operator(node) not in CONTROL_FLOW_OPERATORS:
        return False
    child, parent = node, node.parent()
    while parent is not None:
        kind = parent.kind()
        if kind == "expression_statement":
            return True
        if kind == "parenthesized_expression":
            child, parent = parent, parent.parent()
            continue
        if kind in BOOLEAN_KINDS and parent.field("right") == child:
            # The right operand of a control-flow boolean is its body.
            return is_control_flow_boolean(parent)
        return False
    return False


def _branch(
    node: SgNode, operator: str | None = None, control_flow: bool = False
) -> BranchNode:
    """A BranchNode at the node's 1-based line; the walk fills in the depth."""
    span = node.range()
    return BranchNode(node.kind(), span.start.line + 1, operator, 0, control_flow, span.end.line + 1)


def _operator(node: SgNode) -> str:
    """The node's boolean operator token (&&, or, ??=, ...), or '' if it has none."""
    for child in node.children():
        if not child.is_named() and child.kind() in BOOLEAN_OPERATORS:
            return child.text()
    return ""


def _is_optional_call(node: SgNode) -> bool:
    """A2: `fn?.()` and `a?.b()` run conditionally; a plain `a?.b` is no call."""
    callee = node.field("function")
    direct = any(child.kind() == "optional_chain" for child in node.children())
    return direct or (callee is not None and callee.find(kind="optional_chain") is not None)


def _is_default_case(node: SgNode) -> bool:
    """Only the first catch-all case in a match block is free, as in radon."""
    if not _is_catch_all_case(node):
        return False
    return not any(
        sibling.kind() == "case_clause" and _is_catch_all_case(sibling)
        for sibling in node.prev_all()
    )


def _is_catch_all_case(node: SgNode) -> bool:
    """§6.2: no guard, and a single pattern that is `_` or a bare capture name."""
    if any(child.kind() == "if_clause" for child in node.named_children()):
        return False
    patterns = [child for child in node.named_children() if child.kind() == "case_pattern"]
    # `case x, y:` and `case x,:` are sequence patterns, never catch-alls.
    return len(patterns) == 1 and not _has_comma(node) and _is_catch_all_pattern(patterns[0])


def _has_comma(node: SgNode) -> bool:
    """True for `case x,:` and `(x,)`: sequences, never catch-alls."""
    return any(child.kind() == "," for child in node.children())


def _is_catch_all_pattern(pattern: SgNode) -> bool:
    """`_`, a bare capture name, or either one wrapped in parentheses."""
    if pattern.text() == "_":
        return True
    parts = pattern.named_children()
    if len(parts) != 1:
        return False
    part = parts[0]
    if part.kind() == "dotted_name":
        return "." not in part.text()
    if part.kind() == "tuple_pattern":
        inner = part.named_children()
        return len(inner) == 1 and not _has_comma(part) and _is_catch_all_pattern(inner[0])
    return False
