"""Effective lines (spec §9.4 as amended Oct 2026): packing code no longer shrinks a file.

An effective line (any character outside whitespace and comments) weighs
max(1, statements starting on it, ceil(chars / width)), with width 40.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from itertools import accumulate
from math import ceil

# (start_line, start_column, end_line, end_column), 0-based, end exclusive
Span = tuple[int, int, int, int]


def physical_lines(text: str) -> list[str]:
    """Newline-separated lines; a final newline does not start another line."""
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


@dataclass(frozen=True)
class LineWeights:
    """Per-line effective weight with prefix sums, for any line range or altered subset."""

    weights: tuple[int, ...]  # index 0 is line 1
    prefix: tuple[int, ...]  # prefix[i] = sum of weights[:i]
    starts: tuple[int, ...] = ()  # character offset where each line begins
    code: tuple[str, ...] = ()  # each line with its comments blanked out
    code_chars: dict[int, int] = field(default_factory=dict, compare=False, repr=False)  # cache

    @classmethod
    def build(
        cls,
        text: str,
        comment_spans: list[Span],
        width: int,
        statements: dict[int, int] | None = None,
    ) -> "LineWeights":
        """statements maps a 0-based line to the number of statements starting on it."""
        statements = statements or {}
        raw = text.split("\n")
        if text.endswith("\n"):
            raw.pop()
        lines = [line.removesuffix("\r") for line in raw]
        code_only = _blank_out(lines, comment_spans)
        weights = tuple(
            max(1, statements.get(index, 0), ceil(len(line.strip()) / width)) if code.strip() else 0
            for index, (line, code) in enumerate(zip(lines, code_only))
        )
        starts = tuple(accumulate((len(line) + 1 for line in raw), initial=0))[: len(raw)]
        return cls(weights, (0, *accumulate(weights)), starts, tuple(code_only))

    @property
    def total(self) -> int:
        """Effective lines of the whole file."""
        return self.prefix[-1]

    def range_total(self, start_line: int, end_line: int) -> int:
        """Sum over 1-based lines start_line..end_line inclusive."""
        start = max(start_line, 1) - 1
        end = min(end_line, len(self.weights))
        return self.prefix[end] - self.prefix[start] if end > start else 0

    def altered_total(self, start_line: int, end_line: int, altered: frozenset[int] | None) -> int:
        """Effective lines in a range, counting only altered lines when a set is given."""
        if altered is None:
            return self.range_total(start_line, end_line)
        return sum(self.weights[line - 1] for line in altered if start_line <= line <= end_line and line <= len(self.weights))
    def own_total(self, spans: tuple[tuple[int, int], ...], altered: frozenset[int] | None = None) -> float:
        """Effective lines owned by character spans (start, end, end exclusive).

        A line wholly inside a span counts in full. A line the span only partly covers (several
        functions sharing it, or code after the span on the same line) counts by the share of
        its code characters the span holds, so functions on one line split that line's weight
        instead of each taking all of it, or none.
        """
        return sum(self._span_total(start, end, altered) for start, end in spans)

    def _span_total(self, start: int, end: int, altered: frozenset[int] | None) -> float:
        """One span: its partial first and last lines plus the whole lines between."""
        if end <= start or not self.weights:
            return 0.0
        first, last = self._line_at(start), self._line_at(end - 1)
        if first == last:
            return self._partial(first, start, end, altered)
        return self._partial(first, start, end, altered) + self._whole(first + 1, last, altered) + self._partial(last, start, end, altered)

    def _line_at(self, offset: int) -> int:
        """The 0-based line holding a character offset."""
        return min(max(bisect_right(self.starts, offset) - 1, 0), len(self.weights) - 1)

    def _whole(self, first: int, stop: int, altered: frozenset[int] | None) -> int:
        """Weights of lines first..stop-1 (0-based), counting only altered lines if a set is given."""
        if altered is None:
            return self.prefix[stop] - self.prefix[first]
        return sum(self.weights[number - 1] for number in altered if first < number <= stop and number <= len(self.weights))

    def _partial(self, index: int, start: int, end: int, altered: frozenset[int] | None) -> float:
        """One boundary line's weight times the share of its code characters inside [start, end)."""
        weight = self.weights[index]
        if weight == 0 or (altered is not None and index + 1 not in altered):
            return 0.0
        low = max(start - self.starts[index], 0)
        inside = sum(1 for char in self.code[index][low : end - self.starts[index]] if not char.isspace())
        return weight * inside / self._code_chars(index)

    def _code_chars(self, index: int) -> int:
        """Non-space code characters on a 0-based line (cached)."""
        if index not in self.code_chars:
            self.code_chars[index] = sum(1 for char in self.code[index] if not char.isspace())
        return self.code_chars[index]


def non_blank_weights(text: str, width: int) -> LineWeights:
    """Weights without a syntax tree (oversize / unparseable files): comments still count."""
    return LineWeights.build(text, [], width)


def _blank_out(lines: list[str], spans: list[Span]) -> list[str]:
    """The lines with every comment character replaced by a space."""
    code = list(lines)
    for span in spans:
        for index in range(span[0], min(span[2], len(code) - 1) + 1):
            code[index] = _blank_columns(code[index], index, span)
    return code


def _blank_columns(line: str, index: int, span: Span) -> str:
    """The part of one line that a (possibly multi-line) comment span covers, as spaces."""
    start_line, start_col, end_line, end_col = span
    left = start_col if index == start_line else 0
    right = end_col if index == end_line else len(line)
    return line[:left] + " " * max(right - left, 0) + line[right:]
