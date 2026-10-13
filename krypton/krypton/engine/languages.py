"""Rust-backed JavaScript, TypeScript, and Python parsing through ast-grep (spec §5)."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from ast_grep_py import SgNode, SgRoot


LANGUAGES = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".py": "python",
}
SUPPORTED_EXTENSIONS = frozenset(LANGUAGES)


@dataclass(frozen=True)
class ParsedSource:
    """A parsed file: its tree, language, decoded text and syntax-error measures."""

    root: SgNode
    language: str
    text: str  # decoded source; line math uses this
    has_error: bool
    error_coverage: float  # 0.0-1.0 share of characters inside ERROR / MISSING nodes


def extension_of(filename: str) -> str:
    """A path's lowercase extension, with either slash style."""
    return posixpath.splitext(filename.replace("\\", "/"))[1].lower()


def language_for(filename: str) -> str | None:
    """The ast-grep language for a filename, or None if unsupported."""
    return LANGUAGES.get(extension_of(filename))


def decode_source(source: bytes) -> str:
    """C3: never raise on bad bytes; strip a BOM so it can't shift columns."""
    return source.decode("utf-8", errors="replace").removeprefix("\ufeff")


def parse_source(filename: str, source: bytes) -> ParsedSource:
    """Parse bytes, then measure ERROR spans and zero-width MISSING nodes (C1, C2)."""
    language = language_for(filename)
    if language is None:
        raise ValueError(f"unsupported source extension: {extension_of(filename) or '<none>'}")
    text = decode_source(source)
    root = SgRoot(text, language).root()
    error_spans = _merged_spans(root.find_all(kind="ERROR"))
    missing = _missing_nodes(root, error_spans)
    error_chars = sum(end - start for start, end in error_spans)
    coverage = min(1.0, (error_chars + missing) / max(len(text), 1))
    has_error = bool(error_spans) or missing > 0
    return ParsedSource(root, language, text, has_error, coverage)


def _merged_spans(nodes: list[SgNode]) -> list[tuple[int, int]]:
    """ERROR spans with nested errors folded into their outer span."""
    spans = sorted((node.range().start.index, node.range().end.index) for node in nodes)
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _missing_nodes(root: SgNode, error_spans: list[tuple[int, int]]) -> int:
    """C1: tree-sitter marks an invented `)` / `}` with a zero-width node, not ERROR.

    ast-grep-py has no is_missing(); valid code never has an empty-text node
    other than the root of an empty file.
    """
    count = 0
    for node in root.find_all(regex="^$"):
        position = node.range().start.index
        if node != root and not any(start <= position <= end for start, end in error_spans):
            count += 1
    return count
