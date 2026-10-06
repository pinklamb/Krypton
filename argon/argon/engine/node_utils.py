"""Small helpers over ast-grep syntax nodes, shared by the engine modules."""

from __future__ import annotations

import re

from ast_grep_py import SgNode


SPACE_RUN = re.compile(r"\s+")


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
