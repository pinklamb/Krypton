"""Functions the gate should not block on, and why.

A function can be exempt for three reasons, each recorded on the finding so a report always says what was waved through:
a waiver comment (`# krypton: ignore -- reason` or `// krypton: ignore -- reason`), a run that showed it is no worse than
before (`--no-worse`), or a hand-off after it stalled (`--handoff-stalled`). Exempt functions keep their numbers in the
report but do not count toward flags or the purity score, and they get no refactoring hint.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from krypton.schemas import FunctionFinding

WAIVER = re.compile(r"(?:#|//|/\*)\s*krypton:\s*ignore\b(?P<rest>[^\n]*)")
COMMENT_STARTS = ("#", "//", "/*")
Exemptions = Mapping[tuple[str, str], str]


def waiver_reason(lines: Sequence[str], start_line: int) -> str | None:
    """The reason on a waiver at the function's first line or in a comment line just above it ('' if none was given), else None."""
    own = _waiver_text(lines, start_line, whole_line=False)
    return own if own is not None else _waiver_text(lines, start_line - 1, whole_line=True)


def _waiver_text(lines: Sequence[str], number: int, whole_line: bool) -> str | None:
    """The waiver reason on one line (a line above counts only if it is a comment by itself), else None."""
    if not 1 <= number <= len(lines):
        return None
    line = lines[number - 1]
    if whole_line and not line.lstrip().startswith(COMMENT_STARTS):
        return None
    match = WAIVER.search(line)
    return None if match is None else match.group("rest").strip().removesuffix("*/").strip().lstrip("-:").strip()


def exempt_reason(function: FunctionFinding, filename: str, lines: Sequence[str], given: Exemptions | None) -> str | None:
    """Why the gate ignores this function, or None. A reason supplied by the caller wins over a waiver comment."""
    if given and (filename, function.name) in given:
        return given[(filename, function.name)]
    waiver = waiver_reason(lines, function.start_line)
    if waiver is None:
        return None
    return f"waived: {waiver}" if waiver else "waived (no reason given)"


def mark_exempt(functions: list[FunctionFinding], filename: str, lines: Sequence[str], given: Exemptions | None) -> list[FunctionFinding]:
    """The same functions, with `exempt_reason` set on the ones the gate ignores."""
    marked = []
    for function in functions:
        reason = exempt_reason(function, filename, lines, given)
        marked.append(function if reason is None else function.model_copy(update={"exempt_reason": reason}))
    return marked
