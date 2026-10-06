"""Which children of a node sit one nesting level deeper than it (spec §7.2, §7.3)."""

from __future__ import annotations

from ast_grep_py import SgNode

from argon.engine.branches import COMPREHENSION_TYPES


CONSEQUENCE_NESTING_TYPES = frozenset({"if_statement", "elif_clause", "case_clause"})
BODY_NESTING_TYPES = frozenset(
    {"for_statement", "for_in_statement", "while_statement", "do_statement", "catch_clause"}
)
SWITCH_BODY_TYPES = frozenset({"switch_case", "switch_default"})
TERNARY_TYPES = frozenset({"ternary_expression", "conditional_expression"})
COMPREHENSION_CLAUSES = frozenset({"for_in_clause", "if_clause"})


def child_frames(
    node: SgNode, kind: str, depth: int, control_flow: bool = False
) -> list[tuple[SgNode, int]]:
    """(child, depth) for every direct child of node, in source order."""
    if kind in COMPREHENSION_TYPES:
        return _comprehension_frames(node, depth)
    nested = _nested_children(node, kind, control_flow)
    if not nested:
        return [(child, depth) for child in node.children()]
    return [
        (child, depth + 1 if any(child == body for body in nested) else depth)
        for child in node.children()
    ]


def _comprehension_frames(node: SgNode, depth: int) -> list[tuple[SgNode, int]]:
    """§7.3: score a comprehension like the loop it stands for.

    `[f(x) for a in b if c for x in a]` is `for a in b: if c: for x in a: f(x)`,
    so clause i sits at depth + i and the element is innermost, at depth + n.
    """
    children = node.children()
    innermost = depth + sum(child.kind() in COMPREHENSION_CLAUSES for child in children)
    frames = []
    clause_depth = depth
    for child in children:
        if child.kind() in COMPREHENSION_CLAUSES:
            frames.append((child, clause_depth))
            clause_depth += 1
        else:
            frames.append((child, innermost))
    return frames


def _nested_children(node: SgNode, kind: str, control_flow: bool) -> list[SgNode]:
    """Children of a decision that go one level deeper (a control-flow boolean's right side)."""
    if control_flow:
        return _present([node.field("right")])  # §7.1: the right operand is the body
    nested_for = _NESTED_CHILDREN.get(kind)
    return _present(nested_for(node)) if nested_for is not None else []


def _present(nodes: list[SgNode | None]) -> list[SgNode]:
    """Drop missing optional fields."""
    return [node for node in nodes if node is not None]  # optional fields, e.g. no else


def _ternary_parts(node: SgNode) -> list[SgNode | None]:
    """Both branches of a ternary, except an unparenthesized chained alternative."""
    if node.kind() == "ternary_expression":
        consequence, alternative = node.field("consequence"), node.field("alternative")
    else:
        named = node.named_children()
        consequence, alternative = named[0], named[-1]
    # `a ? b : c ? d : e` is a flat chain, like `else if`; a parenthesized one nests.
    if alternative is not None and alternative.kind() in TERNARY_TYPES:
        return [consequence]
    return [consequence, alternative]


def _blocks(node: SgNode) -> list[SgNode]:
    """Python except_clause has no body field; its block child is the body."""
    return [child for child in node.children() if child.kind() == "block"]


def _switch_body(node: SgNode) -> list[SgNode]:
    """Every child after `value`: the body field holds only the first statement."""
    value = node.field("value")
    named = node.named_children()
    if value is None:  # `default:` has no value
        return named
    return [child for child in named if child != value]


def _else_body(node: SgNode) -> list[SgNode]:
    """The else body nests; an `else if` stays at the chain's level."""
    return [child for child in node.named_children() if child.kind() != "if_statement"]


_NESTED_CHILDREN = {
    **dict.fromkeys(CONSEQUENCE_NESTING_TYPES, lambda node: [node.field("consequence")]),
    **dict.fromkeys(BODY_NESTING_TYPES, lambda node: [node.field("body")]),
    **dict.fromkeys(SWITCH_BODY_TYPES, _switch_body),
    **dict.fromkeys(TERNARY_TYPES, _ternary_parts),
    "except_clause": _blocks,
    "else_clause": _else_body,
}
