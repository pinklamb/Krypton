"""Small helpers over ast-grep syntax nodes, shared by the engine modules."""

from __future__ import annotations

import re

from collections.abc import Iterator

from ast_grep_py import SgNode


SPACE_RUN = re.compile(r"\s+")


def iter_children(node: SgNode, named: bool = False) -> Iterator[SgNode]:
    """Direct children one at a time, by stepping to each next sibling.

    Unlike `children()` it never holds more than one child object, so a node with hundreds of
    thousands of children costs constant memory. Same order and members as `children()`
    (`named_children()` when `named`).
    """
    child = node.child(0)
    while child is not None:
        if child.is_named() or not named:
            yield child
        child = child.next()


def node_text(node: SgNode | None) -> str:
    """A node's source text, or '' for None."""
    return "" if node is None else node.text()


def collapsed(text: str) -> str:
    """Source text on one line: whitespace runs become single spaces."""
    return SPACE_RUN.sub(" ", text).strip()


def parent_kind(node: SgNode) -> str:
    """The parent node's kind, or '' at the root."""
    parent = node.parent()
    return "" if parent is None else parent.kind()
